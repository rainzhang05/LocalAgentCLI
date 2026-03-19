"""/setup command handler — first-run interactive wizard."""

from __future__ import annotations

from rich.console import Console
from rich.prompt import Prompt

from localagentcli.commands.router import CommandHandler, CommandResult, CommandRouter
from localagentcli.config.manager import ConfigManager
from localagentcli.session.manager import SessionManager
from localagentcli.shell.prompt import supports_interactive_prompt


class SetupHandler(CommandHandler):
    """Launch the interactive setup wizard."""

    def __init__(self, config: ConfigManager, session_manager: SessionManager, console: Console):
        self._config = config
        self._session_manager = session_manager
        self._console = console

    def execute(self, args: list[str]) -> CommandResult:
        self._console.print()
        self._console.print("[bold]Setup Wizard[/bold]")
        self._console.print("Configure your LocalAgent CLI settings.\n")

        current_workspace = self._config.get("general.workspace", ".")
        current_mode = self._config.get("general.default_mode", "agent")
        current_level = self._config.get("general.logging_level", "normal")

        if supports_interactive_prompt():
            workspace = Prompt.ask(
                "Workspace directory",
                default=current_workspace,
                console=self._console,
            )
            mode = Prompt.ask(
                "Default mode",
                choices=["chat", "agent"],
                default=current_mode,
                console=self._console,
            )
            level = Prompt.ask(
                "Logging level",
                choices=["normal", "verbose", "debug"],
                default=current_level,
                console=self._console,
            )
        else:
            workspace = current_workspace
            mode = current_mode
            level = current_level
            self._console.print(
                "[dim]Non-interactive setup detected; using current default settings.[/dim]"
            )

        try:
            self._config.set("general.workspace", workspace)
            self._config.set("general.default_mode", mode)
            self._config.set("general.logging_level", level)
        except ValueError as e:
            self._console.print(f"[red]Warning: {e}[/red]")

        # Update session with new config values
        session = self._session_manager.current
        session.mode = self._config.get("general.default_mode", "agent")
        session.workspace = self._config.get("general.workspace", ".")

        self._console.print()
        self._console.print(
            "[dim]Model and provider setup will be available once\n"
            "model/provider support is installed.[/dim]"
        )
        self._console.print()

        return CommandResult.ok("Setup complete.")

    def help_text(self) -> str:
        return "Run the interactive setup wizard.\nUsage: /setup"


def register(
    router: CommandRouter,
    config: ConfigManager,
    session_manager: SessionManager,
    console: Console,
) -> None:
    """Register the /setup command."""
    router.register("setup", SetupHandler(config, session_manager, console))
