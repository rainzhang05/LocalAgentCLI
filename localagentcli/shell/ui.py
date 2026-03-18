"""ShellUI — main input loop, rendering, and lifecycle management."""

from __future__ import annotations

from rich.console import Console
from rich.prompt import Confirm

from localagentcli.commands import (
    config_cmd,
    exit_cmd,
    setup_cmd,
)
from localagentcli.commands import (
    help as help_cmd,
)
from localagentcli.commands import (
    session as session_cmd,
)
from localagentcli.commands import (
    status as status_cmd,
)
from localagentcli.commands.router import CommandResult, CommandRouter
from localagentcli.config.manager import ConfigManager
from localagentcli.session.manager import SessionManager
from localagentcli.shell.prompt import create_prompt_session
from localagentcli.storage.logger import Logger
from localagentcli.storage.manager import StorageManager


class ShellUI:
    """Interactive shell for LocalAgentCLI."""

    def __init__(
        self,
        config: ConfigManager,
        storage: StorageManager,
        first_run: bool = False,
    ):
        self._config = config
        self._storage = storage
        self._first_run = first_run
        self._console = Console()

        # Initialize logger
        self._logger = Logger(
            storage.logs_dir,
            config.get("general.logging_level", "normal"),
        )

        # Initialize session manager and create initial session
        self._session_manager = SessionManager(storage.sessions_dir, config)
        self._session_manager.new_session()

        # Initialize command router and register commands
        self._router = CommandRouter()
        self._register_commands()

        # Create prompt session with history and tab completion
        history_file = storage.cache_dir / "input_history"
        self._prompt_session = create_prompt_session(self._router, history_file)

    def _register_commands(self) -> None:
        """Register all Phase 1 command handlers."""
        help_cmd.register(self._router)
        status_cmd.register(self._router, self._session_manager, self._config)
        config_cmd.register(self._router, self._config)
        setup_cmd.register(self._router, self._config, self._session_manager, self._console)
        session_cmd.register(self._router, self._session_manager)
        exit_cmd.register(self._router)

    def run(self) -> None:
        """Main input loop."""
        self._logger.normal("Session started (id: %s)", self._session_manager.current.id)

        self._display_welcome()

        if self._first_run:
            self._run_first_time_setup()

        # Cleanup at startup (best-effort)
        try:
            self._storage.cleanup_cache()
            self._storage.cleanup_logs()
        except Exception:
            pass

        # Main loop
        while True:
            try:
                self._display_status_header()
                user_input = self._prompt_session.prompt("> ")

                if not user_input.strip():
                    continue

                stripped = user_input.strip()

                if stripped.startswith("/"):
                    result = self._router.dispatch(stripped[1:])
                    self._render_command_result(result)

                    if result.data and result.data.get("action") == "exit":
                        self._handle_exit()
                        break
                else:
                    self._console.print(
                        "[dim]No model connected. Use /setup or configure a "
                        "model/provider to start chatting.[/dim]"
                    )

            except KeyboardInterrupt:
                self._console.print()
                continue
            except EOFError:
                self._console.print()
                self._handle_exit()
                break

    def _display_welcome(self) -> None:
        """Show the welcome banner."""
        self._console.print()
        self._console.print("[bold]LocalAgent CLI[/bold] v0.1.0")
        self._console.print()

    def _run_first_time_setup(self) -> None:
        """Run the setup wizard on first launch."""
        self._console.print("Welcome to LocalAgent CLI!")
        self._console.print()
        self._console.print("Let's get you set up. This will only take a moment.")
        self._console.print()

        result = self._router.dispatch("setup")
        self._render_command_result(result)

        self._console.print("You're all set! Here's how to get started:")
        self._console.print()
        self._console.print("  Just type naturally to start a conversation or task.")
        self._console.print("  Use /help to see all available commands.")
        self._console.print("  Use /mode chat for conversation, /mode agent for tasks.")
        self._console.print()

    def _display_status_header(self) -> None:
        """Render the status header line."""
        session = self._session_manager.current
        model = session.model or "(none)"
        workspace = session.workspace

        # Abbreviate home directory
        try:
            from pathlib import Path

            home = str(Path.home())
            if workspace.startswith(home):
                workspace = "~" + workspace[len(home) :]
        except Exception:
            pass

        self._console.print(
            f"[dim]LocalAgent | mode: {session.mode} | model: {model} "
            f"| workspace: {workspace}[/dim]"
        )

    def _render_command_result(self, result: CommandResult) -> None:
        """Render a command result to the console."""
        if result.success:
            if result.message and result.message != "exit":
                self._console.print(result.message)
        else:
            self._console.print(f"[red]✗ {result.message}[/red]")

    def _handle_exit(self) -> None:
        """Handle clean shutdown with optional session save."""
        session = self._session_manager.current
        if session.is_modified:
            try:
                save = Confirm.ask(
                    "Save session before exiting?",
                    default=False,
                    console=self._console,
                )
                if save:
                    path = self._session_manager.save_session()
                    self._console.print(f"Session saved to {path}")
            except (KeyboardInterrupt, EOFError):
                pass

        self._logger.normal("Session ended")
        self._console.print("[dim]Goodbye.[/dim]")
