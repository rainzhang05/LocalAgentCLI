"""Submission/event runtime shared by shell and headless surfaces."""

from __future__ import annotations

import asyncio
import warnings
from collections import deque
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from typing import Any, Callable

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


async def _anext_agent_event(agent_iter: AsyncIterator[AgentEvent]) -> AgentEvent:
    """Resume the agent iterator after ToolCallRequested (enters tool-approval wait)."""
    return await anext(agent_iter)


@dataclass
class _PendingApproval:
    """State captured when a tool call pauses waiting for approval."""

    submission_id: str
    tool_name: str
    agent_iter: Any
    approval_policy: str


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
        self._current_iterator: AsyncIterator[RuntimeEvent] | None = None
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

    async def aiter_events(self) -> AsyncIterator[RuntimeEvent]:
        """Drain events for queued or active submissions (async)."""
        while self._current_iterator is not None or self._submission_queue:
            if self._current_iterator is None:
                submission = self._submission_queue.popleft()
                self._current_submission_id = submission.id
                self._current_iterator = self._astart_submission(submission)

            try:
                event = await anext(self._current_iterator)
            except StopAsyncIteration:
                self._current_iterator = None
                if self._pending_approval is None:
                    self._current_submission_id = None
                continue

            if self._event_log is not None:
                self._event_log.append_event(event)
            yield event

    def iter_events(self) -> Iterator[RuntimeEvent]:
        """Sync wrapper (runs the shared async iterator under asyncio.run)."""
        warnings.warn(
            "SessionRuntime.iter_events() is deprecated; use aiter_events() instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        import asyncio

        agen = self.aiter_events()

        class _SyncBridge:
            def __init__(self) -> None:
                self._it = agen.__aiter__()

            def __iter__(self):
                return self

            def __next__(self) -> RuntimeEvent:
                async def _one() -> RuntimeEvent:
                    return await self._it.__anext__()

                try:
                    asyncio.get_running_loop()
                except RuntimeError:
                    return asyncio.run(_one())
                raise RuntimeError(
                    "iter_events() cannot be used while an event loop is running; "
                    "use aiter_events()."
                )

        return _SyncBridge()

    async def ainterrupt(self) -> AsyncIterator[RuntimeEvent]:
        """Interrupt the active turn (async)."""
        self.submit(InterruptOp())
        async for event in self.aiter_events():
            yield event

    async def ashutdown(self) -> AsyncIterator[RuntimeEvent]:
        """Shut down the runtime (async)."""
        self.submit(ShutdownOp())
        async for event in self.aiter_events():
            yield event

    def interrupt(self) -> Iterator[RuntimeEvent]:
        import asyncio

        async def _run():
            out: list[RuntimeEvent] = []
            async for e in self.ainterrupt():
                out.append(e)
            return out

        return iter(asyncio.run(_run()))

    def shutdown(self) -> Iterator[RuntimeEvent]:
        import asyncio

        async def _run():
            out: list[RuntimeEvent] = []
            async for e in self.ashutdown():
                out.append(e)
            return out

        return iter(asyncio.run(_run()))

    def close(self) -> None:
        """Immediately release execution resources."""
        self._submission_queue.clear()
        self._current_iterator = None
        self._current_submission_id = None
        self._pending_approval = None
        self._active_model = None
        self._execution_runtime.close()

    async def aclose(self) -> None:
        """Async teardown including remote provider cleanup."""
        self._submission_queue.clear()
        self._current_iterator = None
        self._current_submission_id = None
        self._pending_approval = None
        self._active_model = None
        await self._execution_runtime.aclose()

    def _astart_submission(self, submission: Submission) -> AsyncIterator[RuntimeEvent]:
        op = submission.op
        if isinstance(op, UserTurnOp):
            return self._arun_user_turn(submission.id, op)
        if isinstance(op, ApprovalDecisionOp):
            return self._aresume_approval(submission.id, op)
        if isinstance(op, InterruptOp):
            return self._ahandle_interrupt(submission.id)
        if isinstance(op, ShutdownOp):
            return self._ahandle_shutdown(submission.id)
        raise RuntimeError(f"Unsupported runtime operation: {op}")

    async def _arun_user_turn(
        self, submission_id: str, op: UserTurnOp
    ) -> AsyncIterator[RuntimeEvent]:
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
            turn = await self._execution_runtime.adispatch_agent_turn(op.prompt)
        else:
            turn = await self._execution_runtime.arun_chat_turn(op.prompt)

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
            async for ev in self._adrain_stream(
                submission_id,
                turn.stream,
                mode=turn.mode,
                route=turn.route,
            ):
                yield ev
            return

        if turn.events is not None:
            async for ev in self._adrain_agent_events(
                submission_id=submission_id,
                events=turn.events,
                approval_policy=op.approval_policy,
            ):
                yield ev
            return

        yield RuntimeEvent(
            type="turn_completed",
            submission_id=submission_id,
            data={"mode": turn.mode},
        )

    async def _adrain_stream(
        self,
        submission_id: str,
        chunks: AsyncIterator[StreamChunk],
        *,
        mode: str,
        route: str | None = None,
    ) -> AsyncIterator[RuntimeEvent]:
        final_parts: list[str] = []
        completed = False

        async for chunk in chunks:
            if chunk.kind == "final_text" and chunk.text:
                final_parts.append(chunk.text)
            yield RuntimeEvent(
                type="stream_chunk",
                submission_id=submission_id,
                data=chunk,
            )
            if chunk.is_done:
                completed = True
                final_text = "".join(final_parts).strip()
                yield RuntimeEvent(
                    type="turn_completed",
                    submission_id=submission_id,
                    data={"mode": mode, "final_text": final_text, "route": route or ""},
                    message=final_text,
                )

        if completed:
            return

        synthetic_done = StreamChunk(kind="done", is_done=True)
        yield RuntimeEvent(
            type="stream_chunk",
            submission_id=submission_id,
            data=synthetic_done,
        )
        final_text = "".join(final_parts).strip()
        yield RuntimeEvent(
            type="turn_completed",
            submission_id=submission_id,
            data={"mode": mode, "final_text": final_text, "route": route or ""},
            message=final_text,
        )

    async def _adrain_agent_events(
        self,
        submission_id: str,
        events: AsyncIterator[AgentEvent],
        *,
        approval_policy: str,
    ) -> AsyncIterator[RuntimeEvent]:
        controller = self._execution_runtime.agent_controller
        it = events.__aiter__()
        next_event_override: AgentEvent | None = None
        while True:
            try:
                if next_event_override is not None:
                    event = next_event_override
                    next_event_override = None
                else:
                    event = await anext(it)
            except StopAsyncIteration:
                break
            yield RuntimeEvent(
                type="agent_event",
                submission_id=submission_id,
                data=event,
                message=getattr(event, "type", ""),
            )
            if isinstance(event, ToolCallRequested) and event.requires_approval:
                if approval_policy == "auto":
                    self._pending_approval = _PendingApproval(
                        submission_id=submission_id,
                        tool_name=event.tool_name,
                        agent_iter=it,
                        approval_policy=approval_policy,
                    )
                    resume_task: asyncio.Task[AgentEvent] = asyncio.create_task(
                        _anext_agent_event(it)
                    )
                    await asyncio.sleep(0)
                    if controller is not None:
                        controller.apply_tool_approval(True, autonomous_all=True)
                    try:
                        next_event = await resume_task
                    except StopAsyncIteration:
                        break
                    self._pending_approval = None
                    next_event_override = next_event
                    continue
                if approval_policy == "deny":
                    self._pending_approval = _PendingApproval(
                        submission_id=submission_id,
                        tool_name=event.tool_name,
                        agent_iter=it,
                        approval_policy=approval_policy,
                    )
                    resume_deny: asyncio.Task[AgentEvent] = asyncio.create_task(
                        _anext_agent_event(it)
                    )
                    await asyncio.sleep(0)
                    if controller is not None:
                        controller.apply_tool_approval(False)
                    try:
                        next_event = await resume_deny
                    except StopAsyncIteration:
                        break
                    self._pending_approval = None
                    next_event_override = next_event
                    continue
                if controller is not None:
                    self._pending_approval = _PendingApproval(
                        submission_id=submission_id,
                        tool_name=event.tool_name,
                        agent_iter=it,
                        approval_policy=approval_policy,
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

    async def _aresume_approval(
        self,
        submission_id: str,
        op: ApprovalDecisionOp,
    ) -> AsyncIterator[RuntimeEvent]:
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

        controller = self._execution_runtime.agent_controller
        if controller is not None:
            if op.decision == "deny":
                controller.apply_tool_approval(False)
            elif op.decision == "approve_all":
                controller.apply_tool_approval(True, autonomous_all=True)
            else:
                controller.apply_tool_approval(True, autonomous_all=op.autonomous)

        self._pending_approval = None
        async for ev in self._adrain_agent_events(
            submission_id=submission_id,
            events=_AsyncIterResume(pending.agent_iter),
            approval_policy=pending.approval_policy,
        ):
            yield ev

    async def _ahandle_interrupt(self, submission_id: str) -> AsyncIterator[RuntimeEvent]:
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

    async def _ahandle_shutdown(self, submission_id: str) -> AsyncIterator[RuntimeEvent]:
        await self.aclose()
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


class _AsyncIterResume:
    """Re-wrap a partially consumed async-iterator for continued anext consumption."""

    def __init__(self, it: Any) -> None:
        self._it = it

    def __aiter__(self) -> _AsyncIterResume:
        return self

    async def __anext__(self) -> AgentEvent:
        return await anext(self._it)


def _legacy_approval_callback(
    controller: AgentController,
) -> Callable[[ApprovalDecisionOp], Iterator[AgentEvent]]:
    """Sync approval resume (legacy); async runtime uses apply_tool_approval + saved iterator."""

    def callback(op: ApprovalDecisionOp) -> Iterator[AgentEvent]:
        if op.decision == "approve":
            return controller.approve_action(autonomous=op.autonomous)
        if op.decision == "approve_all":
            return controller.approve_action(autonomous=True)
        return controller.deny_action()

    return callback
