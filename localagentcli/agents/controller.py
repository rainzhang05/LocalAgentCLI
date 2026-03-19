"""High-level agent mode orchestration and session integration."""

from __future__ import annotations

from collections.abc import Generator, Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from localagentcli.agents.events import (
    AgentEvent,
    PlanGenerated,
    TaskComplete,
    TaskFailed,
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
    build_instruction_messages,
    build_system_instructions,
)
from localagentcli.session.state import Message, Session
from localagentcli.tools.registry import ToolRegistry


@dataclass
class AgentDispatch:
    """Prepared execution path for one agent-mode plain-text input."""

    triage: TaskTriage
    events: Iterator[AgentEvent] | None = None
    stream: Iterator[StreamChunk] | None = None


class AgentController:
    """Drive agent tasks and persist state to the active session."""

    def __init__(
        self,
        model: ModelAbstractionLayer,
        session: Session,
        tool_registry: ToolRegistry,
        approval: ApprovalManager | None = None,
        safety: SafetyLayer | None = None,
        rollback_storage: Path | None = None,
        context_limit: int = 8192,
        generation_config: dict[str, object] | None = None,
        inactivity_timeout: int | None = None,
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

    @property
    def has_active_task(self) -> bool:
        """Whether an agent task is currently running or paused for approval."""
        return self._generator is not None or self._pending_tool is not None

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
    def last_compaction_count(self) -> int:
        """Messages compacted before the most recent task started."""
        return self._last_compaction_count

    def handle_task(self, task_input: str) -> Iterator[AgentEvent]:
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
        context = self._build_context_messages()
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
        self._session.touch()

        if triage.outcome == "direct_answer":
            return AgentDispatch(
                triage=triage,
                stream=self._stream_direct_answer(context, self._profile("direct")),
            )

        supports_tools = getattr(self._model, "supports_tools", lambda: True)
        if not bool(supports_tools()):
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
        )
        return AgentDispatch(triage=triage, events=self._drain())

    def approve_action(self, autonomous: bool = False) -> Iterator[AgentEvent]:
        """Approve the pending tool call and optionally enable autonomous mode."""
        if autonomous:
            self.set_autonomous()
        if self._pending_tool is None:
            return iter(())
        self._pending_tool = None
        return self._drain(True)

    def deny_action(self) -> Iterator[AgentEvent]:
        """Deny the pending tool call and resume the task."""
        if self._pending_tool is None:
            return iter(())
        self._pending_tool = None
        return self._drain(False)

    def set_autonomous(self) -> None:
        """Enable autonomous approvals for the current task."""
        self._approval.set_autonomous()
        self._session.metadata["approval_mode"] = self._approval.mode
        self._session.touch()

    def stop(self) -> None:
        """Stop the current task and reset controller state."""
        if not self.has_active_task:
            return

        self._loop.stop()
        self._model.cancel()
        self._pending_tool = None
        self._generator = None
        self._approval.reset()
        self._session.metadata.pop("approval_mode", None)
        self._session.touch()

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
        return self._last_compaction_count

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
        if isinstance(event, PlanGenerated):
            self._session.tasks.append(event.plan)
            self._session.touch()
            return

        if isinstance(event, ToolCallResult):
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
                    },
                )
            )
            self._session.touch()
            return

        if isinstance(event, TaskComplete):
            self._session.history.append(
                Message(
                    role="assistant",
                    content=event.summary,
                    timestamp=datetime.now(),
                    metadata={"agent_task": "completed"},
                )
            )
            self._finish_task()
            return

        if isinstance(event, TaskFailed):
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
        self._approval.reset()
        self._session.metadata.pop("approval_mode", None)
        self._session.touch()

    def undo_last(self):
        """Undo the most recent file change recorded for this session."""
        return self._safety.rollback.undo_last()

    def undo_all(self):
        """Undo all recorded file changes for this session."""
        return self._safety.rollback.undo_all()

    def _append_user_input(self, task_input: str) -> None:
        self._session.history.append(
            Message(role="user", content=task_input, timestamp=datetime.now())
        )
        self._session.touch()
        self.compact_if_needed()

    def _profile(self, phase: str) -> dict[str, object]:
        """Derive an internal generation profile for a specific agent phase."""
        base = dict(self._generation_config)
        temperature = self._coerce_float(base.get("temperature"), 0.7)
        max_tokens = self._coerce_int(base.get("max_tokens"), 4096)
        top_p = self._coerce_float(base.get("top_p"), 1.0)

        if phase == "triage":
            return {
                "temperature": min(temperature, 0.1),
                "max_tokens": min(max_tokens, 120),
                "top_p": top_p,
            }
        if phase == "planning":
            return {
                "temperature": min(temperature, 0.1),
                "max_tokens": min(max_tokens, 600),
                "top_p": top_p,
            }
        if phase == "step":
            return {
                "temperature": min(temperature, 0.2),
                "max_tokens": min(max_tokens, 1600),
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
            self._session.metadata.pop("approval_mode", None)
            assistant_text = "".join(assistant_parts).strip()
            reasoning_text = "".join(reasoning_parts).strip()
            if assistant_text or reasoning_text:
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
            self._session.touch()

    def _build_context_messages(self) -> list[ModelMessage]:
        system_parts = build_system_instructions(self._session)
        conversation: list[ModelMessage] = []
        for message in self._session.history:
            if message.role == "system":
                system_parts.append(message.content)
                continue
            conversation.append(
                ModelMessage(
                    role=message.role,
                    content=message.content,
                    metadata=dict(message.metadata),
                )
            )
        if system_parts:
            return [ModelMessage(role="system", content="\n\n".join(system_parts)), *conversation]
        return conversation

    def _messages_for_token_estimation(self) -> list[Message]:
        return [*build_instruction_messages(self._session), *self._session.history]
