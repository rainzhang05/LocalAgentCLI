"""/agent command handlers."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeAlias

from localagentcli.agents.controller import AgentController
from localagentcli.commands.router import CommandHandler, CommandResult, CommandRouter, CommandSpec
from localagentcli.config.manager import ConfigManager

UndoAction: TypeAlias = Callable[[], tuple[str, str | None]]


class AgentParentHandler(CommandHandler):
    """Parent handler that shows subcommand help."""

    def execute(self, args: list[str]) -> CommandResult:
        return CommandResult.error(
            "/agent requires a subcommand: approve, deny, undo, undo-all. "
            "Use /help agent for details."
        )

    def describe(self) -> CommandSpec:
        return CommandSpec(
            group="Agent",
            summary="Control the active agent task and approval state.",
            usage="/agent <approve|deny|undo|undo-all>",
            argument_hint="<subcommand>",
            details=(
                "Use /agent approve to enable autonomous approvals, or /agent deny "
                "to reject the pending tool call. Use /agent undo or /agent undo-all "
                "to revert recorded file changes from this session."
            ),
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
                "Approval mode set to autonomous for the current and future sessions.",
                presentation="success",
            )

        controller.set_autonomous()
        if controller.has_pending_approval:
            return CommandResult.ok(
                "Approved pending action. Autonomous approvals enabled for the current and "
                "future sessions.",
                data={"action": "agent_resume", "decision": "approve", "autonomous": True},
                presentation="success",
            )
        return CommandResult.ok(
            "Autonomous approvals enabled for the current and future sessions.",
            presentation="success",
        )

    def describe(self) -> CommandSpec:
        return CommandSpec(
            group="Agent",
            summary="Enable autonomous approvals for this shell and future sessions.",
            usage="/agent approve",
            details="Use /config safety.approval_mode balanced to switch back.",
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
            presentation="warning",
        )

    def describe(self) -> CommandSpec:
        return CommandSpec(
            group="Agent",
            summary="Deny the pending agent action.",
            usage="/agent deny",
        )


class AgentUndoHandler(CommandHandler):
    """Undo the most recent rollback entry for the current session."""

    def __init__(self, undo_action: UndoAction):
        self._undo_action = undo_action

    def execute(self, args: list[str]) -> CommandResult:
        try:
            message, body = self._undo_action()
        except (RuntimeError, ValueError) as exc:
            return CommandResult.error(str(exc))
        return CommandResult.ok(message, presentation="success", body=body)

    def describe(self) -> CommandSpec:
        return CommandSpec(
            group="Agent",
            summary="Undo the most recent agent file change recorded for this session.",
            usage="/agent undo",
        )


class AgentUndoAllHandler(CommandHandler):
    """Undo all rollback entries for the current session."""

    def __init__(self, undo_action: UndoAction):
        self._undo_action = undo_action

    def execute(self, args: list[str]) -> CommandResult:
        try:
            message, body = self._undo_action()
        except (RuntimeError, ValueError) as exc:
            return CommandResult.error(str(exc))
        return CommandResult.ok(message, presentation="success", body=body)

    def describe(self) -> CommandSpec:
        return CommandSpec(
            group="Agent",
            summary="Undo all agent file changes recorded for this session.",
            usage="/agent undo-all",
        )


def register(
    router: CommandRouter,
    controller_getter: Callable[[], AgentController | None],
    config: ConfigManager,
    *,
    undo_last: UndoAction | None = None,
    undo_all: UndoAction | None = None,
) -> None:
    """Register the /agent command group."""
    undo_last = undo_last or _undo_unavailable
    undo_all = undo_all or _undo_unavailable
    router.register("agent", AgentParentHandler(), visible_in_menu=False)
    router.register("agent approve", AgentApproveHandler(controller_getter, config))
    router.register("agent deny", AgentDenyHandler(controller_getter))
    router.register("agent undo", AgentUndoHandler(undo_last))
    router.register("agent undo-all", AgentUndoAllHandler(undo_all))


def _undo_unavailable() -> tuple[str, str | None]:
    raise ValueError("No rollback history is available for this session.")
