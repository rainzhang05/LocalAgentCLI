"""/agent command handlers."""

from __future__ import annotations

from collections.abc import Callable

from localagentcli.agents.controller import AgentController
from localagentcli.commands.router import CommandHandler, CommandResult, CommandRouter
from localagentcli.config.manager import ConfigManager


class AgentParentHandler(CommandHandler):
    """Parent handler that shows subcommand help."""

    def execute(self, args: list[str]) -> CommandResult:
        return CommandResult.error("/agent requires a subcommand: approve, deny")

    def help_text(self) -> str:
        return (
            "Control the active agent task.\n"
            "Subcommands:\n"
            "  /agent approve            Set approvals to autonomous for this and future sessions\n"
            "  /agent deny               Deny the pending tool call"
        )


class AgentApproveHandler(CommandHandler):
    """Persist autonomous approvals and optionally resume a pending action."""

    def __init__(
        self,
        controller_getter: Callable[[], AgentController | None],
        config: ConfigManager,
    ):
        self._controller_getter = controller_getter
        self._config = config

    def execute(self, args: list[str]) -> CommandResult:
        self._config.set("safety.approval_mode", "autonomous")
        controller = self._controller_getter()
        if controller is None or not controller.has_active_task:
            return CommandResult.ok(
                "Approval mode set to autonomous for the current and future sessions."
            )

        controller.set_autonomous()
        if controller.has_pending_approval:
            return CommandResult.ok(
                "Approved pending action. Autonomous approvals enabled for the current and "
                "future sessions.",
                data={"action": "agent_resume", "decision": "approve", "autonomous": True},
            )
        return CommandResult.ok("Autonomous approvals enabled for the current and future sessions.")

    def help_text(self) -> str:
        return (
            "Set approval mode to autonomous for the current and future sessions.\n"
            "Usage: /agent approve\n"
            "Use /config safety.approval_mode balanced to switch back."
        )


class AgentDenyHandler(CommandHandler):
    """Deny the current pending tool call."""

    def __init__(self, controller_getter: Callable[[], AgentController | None]):
        self._controller_getter = controller_getter

    def execute(self, args: list[str]) -> CommandResult:
        controller = self._controller_getter()
        if controller is None or not controller.has_pending_approval:
            return CommandResult.error("No pending agent action to deny.")
        return CommandResult.ok(
            "Denied pending agent action.",
            data={"action": "agent_resume", "decision": "deny"},
        )

    def help_text(self) -> str:
        return "Deny the pending agent action.\nUsage: /agent deny"


def register(
    router: CommandRouter,
    controller_getter: Callable[[], AgentController | None],
    config: ConfigManager,
) -> None:
    """Register the /agent command group."""
    router.register("agent", AgentParentHandler(), visible_in_menu=False)
    router.register("agent approve", AgentApproveHandler(controller_getter, config))
    router.register("agent deny", AgentDenyHandler(controller_getter))
