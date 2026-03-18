"""/status command handler."""

from __future__ import annotations

from localagentcli.commands.router import CommandHandler, CommandResult, CommandRouter
from localagentcli.config.manager import ConfigManager
from localagentcli.session.manager import SessionManager


class StatusHandler(CommandHandler):
    """Display current session status."""

    def __init__(self, session_manager: SessionManager, config: ConfigManager):
        self._session_manager = session_manager
        self._config = config

    def execute(self, args: list[str]) -> CommandResult:
        session = self._session_manager.current
        lines = [
            "Current Status:",
            "",
            f"  Mode:          {session.mode}",
            f"  Model:         {session.model or '(none)'}",
            f"  Provider:      {session.provider or '(none)'}",
            f"  Workspace:     {session.workspace}",
            f"  Session:       {session.name or '(unsaved)'}",
            f"  Approval:      {self._config.get('safety.approval_mode', 'balanced')}",
            f"  Messages:      {len(session.history)}",
        ]
        return CommandResult.ok("\n".join(lines))

    def help_text(self) -> str:
        return "Display current session status.\nUsage: /status"


def register(router: CommandRouter, session_manager: SessionManager, config: ConfigManager) -> None:
    """Register the /status command."""
    router.register("status", StatusHandler(session_manager, config))
