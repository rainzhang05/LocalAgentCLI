"""/agent command handlers."""

from __future__ import annotations

from collections.abc import Callable

from localagentcli.agents.controller import AgentController
from localagentcli.commands.router import CommandHandler, CommandResult, CommandRouter


class AgentParentHandler(CommandHandler):
    """Parent handler that shows subcommand help."""

    def execute(self, args: list[str]) -> CommandResult:
        return CommandResult.error("/agent requires a subcommand: approve, deny, stop")

    def help_text(self) -> str:
        return (
            "Control the active agent task.\n"
            "Subcommands:\n"
            "  /agent approve            Enable autonomous approval for the current task\n"
            "  /agent deny               Deny the pending tool call\n"
            "  /agent stop               Stop the current agent task"
        )


class AgentApproveHandler(CommandHandler):
    """Enable autonomous approvals and optionally resume a pending action."""

    def __init__(self, controller_getter: Callable[[], AgentController | None]):
        self._controller_getter = controller_getter

    def execute(self, args: list[str]) -> CommandResult:
        controller = self._controller_getter()
        if controller is None or not controller.has_active_task:
            return CommandResult.error("No active agent task.")

        controller.set_autonomous()
        if controller.has_pending_approval:
            return CommandResult.ok(
                "Approved pending action. Autonomous approvals enabled for the current task.",
                data={"action": "agent_resume", "decision": "approve", "autonomous": True},
            )
        return CommandResult.ok("Autonomous approvals enabled for the current task.")

    def help_text(self) -> str:
        return "Enable autonomous approval for the current task.\nUsage: /agent approve"


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


class AgentStopHandler(CommandHandler):
    """Stop the current agent task."""

    def __init__(self, controller_getter: Callable[[], AgentController | None]):
        self._controller_getter = controller_getter

    def execute(self, args: list[str]) -> CommandResult:
        controller = self._controller_getter()
        if controller is None or not controller.has_active_task:
            return CommandResult.error("No active agent task.")
        controller.stop()
        return CommandResult.ok("Stopped the current agent task.")

    def help_text(self) -> str:
        return "Stop the current agent task.\nUsage: /agent stop"


def register(
    router: CommandRouter,
    controller_getter: Callable[[], AgentController | None],
) -> None:
    """Register the /agent command group."""
    router.register("agent", AgentParentHandler())
    router.register("agent approve", AgentApproveHandler(controller_getter))
    router.register("agent deny", AgentDenyHandler(controller_getter))
    router.register("agent stop", AgentStopHandler(controller_getter))
