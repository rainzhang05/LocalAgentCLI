"""/help command handler."""

from __future__ import annotations

from localagentcli.commands.router import CommandHandler, CommandResult, CommandRouter


# Command groupings for organized help display
COMMAND_GROUPS = [
    ("System", ["help", "setup", "status", "config", "exit"]),
    ("Session", ["session new", "session save", "session load", "session list", "session clear"]),
]


class HelpHandler(CommandHandler):
    """Display help for all commands or a specific command."""

    def __init__(self, router: CommandRouter):
        self._router = router

    def execute(self, args: list[str]) -> CommandResult:
        if args:
            return self._command_help(args[0])
        return self._all_help()

    def _all_help(self) -> CommandResult:
        commands = self._router.get_commands()
        lines = ["Available commands:", ""]

        for group_name, command_names in COMMAND_GROUPS:
            lines.append(f"  {group_name}:")
            for name in command_names:
                if name in commands:
                    handler = commands[name]
                    # Get first line of help text as summary
                    summary = handler.help_text().split("\n")[0]
                    lines.append(f"    /{name:<20s} {summary}")
            lines.append("")

        lines.append("Type /help <command> for detailed help on a specific command.")
        return CommandResult.ok("\n".join(lines))

    def _command_help(self, name: str) -> CommandResult:
        commands = self._router.get_commands()

        # Try exact match
        if name in commands:
            return CommandResult.ok(f"/{name}\n\n{commands[name].help_text()}")

        # Try with subcommands
        matching = {k: v for k, v in commands.items() if k.startswith(f"{name} ")}
        if matching:
            lines = [f"/{name} subcommands:", ""]
            for cmd_name, handler in sorted(matching.items()):
                sub = cmd_name.split(" ", 1)[1]
                summary = handler.help_text().split("\n")[0]
                lines.append(f"  /{cmd_name:<20s} {summary}")
            return CommandResult.ok("\n".join(lines))

        return CommandResult.error(
            f"Unknown command: /{name}\nUse /help to see all available commands."
        )

    def help_text(self) -> str:
        return "Show help for all commands or a specific command.\nUsage: /help [command]"


def register(router: CommandRouter) -> None:
    """Register the /help command."""
    router.register("help", HelpHandler(router))
