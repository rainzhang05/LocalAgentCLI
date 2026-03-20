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
    def test_approve_sets_autonomous_and_resumes_pending(self, config):
        controller = FakeController(active=True, pending=True)
        router = CommandRouter()
        register_agent(router, lambda: controller, config)

        result = router.dispatch("agent approve")

        assert result.success
        assert config.get("safety.approval_mode") == "autonomous"
        assert controller.autonomous_set is True
        assert result.data == {
            "action": "agent_resume",
            "decision": "approve",
            "autonomous": True,
        }

    def test_approve_without_active_task_updates_config(self, config):
        controller = FakeController(active=False, pending=False)
        router = CommandRouter()
        register_agent(router, lambda: controller, config)

        result = router.dispatch("agent approve")

        assert result.success
        assert config.get("safety.approval_mode") == "autonomous"
        assert controller.autonomous_set is False

    def test_deny_errors_without_pending_action(self, config):
        controller = FakeController(active=True, pending=False)
        router = CommandRouter()
        register_agent(router, lambda: controller, config)

        result = router.dispatch("agent deny")

        assert not result.success
        assert "No pending" in result.message

    def test_undo_runs_supplied_action(self, config):
        router = CommandRouter()
        register_agent(
            router,
            lambda: None,
            config,
            undo_last=lambda: ("Reverted last agent change.", "Path: demo.py"),
        )

        result = router.dispatch("agent undo")

        assert result.success
        assert result.presentation == "success"
        assert result.body == "Path: demo.py"

    def test_undo_all_errors_when_history_is_unavailable(self, config):
        router = CommandRouter()
        register_agent(router, lambda: None, config)

        result = router.dispatch("agent undo-all")

        assert not result.success
        assert "No rollback history" in result.message
