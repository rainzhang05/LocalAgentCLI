"""Tests for /agents command handlers."""

from __future__ import annotations

from localagentcli.commands import agents as agents_cmd
from localagentcli.commands.router import CommandRouter


class _RuntimeStub:
    def __init__(self):
        self.enabled = True
        self.snapshot: dict[str, dict[str, object]] = {}
        self.inspect_calls: list[str] = []
        self.cleared = 0

    def is_multi_agent_path_routing_enabled(self) -> bool:
        return self.enabled

    def active_agents_snapshot(self) -> dict[str, dict[str, object]]:
        return self.snapshot

    def inspect_active_agent(self, target_path: str):
        self.inspect_calls.append(target_path)
        if target_path == "missing":
            raise ValueError("live agent path `/root/missing` not found")
        return "/root/worker", {
            "name": "worker",
            "status": "completed",
            "nickname": "",
            "role": "analysis",
            "task_count": 2,
            "last_error": "",
            "updated_at": "2026-03-29T12:34:56",
        }

    def clear_active_agents(self) -> int:
        return self.cleared


def _make_router(runtime: _RuntimeStub) -> CommandRouter:
    router = CommandRouter()
    agents_cmd.register(router, runtime_provider=lambda: runtime)
    return router


def test_agents_parent_requires_subcommand():
    runtime = _RuntimeStub()
    router = _make_router(runtime)

    result = router.dispatch("agents")

    assert not result.success
    assert "subcommand" in result.message


def test_agents_list_reports_disabled_feature():
    runtime = _RuntimeStub()
    runtime.enabled = False
    router = _make_router(runtime)

    result = router.dispatch("agents list")

    assert not result.success
    assert "disabled" in result.message


def test_agents_list_reports_empty_state():
    runtime = _RuntimeStub()
    router = _make_router(runtime)

    result = router.dispatch("agents list")

    assert result.success
    assert result.presentation == "status"
    assert "No active sub-agents" in result.message


def test_agents_list_renders_table_rows():
    runtime = _RuntimeStub()
    runtime.snapshot = {
        "/root/worker": {
            "status": "completed",
            "task_count": 3,
            "updated_at": "2026-03-29T10:20:30",
            "role": "analysis",
        }
    }
    router = _make_router(runtime)

    result = router.dispatch("agents list")

    assert result.success
    assert "Active sub-agents" in result.message
    assert "/root/worker" in result.message
    assert "completed" in result.message


def test_agents_inspect_requires_path():
    runtime = _RuntimeStub()
    router = _make_router(runtime)

    result = router.dispatch("agents inspect")

    assert not result.success
    assert "Usage" in result.message


def test_agents_inspect_renders_details():
    runtime = _RuntimeStub()
    router = _make_router(runtime)

    result = router.dispatch("agents inspect worker")

    assert result.success
    assert "Agent: /root/worker" in result.message
    assert "Status:" in result.message
    assert runtime.inspect_calls == ["worker"]


def test_agents_inspect_handles_missing_target():
    runtime = _RuntimeStub()
    router = _make_router(runtime)

    result = router.dispatch("agents inspect missing")

    assert not result.success
    assert "not found" in result.message


def test_agents_clear_handles_empty_and_non_empty_states():
    runtime = _RuntimeStub()
    router = _make_router(runtime)

    runtime.cleared = 0
    empty = router.dispatch("agents clear")
    assert empty.success
    assert empty.presentation == "status"

    runtime.cleared = 2
    cleared = router.dispatch("agents clear")
    assert cleared.success
    assert cleared.presentation == "success"
    assert "Cleared 2" in cleared.message
