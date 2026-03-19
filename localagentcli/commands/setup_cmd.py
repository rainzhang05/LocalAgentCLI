"""/setup command handler — first-run interactive wizard."""

from __future__ import annotations

from rich.console import Console

from localagentcli.commands.router import CommandHandler, CommandResult, CommandRouter, CommandSpec
from localagentcli.config.manager import ConfigManager
from localagentcli.session.manager import SessionManager
from localagentcli.shell.prompt import (
    SelectionOption,
    prompt_text,
    select_option,
    supports_interactive_prompt,
)


class SetupHandler(CommandHandler):
    """Launch the interactive setup wizard."""

    def __init__(self, config: ConfigManager, session_manager: SessionManager, console: Console):
        self._config = config
        self._session_manager = session_manager
        self._console = console

    def execute(self, args: list[str]) -> CommandResult:
        current_workspace = self._config.get("general.workspace", ".")
        current_mode = self._config.get("general.default_mode", "agent")
        current_level = self._config.get("general.logging_level", "normal")

        if supports_interactive_prompt():
            workspace = prompt_text(
                "Workspace directory",
                default=str(current_workspace),
            )
            if workspace is None:
                return CommandResult.ok(
                    "Setup cancelled. Using current settings.",
                    presentation="warning",
                )

            mode_selection = select_option(
                "Choose the default mode",
                [
                    SelectionOption(value="chat", label="chat"),
                    SelectionOption(value="agent", label="agent"),
                ],
                default=str(current_mode),
            )
            if mode_selection is None:
                return CommandResult.ok(
                    "Setup cancelled. Using current settings.",
                    presentation="warning",
                )
            mode = mode_selection.value

            level_selection = select_option(
                "Choose the logging level",
                [
                    SelectionOption(value="normal", label="normal"),
                    SelectionOption(value="verbose", label="verbose"),
                    SelectionOption(value="debug", label="debug"),
                ],
                default=str(current_level),
            )
            if level_selection is None:
                return CommandResult.ok(
                    "Setup cancelled. Using current settings.",
                    presentation="warning",
                )
            level = level_selection.value
        else:
            workspace = current_workspace
            mode = current_mode
            level = current_level

        try:
            self._config.set("general.workspace", workspace)
            self._config.set("general.default_mode", mode)
            self._config.set("general.logging_level", level)
        except ValueError as e:
            return CommandResult.error(str(e))

        # Update session with new config values
        session = self._session_manager.current
        session.mode = self._config.get("general.default_mode", "agent")
        session.workspace = self._config.get("general.workspace", ".")

        body = (
            "Model and provider setup will be available once model/provider support is installed."
        )
        if not supports_interactive_prompt():
            body = "Non-interactive setup detected; using current default settings.\n\n" + body

        return CommandResult.ok("Setup complete.", presentation="success", body=body)

    def describe(self) -> CommandSpec:
        return CommandSpec(
            group="System",
            summary="Run the interactive setup wizard.",
            usage="/setup",
            details="Configure the default workspace, mode, and logging level.",
        )


def register(
    router: CommandRouter,
    config: ConfigManager,
    session_manager: SessionManager,
    console: Console,
) -> None:
    """Register the /setup command."""
    router.register("setup", SetupHandler(config, session_manager, console))
