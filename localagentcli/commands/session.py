"""/session command handlers — new, save, load, list, clear."""

from __future__ import annotations

from localagentcli.commands.router import CommandHandler, CommandResult, CommandRouter
from localagentcli.session.manager import SessionManager


class SessionNewHandler(CommandHandler):
    """Start a fresh session."""

    def __init__(self, session_manager: SessionManager):
        self._session_manager = session_manager

    def execute(self, args: list[str]) -> CommandResult:
        self._session_manager.new_session()
        return CommandResult.ok("New session started.")

    def help_text(self) -> str:
        return "Start a fresh session.\nUsage: /session new"


class SessionSaveHandler(CommandHandler):
    """Save the current session to disk."""

    def __init__(self, session_manager: SessionManager):
        self._session_manager = session_manager

    def execute(self, args: list[str]) -> CommandResult:
        name = args[0] if args else None
        try:
            path = self._session_manager.save_session(name)
            return CommandResult.ok(f"Session saved to {path}")
        except RuntimeError as e:
            return CommandResult.error(str(e))

    def help_text(self) -> str:
        return "Save the current session.\nUsage: /session save [name]"


class SessionLoadHandler(CommandHandler):
    """Load a previously saved session."""

    def __init__(self, session_manager: SessionManager):
        self._session_manager = session_manager

    def execute(self, args: list[str]) -> CommandResult:
        if not args:
            return CommandResult.error(
                "Session name required.\nUsage: /session load <name>"
            )
        try:
            self._session_manager.load_session(args[0])
            return CommandResult.ok(f"Session '{args[0]}' loaded.")
        except FileNotFoundError:
            return CommandResult.error(
                f"Session '{args[0]}' not found.\nUse /session list to see available sessions."
            )

    def help_text(self) -> str:
        return "Load a saved session.\nUsage: /session load <name>"


class SessionListHandler(CommandHandler):
    """List all saved sessions."""

    def __init__(self, session_manager: SessionManager):
        self._session_manager = session_manager

    def execute(self, args: list[str]) -> CommandResult:
        sessions = self._session_manager.list_sessions()
        if not sessions:
            return CommandResult.ok("No saved sessions.")

        lines = ["Saved sessions:", ""]
        lines.append(f"  {'Name':<25s} {'Mode':<8s} {'Model':<20s} {'Messages':<10s} {'Created'}")
        lines.append(f"  {'─' * 25} {'─' * 8} {'─' * 20} {'─' * 10} {'─' * 20}")
        for s in sessions:
            created = s["created_at"][:19] if s["created_at"] else ""
            lines.append(
                f"  {s['name']:<25s} {s['mode']:<8s} "
                f"{(s['model'] or '(none)'):<20s} {s['message_count']:<10d} {created}"
            )
        return CommandResult.ok("\n".join(lines))

    def help_text(self) -> str:
        return "List all saved sessions.\nUsage: /session list"


class SessionClearHandler(CommandHandler):
    """Clear the current session history."""

    def __init__(self, session_manager: SessionManager):
        self._session_manager = session_manager

    def execute(self, args: list[str]) -> CommandResult:
        self._session_manager.clear_session()
        return CommandResult.ok("Session history cleared.")

    def help_text(self) -> str:
        return "Clear session history (keeps model/provider/workspace).\nUsage: /session clear"


class SessionParentHandler(CommandHandler):
    """Parent handler that shows subcommand help."""

    def execute(self, args: list[str]) -> CommandResult:
        return CommandResult.error(
            "/session requires a subcommand: new, save, load, list, clear"
        )

    def help_text(self) -> str:
        return (
            "Manage sessions.\n"
            "Subcommands:\n"
            "  /session new              Start a fresh session\n"
            "  /session save [name]      Save current session\n"
            "  /session load <name>      Load a saved session\n"
            "  /session list             List saved sessions\n"
            "  /session clear            Clear session history"
        )


def register(router: CommandRouter, session_manager: SessionManager) -> None:
    """Register all /session subcommands."""
    router.register("session", SessionParentHandler())
    router.register("session new", SessionNewHandler(session_manager))
    router.register("session save", SessionSaveHandler(session_manager))
    router.register("session load", SessionLoadHandler(session_manager))
    router.register("session list", SessionListHandler(session_manager))
    router.register("session clear", SessionClearHandler(session_manager))
