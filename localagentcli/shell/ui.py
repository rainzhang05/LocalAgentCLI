"""ShellUI — main input loop, rendering, and lifecycle management."""

from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.prompt import Confirm

from localagentcli.agents.chat import ChatController
from localagentcli.commands import config_cmd, exit_cmd, setup_cmd
from localagentcli.commands import help as help_cmd
from localagentcli.commands import mode as mode_cmd
from localagentcli.commands import models as models_cmd
from localagentcli.commands import providers as providers_cmd
from localagentcli.commands import session as session_cmd
from localagentcli.commands import status as status_cmd
from localagentcli.commands.router import CommandResult, CommandRouter
from localagentcli.config.manager import ConfigManager
from localagentcli.models.abstraction import ModelAbstractionLayer
from localagentcli.models.backends.base import ModelBackend
from localagentcli.models.detector import HardwareDetector, ModelDetector
from localagentcli.models.installer import ModelInstaller
from localagentcli.models.registry import ModelRegistry
from localagentcli.providers.base import RemoteProvider
from localagentcli.providers.keys import KeyManager
from localagentcli.providers.registry import ProviderRegistry
from localagentcli.session.manager import SessionManager
from localagentcli.shell.prompt import create_prompt_session, get_prompt_history_strings
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

        self._logger = Logger(
            storage.logs_dir,
            config.get("general.logging_level", "normal"),
        )

        self._session_manager = SessionManager(storage.sessions_dir, config)
        self._session_manager.new_session()

        self._key_manager = KeyManager(storage.secrets_dir)
        self._provider_registry = ProviderRegistry(config, self._key_manager)
        self._stream_renderer = StreamRenderer(self._console)
        self._active_provider: RemoteProvider | None = None
        self._active_provider_name = ""

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
        self._active_backend_model = ""

        self._router = CommandRouter()
        self._register_commands()
        self._prompt_session = create_prompt_session(
            self._router,
            self._session_prompt_history(),
        )

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
        mode_cmd.register(
            self._router,
            self._session_manager,
            self._model_registry,
            self._provider_registry,
        )

    def run(self) -> None:
        """Main input loop."""
        self._logger.normal("Session started (id: %s)", self._session_manager.current.id)

        self._display_welcome()
        if self._first_run:
            self._run_first_time_setup()

        try:
            self._storage.cleanup_cache()
            self._storage.cleanup_logs()
        except Exception:
            pass

        while True:
            try:
                self._display_status_header()
                user_input = self._prompt_session.prompt("> ")
                if not user_input.strip():
                    continue

                stripped = user_input.strip()
                self._sync_prompt_history_to_session()

                if stripped.startswith("/"):
                    result = self._router.dispatch(stripped[1:])
                    self._render_command_result(result)

                    action = result.data.get("action") if result.data else None
                    if action == "session_changed":
                        self._rebuild_prompt_session()
                    if action == "exit":
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
        """Handle plain text input according to the current session mode."""
        model = self._resolve_active_model()
        if model is None:
            self._console.print(
                "[dim]No model connected. Use /setup or configure a "
                "model/provider to start chatting.[/dim]"
            )
            return

        session = self._session_manager.current
        if session.mode == "agent":
            self._stream_renderer.render_activity(
                "Agent mode orchestration arrives in Phase 5. Streaming this request directly."
            )

        controller = ChatController(
            model=model,
            session=session,
            context_limit=self._context_limit(),
        )
        chunks = controller.handle_input(text, generation_options=self._generation_options())
        if controller.last_compaction_count:
            self._stream_renderer.render_activity(
                f"Context compacted: summarized {controller.last_compaction_count} messages"
            )

        try:
            self._stream_renderer.render_stream(chunks)
        except Exception as exc:
            self._stream_renderer.render_error(str(exc))

    def _resolve_active_model(self) -> ModelAbstractionLayer | None:
        """Resolve the active local backend or remote provider into a model abstraction."""
        session = self._session_manager.current
        backend: ModelBackend | None = None

        if session.model and not session.provider:
            backend = self._get_active_backend(session.model)
            if backend is None:
                self._console.print(
                    f"[red]Failed to load model '{session.model}'. "
                    "Check /models inspect for details.[/red]"
                )
                return None
        elif session.provider:
            backend = self._get_active_provider(session.provider)
            if backend is None:
                self._console.print(
                    f"[red]Failed to connect to provider '{session.provider}'. "
                    "Check /providers test.[/red]"
                )
                return None

        if backend is None:
            return None
        return ModelAbstractionLayer(backend)

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
        """Get the active local model backend, loading it if needed."""
        if self._active_backend and self._active_backend_model == model_name:
            return self._active_backend

        if self._active_backend is not None:
            try:
                self._active_backend.unload()
            except Exception:
                pass
            self._active_backend = None
            self._active_backend_model = ""

        name, version = self._parse_name_version(model_name)
        entry = self._model_registry.get_model(name, version)
        if entry is None:
            return None

        try:
            backend = self._create_backend(entry.format)
            backend.load(Path(entry.path))
            self._active_backend = backend
            self._active_backend_model = model_name
            return backend
        except Exception as exc:
            self._logger.error("Failed to load model '%s': %s", model_name, exc)
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
        workspace = self._abbreviate_home(session.workspace)
        self._console.print(
            f"[dim]LocalAgent | mode: {session.mode} | model: {self._active_target_label()} "
            f"| workspace: {workspace}[/dim]"
        )

    def _render_command_result(self, result: CommandResult) -> None:
        """Render a command result to the console."""
        if result.success:
            if result.message and result.message != "exit":
                self._console.print(result.message)
            return
        self._console.print(f"[red]✗ {result.message}[/red]")

    def _handle_exit(self) -> None:
        """Handle clean shutdown with optional session save."""
        self._sync_prompt_history_to_session()
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

        if self._active_backend is not None:
            try:
                self._active_backend.unload()
            except Exception:
                pass

        self._logger.normal("Session ended")
        self._console.print("[dim]Goodbye.[/dim]")

    def _generation_options(self) -> dict[str, object]:
        """Build generation options from the effective configuration."""
        return {
            "temperature": self._session_manager.get_effective_config("generation.temperature")
            or 0.7,
            "max_tokens": self._session_manager.get_effective_config("generation.max_tokens")
            or 4096,
            "top_p": self._session_manager.get_effective_config("generation.top_p") or 1.0,
        }

    def _context_limit(self) -> int:
        """Return the best-known context limit for the active target."""
        session = self._session_manager.current
        if session.model and not session.provider:
            name, version = self._parse_name_version(session.model)
            entry = self._model_registry.get_model(name, version)
            if entry is not None:
                for key in ("context_length", "context_window", "n_ctx"):
                    value = entry.metadata.get(key)
                    if isinstance(value, int) and value > 0:
                        return value
        return 8192

    def _active_target_label(self) -> str:
        """Describe the active local model or remote provider for the status header."""
        session = self._session_manager.current
        if session.provider:
            model_name = session.model or "remote"
            return f"{session.provider} ({model_name})"
        if session.model:
            name, version = self._parse_name_version(session.model)
            entry = self._model_registry.get_model(name, version)
            if entry is not None:
                return f"{session.model} ({entry.format})"
            return session.model
        return "(none)"

    def _session_prompt_history(self) -> list[str]:
        """Load persisted prompt history from the current session metadata."""
        history = self._session_manager.current.metadata.get("input_history", [])
        if not isinstance(history, list):
            return []
        return [item for item in history if isinstance(item, str)]

    def _sync_prompt_history_to_session(self) -> None:
        """Persist the current prompt history back into the active session metadata."""
        session = self._session_manager.current
        history = get_prompt_history_strings(self._prompt_session)
        if session.metadata.get("input_history") != history:
            session.metadata["input_history"] = history

    def _rebuild_prompt_session(self) -> None:
        """Rebuild the prompt session after a session switch."""
        self._prompt_session = create_prompt_session(
            self._router,
            self._session_prompt_history(),
        )

    def _abbreviate_home(self, path: str) -> str:
        """Display home-relative paths more compactly."""
        try:
            home = str(Path.home())
            if path.startswith(home):
                return "~" + path[len(home) :]
        except Exception:
            pass
        return path

    def _parse_name_version(self, model_name: str) -> tuple[str, str | None]:
        """Parse a stored model reference in name@version form."""
        if "@" in model_name:
            name, version = model_name.rsplit("@", 1)
            return name, version
        return model_name, None
