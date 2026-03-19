"""High-level agent mode orchestration and session integration."""

from __future__ import annotations

from collections.abc import Generator, Iterator
from datetime import datetime

from localagentcli.agents.events import (
    AgentEvent,
    PlanGenerated,
    TaskComplete,
    TaskFailed,
    ToolCallRequested,
    ToolCallResult,
)
from localagentcli.agents.loop import AgentLoop
from localagentcli.agents.planner import TaskPlanner
from localagentcli.models.abstraction import ModelAbstractionLayer
from localagentcli.models.backends.base import ModelMessage
from localagentcli.safety.approval import ApprovalManager
from localagentcli.session.compactor import ContextCompactor
from localagentcli.session.state import Message, Session
from localagentcli.tools.registry import ToolRegistry


class AgentController:
    """Drive agent tasks and persist state to the active session."""

    def __init__(
        self,
        model: ModelAbstractionLayer,
        session: Session,
        tool_registry: ToolRegistry,
        approval: ApprovalManager | None = None,
        context_limit: int = 8192,
        generation_config: dict[str, object] | None = None,
    ):
        self._model = model
        self._session = session
        self._tools = tool_registry
        self._approval = approval or ApprovalManager()
        self._planner = TaskPlanner(model)
        self._loop = AgentLoop(model, tool_registry, self._planner, self._approval)
        self._compactor = ContextCompactor(model, context_limit)
        self._generation_config = generation_config or {}
        self._generator: Generator[AgentEvent, bool, None] | None = None
        self._pending_tool: ToolCallRequested | None = None
        self._last_compaction_count = 0

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
        if self.has_active_task:
            raise RuntimeError("An agent task is already running.")

        self._session.history.append(
            Message(role="user", content=task_input, timestamp=datetime.now())
        )
        self._session.touch()
        self.compact_if_needed()
        self._session.metadata["approval_mode"] = self._approval.mode

        self._generator = self._loop.run(
            task_input,
            self._build_context_messages(),
            generation_options=self._generation_config,
        )
        return self._drain()

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
            self._session.pinned_instructions,
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

    def _build_context_messages(self) -> list[ModelMessage]:
        system_parts = list(self._session.pinned_instructions)
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
        pinned_messages = [
            Message(role="system", content=instruction, timestamp=datetime.now())
            for instruction in self._session.pinned_instructions
        ]
        return [*pinned_messages, *self._session.history]
