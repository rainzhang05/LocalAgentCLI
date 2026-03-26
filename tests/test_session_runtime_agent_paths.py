"""SessionRuntime agent-event draining (async paths)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from localagentcli.agents.events import (
    TaskComplete,
    TaskFailed,
    TaskRouted,
    TaskStopped,
    TaskTimedOut,
    ToolCallRequested,
)
from localagentcli.agents.planner import TaskPlan
from localagentcli.models.backends.base import StreamChunk
from localagentcli.runtime.core import RuntimeTurn
from localagentcli.runtime.protocol import ApprovalDecisionOp, UserTurnOp
from localagentcli.runtime.session_runtime import SessionRuntime


def _exec_mock() -> MagicMock:
    ex = MagicMock()
    sm = MagicMock()
    sm.current.mode = "agent"
    sm.current.history = []
    ex._services.session_manager = sm
    ex.agent_controller = MagicMock()
    return ex


async def _collect(runtime: SessionRuntime) -> list:
    out = []
    async for ev in runtime.aiter_events():
        out.append(ev)
    return out


@pytest.mark.asyncio
async def test_agent_events_task_complete():
    plan = TaskPlan(task="t")

    async def events():
        yield TaskRouted(route="planner", reason="go")
        yield TaskComplete(summary="ok", plan=plan)

    ex = _exec_mock()
    ex.adispatch_agent_turn = AsyncMock(
        return_value=RuntimeTurn(mode="agent", events=events(), route="planner")
    )
    ex.resolve_active_model = MagicMock(return_value=object())

    rt = SessionRuntime(ex)
    rt.submit(UserTurnOp(prompt="hi", mode="agent"))
    out = await _collect(rt)
    assert any(e.type == "turn_completed" for e in out)


@pytest.mark.asyncio
async def test_agent_events_task_failed():
    plan = TaskPlan(task="t")

    async def events():
        yield TaskFailed(reason="boom", plan=plan)

    ex = _exec_mock()
    ex.adispatch_agent_turn = AsyncMock(
        return_value=RuntimeTurn(mode="agent", events=events(), route="planner")
    )
    ex.resolve_active_model = MagicMock(return_value=object())

    rt = SessionRuntime(ex)
    rt.submit(UserTurnOp(prompt="hi", mode="agent"))
    out = await _collect(rt)
    assert any(e.type == "turn_failed" for e in out)


@pytest.mark.asyncio
async def test_agent_events_task_stopped():
    async def events():
        yield TaskStopped(reason="user")

    ex = _exec_mock()
    ex.adispatch_agent_turn = AsyncMock(
        return_value=RuntimeTurn(mode="agent", events=events(), route="planner")
    )
    ex.resolve_active_model = MagicMock(return_value=object())

    rt = SessionRuntime(ex)
    rt.submit(UserTurnOp(prompt="hi", mode="agent"))
    out = await _collect(rt)
    assert any(e.type == "turn_interrupted" for e in out)


@pytest.mark.asyncio
async def test_agent_events_task_timed_out():
    plan = TaskPlan(task="t")

    async def events():
        yield TaskTimedOut(reason="slow", plan=plan)

    ex = _exec_mock()
    ex.adispatch_agent_turn = AsyncMock(
        return_value=RuntimeTurn(mode="agent", events=events(), route="planner")
    )
    ex.resolve_active_model = MagicMock(return_value=object())

    rt = SessionRuntime(ex)
    rt.submit(UserTurnOp(prompt="hi", mode="agent"))
    out = await _collect(rt)
    assert any(e.type == "turn_interrupted" for e in out)


@pytest.mark.asyncio
async def test_agent_shell_policy_emits_approval_requested():
    async def events():
        yield ToolCallRequested(
            tool_name="shell_execute",
            arguments={"command": "ls"},
            requires_approval=True,
        )

    ex = _exec_mock()
    ex.adispatch_agent_turn = AsyncMock(
        return_value=RuntimeTurn(mode="agent", events=events(), route="planner")
    )
    ex.resolve_active_model = MagicMock(return_value=object())

    rt = SessionRuntime(ex)
    rt.submit(UserTurnOp(prompt="hi", mode="agent", approval_policy="shell"))
    out = await _collect(rt)
    assert any(e.type == "approval_requested" for e in out)
    assert rt.has_pending_approval


@pytest.mark.asyncio
async def test_approval_resume_then_task_complete():
    plan = TaskPlan(task="t")

    async def events():
        yield ToolCallRequested(
            tool_name="file_read",
            arguments={"path": "a.txt"},
            requires_approval=True,
        )
        yield TaskComplete(summary="done", plan=plan)

    ex = _exec_mock()
    ex.adispatch_agent_turn = AsyncMock(
        return_value=RuntimeTurn(mode="agent", events=events(), route="planner")
    )
    ex.resolve_active_model = MagicMock(return_value=object())

    rt = SessionRuntime(ex)
    rt.submit(UserTurnOp(prompt="hi", mode="agent", approval_policy="shell"))
    async for ev in rt.aiter_events():
        if ev.type == "approval_requested":
            break
    assert rt.has_pending_approval
    rt.submit(ApprovalDecisionOp("approve"))
    tail = []
    async for ev in rt.aiter_events():
        tail.append(ev)
    assert any(e.type == "turn_completed" for e in tail)


@pytest.mark.asyncio
async def test_stream_turn_completed_uses_streamed_text_instead_of_session_history():
    async def chunks():
        yield StreamChunk(text="fresh ", kind="final_text")
        yield StreamChunk(text="answer", kind="final_text")
        yield StreamChunk(kind="done", is_done=True)

    ex = _exec_mock()
    ex._services.session_manager.current.mode = "agent"
    ex._services.session_manager.current.history = [
        MagicMock(role="assistant", content="stale summary")
    ]
    ex.adispatch_agent_turn = AsyncMock(
        return_value=RuntimeTurn(mode="agent", stream=chunks(), route="direct_answer")
    )
    ex.resolve_active_model = MagicMock(return_value=object())

    rt = SessionRuntime(ex)
    rt.submit(UserTurnOp(prompt="hi", mode="agent"))
    out = await _collect(rt)

    completed = [event for event in out if event.type == "turn_completed"]
    assert completed
    assert completed[-1].message == "fresh answer"
    assert completed[-1].data["final_text"] == "fresh answer"


@pytest.mark.asyncio
async def test_stream_without_done_still_emits_turn_completed_event():
    async def chunks():
        yield StreamChunk(text="partial", kind="final_text")

    ex = _exec_mock()
    ex._services.session_manager.current.mode = "chat"
    ex.adispatch_agent_turn = AsyncMock(
        return_value=RuntimeTurn(mode="chat", stream=chunks(), route=None)
    )
    ex.arun_chat_turn = AsyncMock(return_value=RuntimeTurn(mode="chat", stream=chunks()))
    ex.resolve_active_model = MagicMock(return_value=object())

    rt = SessionRuntime(ex)
    rt.submit(UserTurnOp(prompt="hi", mode="chat"))
    out = await _collect(rt)

    completed = [event for event in out if event.type == "turn_completed"]
    assert completed
    assert completed[-1].data["final_text"] == "partial"
