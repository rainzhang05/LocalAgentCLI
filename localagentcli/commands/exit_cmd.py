"""/exit command handler."""

from __future__ import annotations

from localagentcli.commands.router import CommandHandler, CommandResult, CommandRouter


class ExitHandler(CommandHandler):
    """Exit the shell cleanly."""

    def execute(self, args: list[str]) -> CommandResult:
        # Return a special action that ShellUI handles
        return CommandResult.ok("exit", data={"action": "exit"})

    def help_text(self) -> str:
        return "Exit the shell.\nUsage: /exit"


def register(router: CommandRouter) -> None:
    """Register the /exit command."""
    router.register("exit", ExitHandler())
