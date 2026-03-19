"""Tests for /agent command handlers."""

from __future__ import annotations

from localagentcli.commands.agent import register as register_agent
from localagentcli.commands.router import CommandRouter


class FakeController:
    """Simple controller stub for command tests."""

    def __init__(self, active: bool = True, pending: bool = False):
        self.has_active_task = active
        self.has_pending_approval = pending
        self.autonomous_set = False
        self.stopped = False

    def set_autonomous(self):
        self.autonomous_set = True

    def stop(self):
        self.stopped = True


class TestAgentCommands:
    def test_approve_sets_autonomous_and_resumes_pending(self):
        controller = FakeController(active=True, pending=True)
        router = CommandRouter()
        register_agent(router, lambda: controller)

        result = router.dispatch("agent approve")

        assert result.success
        assert controller.autonomous_set is True
        assert result.data == {
            "action": "agent_resume",
            "decision": "approve",
            "autonomous": True,
        }

    def test_deny_errors_without_pending_action(self):
        controller = FakeController(active=True, pending=False)
        router = CommandRouter()
        register_agent(router, lambda: controller)

        result = router.dispatch("agent deny")

        assert not result.success
        assert "No pending" in result.message

    def test_stop_stops_active_task(self):
        controller = FakeController(active=True, pending=False)
        router = CommandRouter()
        register_agent(router, lambda: controller)

        result = router.dispatch("agent stop")

        assert result.success
        assert controller.stopped is True
