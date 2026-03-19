"""/exit command handler."""

from __future__ import annotations

from localagentcli.commands.router import CommandHandler, CommandResult, CommandRouter, CommandSpec


class ExitHandler(CommandHandler):
    """Exit the shell cleanly."""

    def execute(self, args: list[str]) -> CommandResult:
        # Return a special action that ShellUI handles
        return CommandResult.ok("exit", data={"action": "exit"})

    def describe(self) -> CommandSpec:
        return CommandSpec(
            group="System",
            summary="Exit the shell, optionally saving the current session first.",
            usage="/exit",
        )


def register(router: CommandRouter) -> None:
    """Register the /exit command."""
    router.register("exit", ExitHandler())
