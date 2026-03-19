"""ShellUI — main input loop, rendering, and lifecycle management."""

from __future__ import annotations

from datetime import datetime

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
    models as models_cmd,
)
from localagentcli.commands import (
    providers as providers_cmd,
)
from localagentcli.commands import (
    session as session_cmd,
)
from localagentcli.commands import (
    status as status_cmd,
)
from localagentcli.commands.router import CommandResult, CommandRouter
from localagentcli.config.manager import ConfigManager
from localagentcli.models.backends.base import ModelBackend, ModelMessage
from localagentcli.models.detector import HardwareDetector, ModelDetector
from localagentcli.models.installer import ModelInstaller
from localagentcli.models.registry import ModelRegistry
from localagentcli.providers.base import RemoteProvider
from localagentcli.providers.keys import KeyManager
from localagentcli.providers.registry import ProviderRegistry
from localagentcli.session.manager import SessionManager
from localagentcli.session.state import Message
from localagentcli.shell.prompt import create_prompt_session
from localagentcli.shell.streaming import StreamRenderer
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

        # Initialize provider infrastructure
        self._key_manager = KeyManager(storage.secrets_dir)
        self._provider_registry = ProviderRegistry(config, self._key_manager)
        self._stream_renderer = StreamRenderer(self._console)
        self._active_provider: RemoteProvider | None = None
        self._active_provider_name: str = ""

        # Initialize model infrastructure
        self._model_registry = ModelRegistry(storage.registry_path)
        self._model_detector = ModelDetector()
        self._hardware_detector = HardwareDetector()
        self._model_installer = ModelInstaller(
            models_dir=storage.models_dir,
            cache_dir=storage.cache_dir,
            registry=self._model_registry,
            detector=self._model_detector,
            console=self._console,
        )
        self._active_backend: ModelBackend | None = None
        self._active_backend_model: str = ""

        # Initialize command router and register commands
        self._router = CommandRouter()
        self._register_commands()

        # Create prompt session with history and tab completion
        history_file = storage.cache_dir / "input_history"
        self._prompt_session = create_prompt_session(self._router, history_file)

    def _register_commands(self) -> None:
        """Register all command handlers."""
        help_cmd.register(self._router)
        status_cmd.register(self._router, self._session_manager, self._config)
        config_cmd.register(self._router, self._config)
        setup_cmd.register(self._router, self._config, self._session_manager, self._console)
        session_cmd.register(self._router, self._session_manager)
        exit_cmd.register(self._router)
        providers_cmd.register(
            self._router,
            self._provider_registry,
            self._key_manager,
            self._session_manager,
            self._console,
        )
        models_cmd.register(
            self._router,
            self._model_registry,
            self._model_installer,
            self._hardware_detector,
            self._session_manager,
            self._console,
            self._storage.models_dir,
        )

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
                    self._handle_plain_text(stripped)

            except KeyboardInterrupt:
                self._console.print()
                continue
            except EOFError:
                self._console.print()
                self._handle_exit()
                break

    def _handle_plain_text(self, text: str) -> None:
        """Handle plain text input — route to active model/provider or show message."""
        session = self._session_manager.current
        model_name = session.model
        provider_name = session.provider

        # Determine which backend to use
        backend: ModelBackend | None = None

        if model_name and not provider_name:
            # Local model — get or load the backend
            backend = self._get_active_backend(model_name)
            if backend is None:
                self._console.print(
                    f"[red]Failed to load model '{model_name}'. "
                    "Check /models inspect for details.[/red]"
                )
                return
        elif provider_name:
            # Remote provider
            backend = self._get_active_provider(provider_name)
            if backend is None:
                self._console.print(
                    f"[red]Failed to connect to provider '{provider_name}'. "
                    "Check /providers test.[/red]"
                )
                return
        else:
            self._console.print(
                "[dim]No model connected. Use /setup or configure a "
                "model/provider to start chatting.[/dim]"
            )
            return

        # Add user message to history
        session.history.append(Message(role="user", content=text, timestamp=datetime.now()))

        # Build model messages from session history
        model_messages = self._history_to_model_messages()

        # Stream the response
        try:
            chunks = backend.stream_generate(model_messages)
            response_text = self._stream_renderer.render_stream(chunks)
        except Exception as e:
            self._stream_renderer.render_error(str(e))
            return

        # Add assistant response to history
        if response_text:
            session.history.append(
                Message(role="assistant", content=response_text, timestamp=datetime.now())
            )

    def _get_active_provider(self, provider_name: str) -> RemoteProvider | None:
        """Get the active provider, caching the instance."""
        if self._active_provider and self._active_provider_name == provider_name:
            return self._active_provider
        try:
            self._active_provider = self._provider_registry.create_provider(provider_name)
            self._active_provider_name = provider_name
            return self._active_provider
        except Exception:
            self._active_provider = None
            self._active_provider_name = ""
            return None

    def _get_active_backend(self, model_name: str) -> ModelBackend | None:
        """Get the active local model backend, loading if needed."""
        if self._active_backend and self._active_backend_model == model_name:
            return self._active_backend

        # Parse name@version
        if "@" in model_name:
            name, version = model_name.rsplit("@", 1)
        else:
            name, version = model_name, None

        entry = self._model_registry.get_model(name, version)
        if entry is None:
            return None

        try:
            backend = self._create_backend(entry.format)
            from pathlib import Path

            backend.load(Path(entry.path))
            self._active_backend = backend
            self._active_backend_model = model_name
            return backend
        except Exception as e:
            self._logger.error("Failed to load model '%s': %s", model_name, e)
            self._active_backend = None
            self._active_backend_model = ""
            return None

    def _create_backend(self, fmt: str) -> ModelBackend:
        """Create the appropriate backend instance for a model format."""
        if fmt == "mlx":
            from localagentcli.models.backends.mlx import MLXBackend

            return MLXBackend()
        if fmt == "gguf":
            from localagentcli.models.backends.gguf import GGUFBackend

            return GGUFBackend()
        if fmt == "safetensors":
            from localagentcli.models.backends.safetensors import SafetensorsBackend

            return SafetensorsBackend()
        raise ValueError(f"Unknown model format: '{fmt}'")

    def _history_to_model_messages(self) -> list[ModelMessage]:
        """Convert session history to ModelMessage list for the provider."""
        session = self._session_manager.current
        return [
            ModelMessage(role=msg.role, content=msg.content)
            for msg in session.history
            if not msg.is_summary
        ]

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
