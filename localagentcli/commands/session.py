"""/session command handlers — new, save, load, list, clear."""

from __future__ import annotations

from localagentcli.commands.router import CommandHandler, CommandResult, CommandRouter, CommandSpec
from localagentcli.session.manager import SessionManager
from localagentcli.shell.prompt import SelectionOption, select_option, supports_interactive_prompt


class SessionNewHandler(CommandHandler):
    """Start a fresh session."""

    def __init__(self, session_manager: SessionManager):
        self._session_manager = session_manager

    def execute(self, args: list[str]) -> CommandResult:
        self._session_manager.new_session()
        return CommandResult.ok(
            "New session started.",
            data={"action": "session_changed"},
            presentation="success",
        )

    def describe(self) -> CommandSpec:
        return CommandSpec(
            group="Session",
            summary="Start a fresh session with the default target and empty history.",
            usage="/session new",
        )


class SessionSaveHandler(CommandHandler):
    """Save the current session to disk."""

    def __init__(self, session_manager: SessionManager):
        self._session_manager = session_manager

    def execute(self, args: list[str]) -> CommandResult:
        name = args[0] if args else None
        try:
            path = self._session_manager.save_session(name)
            return CommandResult.ok(f"Session saved to {path}", presentation="success")
        except RuntimeError as e:
            return CommandResult.error(str(e))

    def describe(self) -> CommandSpec:
        return CommandSpec(
            group="Session",
            summary="Save the current session to disk.",
            usage="/session save [name]",
            argument_hint="[name]",
        )


class SessionLoadHandler(CommandHandler):
    """Load a previously saved session."""

    def __init__(self, session_manager: SessionManager):
        self._session_manager = session_manager

    def execute(self, args: list[str]) -> CommandResult:
        if not args:
            if not supports_interactive_prompt():
                return CommandResult.error("Session name required.\nUsage: /session load <name>")
            sessions = self._session_manager.list_sessions()
            if not sessions:
                return CommandResult.ok("No saved sessions.", presentation="status")
            selection = _select_session_option(self._session_manager, "Choose a session to load")
            if selection is None:
                return CommandResult.ok("Session load cancelled.", presentation="warning")
            args = [selection.value]
        try:
            self._session_manager.load_session(args[0])
            return CommandResult.ok(
                f"Session '{args[0]}' loaded.",
                data={"action": "session_changed"},
                presentation="success",
            )
        except FileNotFoundError:
            return CommandResult.error(
                f"Session '{args[0]}' not found.\nUse /session list to see available sessions."
            )

    def describe(self) -> CommandSpec:
        return CommandSpec(
            group="Session",
            summary="Load a saved session.",
            usage="/session load <name>",
            argument_hint="[name]",
        )


class SessionListHandler(CommandHandler):
    """List all saved sessions."""

    def __init__(self, session_manager: SessionManager):
        self._session_manager = session_manager

    def execute(self, args: list[str]) -> CommandResult:
        sessions = self._session_manager.list_sessions()
        if not sessions:
            return CommandResult.ok("No saved sessions.", presentation="status")

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

    def describe(self) -> CommandSpec:
        return CommandSpec(
            group="Session",
            summary="List saved sessions with their mode, target, and message count.",
            usage="/session list",
        )


class SessionClearHandler(CommandHandler):
    """Clear the current session history."""

    def __init__(self, session_manager: SessionManager):
        self._session_manager = session_manager

    def execute(self, args: list[str]) -> CommandResult:
        self._session_manager.clear_session()
        return CommandResult.ok("Session history cleared.", presentation="success")

    def describe(self) -> CommandSpec:
        return CommandSpec(
            group="Session",
            summary="Clear session history while keeping the active target and workspace.",
            usage="/session clear",
        )


class SessionParentHandler(CommandHandler):
    """Parent handler that shows subcommand help."""

    def execute(self, args: list[str]) -> CommandResult:
        return CommandResult.error(
            "/session requires a subcommand: new, save, load, list, clear. "
            "Use /help session for details."
        )

    def describe(self) -> CommandSpec:
        return CommandSpec(
            group="Session",
            summary="Manage saved sessions.",
            usage="/session <new|save|load|list|clear>",
            argument_hint="<subcommand>",
            details=(
                "Use /session new to reset context, /session save or load for continuity, "
                "and /session clear to keep the target while removing history."
            ),
        )


def register(router: CommandRouter, session_manager: SessionManager) -> None:
    """Register all /session subcommands."""
    router.register("session", SessionParentHandler(), visible_in_menu=False)
    router.register("session new", SessionNewHandler(session_manager))
    router.register("session save", SessionSaveHandler(session_manager))
    router.register("session load", SessionLoadHandler(session_manager))
    router.register("session list", SessionListHandler(session_manager))
    router.register("session clear", SessionClearHandler(session_manager))


def build_session_selection_options(session_manager: SessionManager) -> list[SelectionOption]:
    """Build interactive selection options for saved sessions."""
    options: list[SelectionOption] = []
    for item in session_manager.list_sessions():
        name = str(item.get("name", ""))
        if not name:
            continue
        model = str(item.get("model", "")) or "(none)"
        mode = str(item.get("mode", "")) or "unknown"
        options.append(
            SelectionOption(
                value=name,
                label=name,
                description=f"{mode} • {model}",
                aliases=(mode, model),
            )
        )
    return options


def _select_session_option(
    session_manager: SessionManager,
    message: str,
) -> SelectionOption | None:
    """Prompt for one saved session."""
    options = build_session_selection_options(session_manager)
    if not options:
        return None
    return select_option(message, options)
