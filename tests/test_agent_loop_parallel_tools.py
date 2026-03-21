"""Tests for parallel read-only tool batches in AgentLoop."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from pathlib import Path

from localagentcli.agents.events import ToolCallRequested, ToolCallResult
from localagentcli.agents.loop import AgentLoop
from localagentcli.agents.planner import TaskPlanner
from localagentcli.models.backends.base import GenerationResult, ModelMessage
from localagentcli.safety.approval import ApprovalManager
from localagentcli.safety.boundary import WorkspaceBoundary
from localagentcli.safety.layer import SafetyLayer
from localagentcli.safety.rollback import RollbackManager
from localagentcli.tools import create_default_tool_registry
from localagentcli.tools.base import ToolResult
from localagentcli.tools.registry import ToolRegistry
from localagentcli.tools.router import DynamicToolSpec, ToolRouter


def _safety(tmp_path: Path) -> SafetyLayer:
    return SafetyLayer(
        ApprovalManager(),
        WorkspaceBoundary(tmp_path.resolve()),
        RollbackManager("session-1", tmp_path / ".cache"),
    )


def _drain_handle_tool_calls(
    gen: Iterator[object],
) -> tuple[list[object], tuple[list[ModelMessage], bool]]:
    events: list[object] = []
    while True:
        try:
            events.append(next(gen))
        except StopIteration as exc:
            assert exc.value is not None
            return events, exc.value


def test_parallel_read_only_yields_all_requests_before_results(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    (tmp_path / "b.txt").write_text("b", encoding="utf-8")
    registry = create_default_tool_registry(tmp_path)
    loop = AgentLoop(
        model=object(),  # unused
        tools=registry,
        planner=TaskPlanner(object()),
        safety=_safety(tmp_path),
    )
    result = GenerationResult(
        text="",
        tool_calls=[
            {
                "id": "call-a",
                "type": "function",
                "function": {"name": "file_read", "arguments": '{"path":"a.txt"}'},
            },
            {
                "id": "call-b",
                "type": "function",
                "function": {"name": "file_read", "arguments": '{"path":"b.txt"}'},
            },
        ],
    )
    events, (messages, had_error) = _drain_handle_tool_calls(loop._handle_tool_calls(result))

    assert had_error is False
    assert [m.metadata.get("tool_call_id") for m in messages] == ["call-a", "call-b"]
    assert [m.metadata.get("tool_name") for m in messages] == ["file_read", "file_read"]

    tool_reqs = [e for e in events if isinstance(e, ToolCallRequested)]
    tool_res = [e for e in events if isinstance(e, ToolCallResult)]
    assert len(tool_reqs) == len(tool_res) == 2
    req_indices = [events.index(e) for e in tool_reqs]
    res_indices = [events.index(e) for e in tool_res]
    assert max(req_indices) < min(res_indices)


def test_parallel_read_only_runs_tools_concurrently(tmp_path: Path) -> None:
    lock = threading.Lock()
    entry_times: list[float] = []

    def slow_a(**_kwargs: object) -> ToolResult:
        with lock:
            entry_times.append(time.monotonic())
        time.sleep(0.12)
        return ToolResult.success(summary="a", output="a")

    def slow_b(**_kwargs: object) -> ToolResult:
        with lock:
            entry_times.append(time.monotonic())
        time.sleep(0.12)
        return ToolResult.success(summary="b", output="b")

    router = ToolRouter(tmp_path, builtins=ToolRegistry())
    router.register_dynamic_tool(
        DynamicToolSpec(
            name="slow_a",
            description="slow a",
            parameters_schema={"type": "object", "properties": {}},
            executor=slow_a,
            requires_approval=False,
            is_read_only=True,
        )
    )
    router.register_dynamic_tool(
        DynamicToolSpec(
            name="slow_b",
            description="slow b",
            parameters_schema={"type": "object", "properties": {}},
            executor=slow_b,
            requires_approval=False,
            is_read_only=True,
        )
    )
    loop = AgentLoop(
        model=object(),
        tools=router,
        planner=TaskPlanner(object()),
        safety=_safety(tmp_path),
    )
    result = GenerationResult(
        text="",
        tool_calls=[
            {
                "id": "1",
                "type": "function",
                "function": {"name": "slow_a", "arguments": "{}"},
            },
            {
                "id": "2",
                "type": "function",
                "function": {"name": "slow_b", "arguments": "{}"},
            },
        ],
    )
    start = time.monotonic()
    events, (_messages, had_error) = _drain_handle_tool_calls(loop._handle_tool_calls(result))
    elapsed = time.monotonic() - start

    assert had_error is False
    assert len(entry_times) == 2
    assert entry_times[1] - entry_times[0] < 0.08
    assert elapsed < 0.22
    assert sum(isinstance(e, ToolCallRequested) for e in events) == 2


def test_mixed_read_and_write_falls_back_to_sequential_interleaving(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    registry = create_default_tool_registry(tmp_path)
    loop = AgentLoop(
        model=object(),
        tools=registry,
        planner=TaskPlanner(object()),
        safety=_safety(tmp_path),
    )
    result = GenerationResult(
        text="",
        tool_calls=[
            {
                "id": "r1",
                "type": "function",
                "function": {"name": "file_read", "arguments": '{"path":"a.txt"}'},
            },
            {
                "id": "w1",
                "type": "function",
                "function": {
                    "name": "file_write",
                    "arguments": '{"path":"out.txt","content":"x"}',
                },
            },
        ],
    )
    gen = loop._handle_tool_calls(result)
    events: list[object] = []
    while True:
        try:
            ev = next(gen)
            events.append(ev)
            if isinstance(ev, ToolCallRequested) and ev.requires_approval:
                events.append(gen.send(True))
        except StopIteration:
            break

    tool_reqs = [e for e in events if isinstance(e, ToolCallRequested)]
    tool_res = [e for e in events if isinstance(e, ToolCallResult)]
    assert len(tool_reqs) == 2
    assert len(tool_res) == 2
    first_res_idx = events.index(tool_res[0])
    second_req_idx = events.index(tool_reqs[1])
    assert first_res_idx < second_req_idx
