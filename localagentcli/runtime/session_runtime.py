"""Submission/event runtime shared by shell and headless surfaces."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Callable

from localagentcli.agents.controller import AgentController
from localagentcli.agents.events import (
    AgentEvent,
    TaskComplete,
    TaskFailed,
    TaskRouted,
    TaskStopped,
    TaskTimedOut,
    ToolCallRequested,
)
from localagentcli.models.abstraction import ModelAbstractionLayer
from localagentcli.models.backends.base import StreamChunk
from localagentcli.runtime.core import SessionExecutionRuntime
from localagentcli.runtime.event_log import SessionEventLog
from localagentcli.runtime.protocol import (
    ApprovalDecisionOp,
    InterruptOp,
    RuntimeEvent,
    ShutdownOp,
    Submission,
    UserTurnOp,
)

_SUBMISSION_CAPACITY = 512


@dataclass
class _PendingApproval:
    """State captured when a tool call pauses waiting for approval."""

    submission_id: str
    tool_name: str
    callback: Callable[[ApprovalDecisionOp], Iterator[AgentEvent]]


class SessionRuntime:
    """Drive typed submissions and emit a unified event stream."""

    def __init__(
        self,
        execution_runtime: SessionExecutionRuntime,
        event_log: SessionEventLog | None = None,
    ) -> None:
        self._execution_runtime = execution_runtime
        self._event_log = event_log
        self._submission_queue: deque[Submission] = deque()
        self._current_submission_id: str | None = None
        self._current_iterator: Iterator[RuntimeEvent] | None = None
        self._pending_approval: _PendingApproval | None = None
        self._active_model: ModelAbstractionLayer | None = None

    @property
    def active_submission_id(self) -> str | None:
        """The currently active submission id, if any."""
        return self._current_submission_id

    @property
    def active_agent_controller(self):
        """Expose the current low-level agent controller for command surfaces."""
        return self._execution_runtime.agent_controller

    @property
    def has_pending_approval(self) -> bool:
        """Whether the runtime is paused waiting on approval."""
        return self._pending_approval is not None

    @property
    def event_log_path(self):
        """Return the append-only event-log path when enabled."""
        return self._event_log.path if self._event_log is not None else None

    def submit(self, op: UserTurnOp | ApprovalDecisionOp | InterruptOp | ShutdownOp) -> str:
        """Queue one runtime operation for later event draining."""
        if len(self._submission_queue) >= _SUBMISSION_CAPACITY:
            raise RuntimeError("Runtime submission queue is full.")

        if isinstance(op, UserTurnOp):
            submission = Submission(op=op)
        else:
            if self._current_submission_id is None:
                raise RuntimeError("No active submission is available for this operation.")
            submission = Submission(op=op, id=self._current_submission_id)

        self._submission_queue.append(submission)
        if self._event_log is not None:
            self._event_log.append_submission(submission)
        return submission.id

    def iter_events(self) -> Iterator[RuntimeEvent]:
        """Drain events for queued or active submissions."""
        while self._current_iterator is not None or self._submission_queue:
            if self._current_iterator is None:
                submission = self._submission_queue.popleft()
                self._current_submission_id = submission.id
                self._current_iterator = self._start_submission(submission)

            try:
                event = next(self._current_iterator)
            except StopIteration:
                self._current_iterator = None
                if self._pending_approval is None:
                    self._current_submission_id = None
                continue

            if self._event_log is not None:
                self._event_log.append_event(event)
            yield event

    def interrupt(self) -> Iterator[RuntimeEvent]:
        """Interrupt the active turn and emit interruption events."""
        self.submit(InterruptOp())
        return self.iter_events()

    def shutdown(self) -> Iterator[RuntimeEvent]:
        """Shut down the runtime and release resources."""
        self.submit(ShutdownOp())
        return self.iter_events()

    def close(self) -> None:
        """Immediately release execution resources."""
        self._submission_queue.clear()
        self._current_iterator = None
        self._current_submission_id = None
        self._pending_approval = None
        self._active_model = None
        self._execution_runtime.close()

    def _start_submission(self, submission: Submission) -> Iterator[RuntimeEvent]:
        op = submission.op
        if isinstance(op, UserTurnOp):
            return self._run_user_turn(submission.id, op)
        if isinstance(op, ApprovalDecisionOp):
            return self._resume_approval(submission.id, op)
        if isinstance(op, InterruptOp):
            return self._handle_interrupt(submission.id)
        if isinstance(op, ShutdownOp):
            return self._handle_shutdown(submission.id)
        raise RuntimeError(f"Unsupported runtime operation: {op}")

    def _run_user_turn(self, submission_id: str, op: UserTurnOp) -> Iterator[RuntimeEvent]:
        mode = op.mode or self._execution_runtime._services.session_manager.current.mode
        yield RuntimeEvent(
            type="turn_started",
            submission_id=submission_id,
            data={
                "prompt": op.prompt,
                "mode": mode,
                "approval_policy": op.approval_policy,
            },
        )

        if mode == "agent":
            turn = self._execution_runtime.dispatch_agent_turn(op.prompt)
        else:
            turn = self._execution_runtime.run_chat_turn(op.prompt)

        self._active_model = self._execution_runtime.resolve_active_model()
        if turn is None:
            yield RuntimeEvent(
                type="turn_failed",
                submission_id=submission_id,
                message="Turn could not start with the current target.",
            )
            return

        if turn.route:
            yield RuntimeEvent(
                type="route_selected",
                submission_id=submission_id,
                data={"route": turn.route},
                message=turn.route,
            )

        if turn.stream is not None:
            yield from self._drain_stream(submission_id, turn.stream, mode=turn.mode)
            return

        if turn.events is not None:
            yield from self._drain_agent_events(
                submission_id=submission_id,
                events=turn.events,
                approval_policy=op.approval_policy,
            )
            return

        yield RuntimeEvent(
            type="turn_completed",
            submission_id=submission_id,
            data={"mode": turn.mode},
        )

    def _drain_stream(
        self,
        submission_id: str,
        chunks: Iterator[StreamChunk],
        *,
        mode: str,
    ) -> Iterator[RuntimeEvent]:
        for chunk in chunks:
            yield RuntimeEvent(
                type="stream_chunk",
                submission_id=submission_id,
                data=chunk,
            )
            if chunk.is_done:
                final_text = self._latest_assistant_message()
                yield RuntimeEvent(
                    type="turn_completed",
                    submission_id=submission_id,
                    data={"mode": mode, "final_text": final_text},
                    message=final_text,
                )

    def _drain_agent_events(
        self,
        submission_id: str,
        events: Iterator[AgentEvent],
        *,
        approval_policy: str,
    ) -> Iterator[RuntimeEvent]:
        controller = self._execution_runtime.agent_controller
        for event in events:
            yield RuntimeEvent(
                type="agent_event",
                submission_id=submission_id,
                data=event,
                message=getattr(event, "type", ""),
            )
            if isinstance(event, ToolCallRequested) and event.requires_approval:
                if approval_policy == "auto":
                    self.submit(ApprovalDecisionOp("approve", autonomous=True))
                    return
                if approval_policy == "deny":
                    self.submit(ApprovalDecisionOp("deny"))
                    return
                if controller is not None:
                    self._pending_approval = _PendingApproval(
                        submission_id=submission_id,
                        tool_name=event.tool_name,
                        callback=self._approval_callback(controller),
                    )
                yield RuntimeEvent(
                    type="approval_requested",
                    submission_id=submission_id,
                    data=event,
                    message=event.tool_name,
                )
                return

            if isinstance(event, TaskRouted):
                continue
            if isinstance(event, TaskComplete):
                yield RuntimeEvent(
                    type="turn_completed",
                    submission_id=submission_id,
                    data={"summary": event.summary, "mode": "agent"},
                    message=event.summary,
                )
                return
            if isinstance(event, TaskFailed):
                yield RuntimeEvent(
                    type="turn_failed",
                    submission_id=submission_id,
                    data={"reason": event.reason},
                    message=event.reason,
                )
                return
            if isinstance(event, TaskStopped | TaskTimedOut):
                yield RuntimeEvent(
                    type="turn_interrupted",
                    submission_id=submission_id,
                    data={"reason": event.reason},
                    message=event.reason,
                )
                return

    def _resume_approval(
        self,
        submission_id: str,
        op: ApprovalDecisionOp,
    ) -> Iterator[RuntimeEvent]:
        pending = self._pending_approval
        if pending is None:
            yield RuntimeEvent(
                type="error",
                submission_id=submission_id,
                message="No pending approval is available.",
            )
            return
        if pending.submission_id != submission_id:
            yield RuntimeEvent(
                type="error",
                submission_id=submission_id,
                message="Approval decision does not match the active submission.",
            )
            return

        self._pending_approval = None
        yield from self._drain_agent_events(
            submission_id=submission_id,
            events=pending.callback(op),
            approval_policy="shell",
        )

    def _handle_interrupt(self, submission_id: str) -> Iterator[RuntimeEvent]:
        controller = self._execution_runtime.agent_controller
        if controller is not None and controller.has_active_task:
            controller.stop("Task interrupted.")
        if self._active_model is not None:
            self._active_model.cancel()
        self._pending_approval = None
        yield RuntimeEvent(
            type="turn_interrupted",
            submission_id=submission_id,
            message="Turn interrupted.",
        )

    def _handle_shutdown(self, submission_id: str) -> Iterator[RuntimeEvent]:
        self.close()
        yield RuntimeEvent(
            type="shutdown",
            submission_id=submission_id,
            message="Runtime shut down.",
        )

    def _latest_assistant_message(self) -> str:
        history = self._execution_runtime._services.session_manager.current.history
        for message in reversed(history):
            if message.role == "assistant":
                return message.content
        return ""

    @staticmethod
    def _approval_callback(
        controller: AgentController,
    ) -> Callable[[ApprovalDecisionOp], Iterator[AgentEvent]]:
        def callback(op: ApprovalDecisionOp) -> Iterator[AgentEvent]:
            if op.decision == "approve":
                return controller.approve_action(autonomous=op.autonomous)
            if op.decision == "approve_all":
                return controller.approve_action(autonomous=True)
            return controller.deny_action()

        return callback
