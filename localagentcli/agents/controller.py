"""High-level agent mode orchestration and session integration."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Generator, Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from localagentcli.agents.events import (
    AgentEvent,
    PhaseChanged,
    PlanGenerated,
    StepStarted,
    TaskComplete,
    TaskFailed,
    TaskRouted,
    TaskStopped,
    TaskTimedOut,
    ToolCallRequested,
    ToolCallResult,
)
from localagentcli.agents.loop import AgentLoop
from localagentcli.agents.planner import PlanStep, TaskPlan, TaskPlanner
from localagentcli.agents.triage import TaskTriage, TaskTriageClassifier
from localagentcli.models.abstraction import ModelAbstractionLayer
from localagentcli.models.backends.base import ModelMessage, StreamChunk
from localagentcli.safety.approval import ApprovalManager
from localagentcli.safety.boundary import WorkspaceBoundary
from localagentcli.safety.layer import SafetyLayer
from localagentcli.safety.rollback import RollbackManager
from localagentcli.session.compactor import ContextCompactor
from localagentcli.session.instructions import (
    build_conversation_model_messages,
    build_instruction_messages,
    build_system_instructions,
)
from localagentcli.session.state import Message, Session
from localagentcli.tools.registry import ToolRegistry
from localagentcli.tools.router import ToolRouter


@dataclass
class AgentDispatch:
    """Prepared execution path for one agent-mode plain-text input."""

    triage: TaskTriage
    events: Iterator[AgentEvent] | AsyncIterator[AgentEvent] | None = None
    stream: Iterator[StreamChunk] | AsyncIterator[StreamChunk] | None = None


_UNCHANGED = object()


class AgentController:
    """Drive agent tasks and persist state to the active session."""

    def __init__(
        self,
        model: ModelAbstractionLayer,
        session: Session,
        tool_registry: ToolRegistry | ToolRouter,
        approval: ApprovalManager | None = None,
        safety: SafetyLayer | None = None,
        rollback_storage: Path | None = None,
        context_limit: int = 8192,
        generation_config: dict[str, object] | None = None,
        inactivity_timeout: int | None = None,
        on_session_mutated: Callable[[], None] | None = None,
    ):
        self._model = model
        self._session = session
        self._tools = tool_registry
        self._approval = approval or ApprovalManager()
        workspace_root = Path(session.workspace).expanduser().resolve()
        self._safety = safety or SafetyLayer(
            self._approval,
            WorkspaceBoundary(workspace_root),
            RollbackManager(
                session.id,
                rollback_storage or (workspace_root / ".localagent_cache"),
            ),
        )
        self._planner = TaskPlanner(model)
        self._loop = AgentLoop(model, tool_registry, self._planner, self._safety)
        self._triage = TaskTriageClassifier(model)
        self._compactor = ContextCompactor(model, context_limit)
        self._generation_config = generation_config or {}
        self._inactivity_timeout_value = inactivity_timeout
        self._generator: Generator[AgentEvent, bool, None] | None = None
        self._pending_tool: ToolCallRequested | None = None
        self._last_compaction_count = 0
        self._last_triage: TaskTriage | None = None
        self._direct_stream_active = False
        self._async_agent_active = False
        self._on_session_mutated = on_session_mutated

    def _notify_autosave(self) -> None:
        if self._on_session_mutated is not None:
            self._on_session_mutated()

    @property
    def has_active_task(self) -> bool:
        """Whether an agent task is currently running or paused for approval."""
        return (
            self._generator is not None
            or self._pending_tool is not None
            or self._direct_stream_active
            or self._async_agent_active
        )

    @property
    def has_pending_approval(self) -> bool:
        """Whether a tool call is waiting for approval."""
        return self._pending_tool is not None

    @property
    def pending_tool(self) -> ToolCallRequested | None:
        """The current pending tool call, if any."""
        return self._pending_tool

    @property
    def approval_mode(self) -> str:
        """Current task-scoped approval mode."""
        return self._approval.mode

    @property
    def task_state(self) -> dict[str, object]:
        """Current or last recorded agent task state for the session."""
        state = self._session.metadata.get("agent_task_state", {})
        if isinstance(state, dict):
            return dict(state)
        return {}

    @property
    def rollback_count(self) -> int:
        """Number of rollback entries currently available for the session."""
        return len(self._safety.rollback.get_history())

    @property
    def last_compaction_count(self) -> int:
        """Messages compacted before the most recent task started."""
        return self._last_compaction_count

    def handle_task(self, task_input: str) -> Iterator[AgentEvent] | AsyncIterator[AgentEvent]:
        """Start a new task and stream its events."""
        dispatch = self.dispatch_input(task_input)
        if dispatch.events is None:
            raise RuntimeError("The task was routed through the direct-answer fast path.")
        return dispatch.events

    def dispatch_input(self, task_input: str) -> AgentDispatch:
        """Append input, compact, triage it, and return the correct execution path."""
        if self.has_active_task:
            raise RuntimeError("An agent task is already running.")

        self._append_user_input(task_input)
        context = build_conversation_model_messages(self._session)
        triage = self._triage.classify(
            task_input,
            context,
            generation_options=self._profile("triage"),
        )
        self._last_triage = triage
        self._session.metadata["last_triage"] = {
            "outcome": triage.outcome,
            "reason": triage.reason,
            "timestamp": datetime.now().isoformat(),
        }
        if triage.outcome == "direct_answer":
            self._update_task_state(
                route=triage.outcome,
                phase="executing",
                step_index=None,
                step_description=None,
                pending_tool=None,
                summary="Answering directly without tool use.",
                active=True,
            )
        else:
            self._update_task_state(
                route=triage.outcome,
                phase="planning",
                step_index=None,
                step_description=None,
                pending_tool=None,
                summary="Preparing agent execution.",
                active=True,
            )
        self._session.touch()

        if triage.outcome == "direct_answer":
            self._direct_stream_active = True
            return AgentDispatch(
                triage=triage,
                stream=self._stream_direct_answer(context, self._profile("direct")),
            )

        supports_tools = getattr(self._model, "supports_tools", lambda: True)
        if not bool(supports_tools()):
            self._update_task_state(
                phase="failed",
                summary=(
                    "The active model/provider cannot execute tools in agent mode. "
                    "Switch targets or ask a simple direct question instead."
                ),
                active=False,
            )
            raise RuntimeError(
                "The active model/provider cannot execute tools in agent mode. "
                "Switch targets or ask a simple direct question instead."
            )

        self._session.metadata["approval_mode"] = self._approval.mode
        plan = (
            TaskPlan(
                task=task_input,
                steps=[PlanStep(index=1, description=task_input)],
                status="planning",
            )
            if triage.outcome == "single_step_task"
            else None
        )
        self._generator = self._loop.run(
            task_input,
            context,
            plan=plan,
            generation_options=self._profile("step"),
            planning_options=self._profile("planning"),
            inactivity_timeout=self._inactivity_timeout(),
            session=self._session,
        )
        return AgentDispatch(triage=triage, events=self._with_route_event(triage, self._drain()))

    async def adispatch_input(self, task_input: str) -> AgentDispatch:
        """Async dispatch: triage and agent loop with awaitable model calls."""
        if self.has_active_task:
            raise RuntimeError("An agent task is already running.")

        self._append_user_input(task_input)
        context = build_conversation_model_messages(self._session)
        triage = await self._triage.aclassify(
            task_input,
            context,
            generation_options=self._profile("triage"),
        )
        self._last_triage = triage
        self._session.metadata["last_triage"] = {
            "outcome": triage.outcome,
            "reason": triage.reason,
            "timestamp": datetime.now().isoformat(),
        }
        if triage.outcome == "direct_answer":
            self._update_task_state(
                route=triage.outcome,
                phase="executing",
                step_index=None,
                step_description=None,
                pending_tool=None,
                summary="Answering directly without tool use.",
                active=True,
            )
        else:
            self._update_task_state(
                route=triage.outcome,
                phase="planning",
                step_index=None,
                step_description=None,
                pending_tool=None,
                summary="Preparing agent execution.",
                active=True,
            )
        self._session.touch()

        if triage.outcome == "direct_answer":
            self._direct_stream_active = True
            return AgentDispatch(
                triage=triage,
                stream=self._astream_direct_answer(context, self._profile("direct")),
            )

        supports_tools = getattr(self._model, "supports_tools", lambda: True)
        if not bool(supports_tools()):
            self._update_task_state(
                phase="failed",
                summary=(
                    "The active model/provider cannot execute tools in agent mode. "
                    "Switch targets or ask a simple direct question instead."
                ),
                active=False,
            )
            raise RuntimeError(
                "The active model/provider cannot execute tools in agent mode. "
                "Switch targets or ask a simple direct question instead."
            )

        self._session.metadata["approval_mode"] = self._approval.mode
        plan = (
            TaskPlan(
                task=task_input,
                steps=[PlanStep(index=1, description=task_input)],
                status="planning",
            )
            if triage.outcome == "single_step_task"
            else None
        )

        async def _events() -> AsyncIterator[AgentEvent]:
            self._async_agent_active = True
            try:
                routed = TaskRouted(route=triage.outcome, reason=triage.reason)
                self._record_event(routed)
                yield routed
                async for event in self._loop.arun(
                    task_input,
                    context,
                    plan=plan,
                    generation_options=self._profile("step"),
                    planning_options=self._profile("planning"),
                    inactivity_timeout=self._inactivity_timeout(),
                    session=self._session,
                ):
                    self._record_event(event)
                    yield event
            finally:
                self._async_agent_active = False

        return AgentDispatch(triage=triage, events=_events())

    def apply_tool_approval(self, approved: bool, *, autonomous_all: bool = False) -> None:
        """Resume async agent loop after a tool approval decision (no sync iterator)."""
        if autonomous_all:
            self.set_autonomous()
        if approved:
            self._update_task_state(
                pending_tool=None,
                wait_reason="",
                summary="Approval granted.",
                active=True,
            )
        else:
            self._update_task_state(
                phase="recovering",
                pending_tool=None,
                wait_reason="",
                last_error="Approval denied.",
                summary="Approval denied. Recovering task flow.",
                active=True,
            )
        self._pending_tool = None
        self._loop.supply_tool_approval(approved)

    def approve_action(self, autonomous: bool = False) -> Iterator[AgentEvent]:
        """Approve the pending tool call and optionally enable autonomous mode."""
        if autonomous:
            self.set_autonomous()
        if self._pending_tool is None:
            return iter(())
        self._update_task_state(
            pending_tool=None,
            wait_reason="",
            summary="Approval granted.",
            active=True,
        )
        self._pending_tool = None
        return self._drain(True)

    def deny_action(self) -> Iterator[AgentEvent]:
        """Deny the pending tool call and resume the task."""
        if self._pending_tool is None:
            return iter(())
        self._update_task_state(
            phase="recovering",
            pending_tool=None,
            wait_reason="",
            last_error="Approval denied.",
            summary="Approval denied. Recovering task flow.",
            active=True,
        )
        self._pending_tool = None
        return self._drain(False)

    def set_autonomous(self) -> None:
        """Enable autonomous approvals for the current task."""
        self._approval.set_autonomous()
        self._session.metadata["approval_mode"] = self._approval.mode
        self._update_task_state(summary="Autonomous approvals enabled.")
        self._session.touch()

    def stop(self, reason: str = "Task stopped by user.") -> None:
        """Stop the current task and reset controller state."""
        if not self.has_active_task:
            return

        self._loop.stop()
        cancel = getattr(self._model, "cancel", None)
        if callable(cancel):
            cancel()
        self._session.history.append(
            Message(
                role="assistant",
                content=reason,
                timestamp=datetime.now(),
                metadata={"agent_task": "stopped"},
            )
        )
        self._update_task_state(
            phase="stopped",
            pending_tool=None,
            summary=reason,
            active=False,
        )
        self._finish_task()

    def compact_if_needed(self) -> int:
        """Compact the stored session history if needed before a task."""
        self._last_compaction_count = 0
        if not self._compactor.needs_compaction(self._messages_for_token_estimation()):
            return 0

        compacted = self._compactor.compact(
            self._session.history,
            build_system_instructions(self._session),
        )
        self._last_compaction_count = self._compactor.last_compacted_count
        if not self._last_compaction_count:
            return 0

        self._session.history = compacted
        self._session.metadata["last_compaction"] = {
            "count": self._last_compaction_count,
            "timestamp": datetime.now().isoformat(),
        }
        self._session.metadata["compaction_count"] = (
            int(self._session.metadata.get("compaction_count", 0)) + 1
        )
        self._session.touch()
        self._notify_autosave()
        return self._last_compaction_count

    def _with_route_event(
        self,
        triage: TaskTriage,
        events: Iterator[AgentEvent],
    ) -> Iterator[AgentEvent]:
        """Prepend a routing event before planned execution begins."""

        def iterator() -> Iterator[AgentEvent]:
            event = TaskRouted(route=triage.outcome, reason=triage.reason)
            self._record_event(event)
            yield event
            yield from events

        return iterator()

    def _drain(self, decision: bool | None = None) -> Iterator[AgentEvent]:
        """Yield events until completion or the next approval pause."""

        def iterator() -> Iterator[AgentEvent]:
            nonlocal decision
            while self._generator is not None:
                try:
                    event = (
                        next(self._generator)
                        if decision is None
                        else self._generator.send(decision)
                    )
                except StopIteration:
                    self._generator = None
                    return

                decision = None
                self._record_event(event)
                yield event
                if isinstance(event, ToolCallRequested) and event.requires_approval:
                    self._pending_tool = event
                    return

        return iterator()

    def _record_event(self, event: AgentEvent) -> None:
        if isinstance(event, TaskRouted):
            self._update_task_state(
                route=event.route,
                summary=event.reason or "Agent route selected.",
                active=True,
            )
            return

        if isinstance(event, PhaseChanged):
            pending_tool = None if event.phase != "waiting_approval" else _UNCHANGED
            retry_count = _UNCHANGED
            wait_reason = _UNCHANGED
            if event.phase == "retrying":
                retry_count = self._coerce_int(self.task_state.get("retry_count", 0), 0) + 1
                wait_reason = "retrying after recent failure"
            elif event.phase == "waiting_approval":
                wait_reason = event.summary
            elif event.phase in {"executing", "planning", "replanning"}:
                wait_reason = ""
            self._update_task_state(
                phase=event.phase,
                step_index=event.step_index,
                step_description=event.step_description,
                pending_tool=pending_tool,
                retry_count=retry_count,
                wait_reason=wait_reason,
                summary=event.summary,
                active=event.phase not in {"stopped", "timed_out", "completed", "failed"},
            )
            return

        if isinstance(event, PlanGenerated):
            self._session.tasks.append(event.plan)
            self._session.touch()
            self._notify_autosave()
            return

        if isinstance(event, StepStarted):
            self._update_task_state(
                phase="executing",
                step_index=event.step.index,
                step_description=event.step.description,
                pending_tool=None,
                wait_reason="",
                retry_count=0,
                last_error="",
                summary=f"Executing step {event.step.index}.",
                active=True,
            )
            return

        if isinstance(event, ToolCallResult):
            if event.result.status == "success":
                self._update_task_state(
                    phase="executing",
                    pending_tool=None,
                    wait_reason="",
                    last_error="",
                    summary=event.result.summary,
                    active=True,
                )
            else:
                self._update_task_state(
                    phase="recovering",
                    pending_tool=None,
                    wait_reason="recovering after failed tool call",
                    last_error=event.result.summary,
                    summary=event.result.summary,
                    active=True,
                )
            self._session.history.append(
                Message(
                    role="tool",
                    content=event.result.output or event.result.summary,
                    timestamp=datetime.now(),
                    metadata={
                        "tool_name": event.tool_name,
                        "status": event.result.status,
                        "exit_code": event.result.exit_code,
                        "files_changed": event.result.files_changed,
                        "rollback_entries": event.rollback_entries,
                    },
                )
            )
            self._session.touch()
            return

        if isinstance(event, ToolCallRequested):
            if event.requires_approval:
                self._pending_tool = event
                self._update_task_state(
                    phase="waiting_approval",
                    pending_tool=event.tool_name,
                    wait_reason=f"approval required for {event.tool_name}",
                    summary=f"Waiting for approval: {event.tool_name}.",
                    active=True,
                )
            else:
                self._pending_tool = None
                self._update_task_state(
                    phase="executing",
                    pending_tool=None,
                    wait_reason="",
                    summary=f"Running tool: {event.tool_name}.",
                    active=True,
                )
            return

        if isinstance(event, TaskComplete):
            self._update_task_state(
                phase="completed",
                pending_tool=None,
                wait_reason="",
                summary="Task complete.",
                active=False,
            )
            self._session.history.append(
                Message(
                    role="assistant",
                    content=event.summary,
                    timestamp=datetime.now(),
                    metadata={
                        "agent_task": "completed",
                        "triage": self.task_state.get("route", ""),
                    },
                )
            )
            self._finish_task()
            return

        if isinstance(event, TaskStopped):
            self._update_task_state(
                phase="stopped",
                pending_tool=None,
                wait_reason="",
                summary=event.reason,
                active=False,
            )
            self._session.history.append(
                Message(
                    role="assistant",
                    content=event.reason,
                    timestamp=datetime.now(),
                    metadata={"agent_task": "stopped"},
                )
            )
            self._finish_task()
            return

        if isinstance(event, TaskTimedOut):
            self._update_task_state(
                phase="timed_out",
                pending_tool=None,
                wait_reason="",
                summary=event.reason,
                active=False,
            )
            self._session.history.append(
                Message(
                    role="assistant",
                    content=event.reason,
                    timestamp=datetime.now(),
                    metadata={"agent_task": "timed_out"},
                )
            )
            self._finish_task()
            return

        if isinstance(event, TaskFailed):
            self._update_task_state(
                phase="failed",
                pending_tool=None,
                wait_reason="",
                last_error=event.reason,
                summary=event.reason,
                active=False,
            )
            self._session.history.append(
                Message(
                    role="assistant",
                    content=event.reason,
                    timestamp=datetime.now(),
                    metadata={"agent_task": "failed"},
                )
            )
            self._finish_task()

    def _finish_task(self) -> None:
        self._generator = None
        self._pending_tool = None
        self._direct_stream_active = False
        self._async_agent_active = False
        self._approval.reset()
        self._session.metadata.pop("approval_mode", None)
        self._update_task_state(active=False, pending_tool=None)
        self._session.touch()

    def undo_last(self):
        """Undo the most recent file change recorded for this session."""
        if self.has_active_task:
            raise RuntimeError("Stop the active agent task before undoing changes.")
        return self._safety.rollback.undo_last()

    def undo_all(self):
        """Undo all recorded file changes for this session."""
        if self.has_active_task:
            raise RuntimeError("Stop the active agent task before undoing changes.")
        return self._safety.rollback.undo_all()

    def _append_user_input(self, task_input: str) -> None:
        self._session.history.append(
            Message(role="user", content=task_input, timestamp=datetime.now())
        )
        self._session.touch()
        self.compact_if_needed()
        self._notify_autosave()

    def _profile(self, phase: str) -> dict[str, object]:
        """Derive an internal generation profile for a specific agent phase."""
        base = dict(self._generation_config)
        temperature = self._coerce_float(base.get("temperature"), 0.7)
        top_p = self._coerce_float(base.get("top_p"), 1.0)

        # Rely on the model's native default maximum tokens, or fallback to the provided config
        default_tokens = self._model.model_info().default_max_tokens
        max_tokens = self._coerce_int(base.get("max_tokens"), default_tokens)

        if phase == "triage":
            return {
                "temperature": min(temperature, 0.1),
                "max_tokens": min(max_tokens, 512),
                "top_p": top_p,
            }
        if phase == "planning":
            return {
                "temperature": min(temperature, 0.1),
                "max_tokens": min(max_tokens, 2048),
                "top_p": top_p,
            }
        if phase == "step":
            return {
                "temperature": min(temperature, 0.2),
                "max_tokens": max_tokens,
                "top_p": top_p,
            }
        return {
            "temperature": temperature,
            "max_tokens": max_tokens,
            "top_p": top_p,
        }

    @staticmethod
    def _coerce_float(value: object, default: float) -> float:
        """Best-effort numeric coercion for generation profiles."""
        if isinstance(value, bool):
            return float(default)
        if isinstance(value, int | float):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                return default
        return default

    @staticmethod
    def _coerce_int(value: object, default: int) -> int:
        """Best-effort integer coercion for generation profiles."""
        if isinstance(value, bool):
            return int(default)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            try:
                return int(value)
            except ValueError:
                return default
        return default

    def _inactivity_timeout(self) -> int | None:
        value = self._inactivity_timeout_value
        if isinstance(value, int) and value > 0:
            return value
        value = self._session.config_overrides.get("timeouts.inactivity")
        if isinstance(value, int) and value > 0:
            return value
        return None

    async def _astream_direct_answer(
        self,
        messages: list[ModelMessage],
        generation_options: dict[str, object],
    ) -> AsyncIterator[StreamChunk]:
        """Async stream for direct-answer fast path."""
        chunks: list[StreamChunk] = []
        assistant_parts: list[str] = []
        reasoning_parts: list[str] = []
        try:
            async for chunk in self._model.astream_generate(messages, **generation_options):
                chunks.append(chunk)
                if chunk.kind == "final_text" and chunk.text:
                    assistant_parts.append(chunk.text)
                elif chunk.kind == "reasoning" and chunk.text:
                    reasoning_parts.append(chunk.text)
                yield chunk
        finally:
            stopped = self.task_state.get("phase") == "stopped"
            assistant_text = "".join(assistant_parts).strip()
            reasoning_text = "".join(reasoning_parts).strip()
            if (assistant_text or reasoning_text) and not stopped:
                self._session.history.append(
                    Message(
                        role="assistant",
                        content=assistant_text,
                        timestamp=datetime.now(),
                        metadata={
                            "agent_task": "direct_answer",
                            "fast_path": True,
                            "triage": (
                                self._last_triage.outcome if self._last_triage else "direct_answer"
                            ),
                            "reasoning": reasoning_text,
                            "chunks": [chunk.to_dict() for chunk in chunks if not chunk.is_done],
                        },
                    )
                )
                self._update_task_state(
                    phase="completed",
                    pending_tool=None,
                    summary="Direct answer completed.",
                    active=False,
                )
            elif stopped:
                self._update_task_state(active=False, pending_tool=None)
            self._finish_task()
            self._session.touch()

    def _stream_direct_answer(
        self,
        messages: list[ModelMessage],
        generation_options: dict[str, object],
    ) -> Iterator[StreamChunk]:
        """Stream a direct-answer fast path while preserving session history."""
        chunks: list[StreamChunk] = []
        assistant_parts: list[str] = []
        reasoning_parts: list[str] = []
        try:
            for chunk in self._model.stream_generate(messages, **generation_options):
                chunks.append(chunk)
                if chunk.kind == "final_text" and chunk.text:
                    assistant_parts.append(chunk.text)
                elif chunk.kind == "reasoning" and chunk.text:
                    reasoning_parts.append(chunk.text)
                yield chunk
        finally:
            stopped = self.task_state.get("phase") == "stopped"
            assistant_text = "".join(assistant_parts).strip()
            reasoning_text = "".join(reasoning_parts).strip()
            if (assistant_text or reasoning_text) and not stopped:
                self._session.history.append(
                    Message(
                        role="assistant",
                        content=assistant_text,
                        timestamp=datetime.now(),
                        metadata={
                            "agent_task": "direct_answer",
                            "fast_path": True,
                            "triage": (
                                self._last_triage.outcome if self._last_triage else "direct_answer"
                            ),
                            "reasoning": reasoning_text,
                            "chunks": [chunk.to_dict() for chunk in chunks if not chunk.is_done],
                        },
                    )
                )
                self._update_task_state(
                    phase="completed",
                    pending_tool=None,
                    summary="Direct answer completed.",
                    active=False,
                )
            elif stopped:
                self._update_task_state(active=False, pending_tool=None)
            self._finish_task()
            self._session.touch()

    def _update_task_state(
        self,
        *,
        route: object = _UNCHANGED,
        phase: object = _UNCHANGED,
        step_index: object = _UNCHANGED,
        step_description: object = _UNCHANGED,
        pending_tool: object = _UNCHANGED,
        wait_reason: object = _UNCHANGED,
        retry_count: object = _UNCHANGED,
        last_error: object = _UNCHANGED,
        summary: object = _UNCHANGED,
        active: object = _UNCHANGED,
    ) -> None:
        """Persist the current or last agent-task state into session metadata."""
        current = self.task_state
        state = {
            "route": current.get("route", ""),
            "phase": current.get("phase", ""),
            "step_index": current.get("step_index"),
            "step_description": current.get("step_description", ""),
            "pending_tool": current.get("pending_tool", ""),
            "wait_reason": current.get("wait_reason", ""),
            "retry_count": self._coerce_int(current.get("retry_count", 0), 0),
            "last_error": current.get("last_error", ""),
            "summary": current.get("summary", ""),
            "active": bool(current.get("active", False)),
        }

        if route is not _UNCHANGED:
            state["route"] = route
        if phase is not _UNCHANGED:
            state["phase"] = phase
        if step_index is not _UNCHANGED:
            state["step_index"] = step_index
        if step_description is not _UNCHANGED:
            state["step_description"] = step_description
        if pending_tool is not _UNCHANGED:
            state["pending_tool"] = pending_tool
        if wait_reason is not _UNCHANGED:
            state["wait_reason"] = wait_reason
        if retry_count is not _UNCHANGED:
            state["retry_count"] = self._coerce_int(retry_count, 0)
        if last_error is not _UNCHANGED:
            state["last_error"] = last_error
        if summary is not _UNCHANGED:
            state["summary"] = summary
        if active is not _UNCHANGED:
            state["active"] = bool(active)

        state["approval_mode"] = self._approval.mode
        state["rollback_count"] = self.rollback_count
        state["updated_at"] = datetime.now().isoformat()
        self._session.metadata["agent_task_state"] = state
        self._session.touch()
        self._notify_autosave()

    def _messages_for_token_estimation(self) -> list[Message]:
        return [*build_instruction_messages(self._session), *self._session.history]
