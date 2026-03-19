"""/set command handler — unified local/provider model selection."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from rich.console import Console

from localagentcli.commands.models import _activate_model_entry, build_model_selection_options
from localagentcli.commands.providers import (
    build_provider_selection_options,
    build_remote_model_selection_options,
)
from localagentcli.commands.router import CommandHandler, CommandResult, CommandRouter, CommandSpec
from localagentcli.config.manager import ConfigManager
from localagentcli.models.detector import HardwareDetector
from localagentcli.models.registry import ModelRegistry
from localagentcli.providers.registry import ProviderRegistry
from localagentcli.session.manager import SessionManager
from localagentcli.shell.prompt import SelectionOption, select_option, supports_interactive_prompt

Selector = Callable[[str, Sequence[SelectionOption], str | None], SelectionOption | None]


class SetHandler(CommandHandler):
    """Pick the active local model or remote provider model."""

    def __init__(
        self,
        model_registry: ModelRegistry,
        provider_registry: ProviderRegistry,
        hardware_detector: HardwareDetector,
        config: ConfigManager,
        session_manager: SessionManager,
        console: Console,
        selector: Selector | None = None,
        *,
        persist_default: bool = False,
    ):
        self._model_registry = model_registry
        self._provider_registry = provider_registry
        self._hardware_detector = hardware_detector
        self._config = config
        self._session_manager = session_manager
        self._console = console
        self._selector = selector or _prompt_selector
        self._persist_default = persist_default

    def execute(self, args: list[str]) -> CommandResult:
        if args:
            return CommandResult.error(
                f"{self._usage()} does not accept arguments.\nUsage: {self._usage()}"
            )
        if not supports_interactive_prompt():
            return CommandResult.ok(
                "Interactive target picker requires a terminal TTY.",
                presentation="status",
                body=self.help_text(),
            )

        target_type = self._choose_target_type()
        if target_type is None:
            return CommandResult.ok("Target selection cancelled.", presentation="warning")
        if target_type == "local":
            return self._choose_local_model()
        if target_type == "provider":
            return self._choose_provider_model()
        return CommandResult.error(f"Unknown target type '{target_type}'.")

    def describe(self) -> CommandSpec:
        if self._persist_default:
            return CommandSpec(
                group="Target",
                summary="Choose the default CLI target for new sessions.",
                usage="/set default",
                details=(
                    "Pick Local models or Providers, then complete the layered selection flow "
                    "to persist the startup target."
                ),
            )
        return CommandSpec(
            group="Target",
            summary="Choose the active local model or remote provider model.",
            usage="/set",
            details=(
                "Pick Local models or Providers, then complete the layered selection flow "
                "for the current session."
            ),
        )

    def _choose_target_type(self) -> str | None:
        selection = self._selector(
            "Choose what to activate",
            [
                SelectionOption(
                    value="provider",
                    label="Providers",
                    description="Use a configured remote provider and choose one of its models.",
                    aliases=("remote", "cloud"),
                ),
                SelectionOption(
                    value="local",
                    label="Local models",
                    description="Use one installed local model.",
                    aliases=("local", "downloaded"),
                ),
            ],
            None,
        )
        return selection.value if selection is not None else None

    def _choose_local_model(self) -> CommandResult:
        options = build_model_selection_options(self._model_registry)
        if not options:
            return CommandResult.ok(
                "No models installed. Use /models install to add one.",
                presentation="status",
            )

        selection = self._selector("Choose a local model", options, None)
        if selection is None:
            return CommandResult.ok("Target selection cancelled.", presentation="warning")

        name, version = _parse_name_version(selection.value)
        entry = self._model_registry.get_model(name, version)
        if entry is None:
            return CommandResult.error(
                f"Model '{selection.value}' not found.\nUse /models list to see installed models."
            )
        result = _activate_model_entry(
            entry, self._hardware_detector, self._session_manager, self._console
        )
        if self._persist_default:
            self._persist_target("", selection.value)
            return CommandResult.ok(
                f"Default model set to '{selection.value}'.",
                presentation="success",
            )
        return result

    def _choose_provider_model(self) -> CommandResult:
        provider_options = build_provider_selection_options(self._provider_registry)
        if not provider_options:
            return CommandResult.ok(
                "No providers configured. Use /providers add to set one up.",
                presentation="status",
            )

        selection = self._selector(
            "Choose a provider",
            provider_options,
            self._session_manager.current.provider or None,
        )
        if selection is None:
            return CommandResult.ok("Target selection cancelled.", presentation="warning")

        entry = self._provider_registry.get(selection.value)
        if entry is None:
            return CommandResult.error(
                f"Provider '{selection.value}' not found.\n"
                "Use /providers list to see configured providers."
            )

        try:
            provider = self._provider_registry.create_provider(entry.name)
        except Exception as exc:
            return CommandResult.error(f"Failed to connect to provider '{entry.name}': {exc}")

        try:
            model_options = build_remote_model_selection_options(provider)
        except Exception as exc:
            return CommandResult.error(f"Failed to list models from provider '{entry.name}': {exc}")
        finally:
            try:
                provider.close()
            except Exception:
                pass
        if not model_options:
            return CommandResult.error(
                f"No models available from provider '{entry.name}'. Check /providers test."
            )

        model_selection = self._selector(
            "Choose a provider model",
            model_options,
            None,
        )
        if model_selection is None:
            return CommandResult.ok("Target selection cancelled.", presentation="warning")

        session = self._session_manager.current
        session.provider = entry.name
        session.model = model_selection.value
        session.touch()
        if self._persist_default:
            self._persist_target(entry.name, model_selection.value)
        message = (
            f"{self._target_label()} provider set to '{entry.name}' "
            f"(model: {model_selection.value})."
        )
        return CommandResult.ok(message, presentation="success")

    def _persist_target(self, provider_name: str, model_name: str) -> None:
        """Persist the selected target as the CLI default."""
        self._config.set("provider.active_provider", provider_name)
        self._config.set("model.active_model", model_name)

    def _target_label(self) -> str:
        """Return a user-facing label for the handler scope."""
        return "Default" if self._persist_default else "Active"

    def _usage(self) -> str:
        """Return the correct usage string for this handler."""
        return "/set default" if self._persist_default else "/set"


def register(
    router: CommandRouter,
    model_registry: ModelRegistry,
    provider_registry: ProviderRegistry,
    hardware_detector: HardwareDetector,
    config: ConfigManager,
    session_manager: SessionManager,
    console: Console,
) -> None:
    """Register the /set command."""
    router.register(
        "set",
        SetHandler(
            model_registry,
            provider_registry,
            hardware_detector,
            config,
            session_manager,
            console,
        ),
    )
    router.register(
        "set default",
        SetHandler(
            model_registry,
            provider_registry,
            hardware_detector,
            config,
            session_manager,
            console,
            persist_default=True,
        ),
    )


def _prompt_selector(
    message: str,
    options: Sequence[SelectionOption],
    default: str | None = None,
) -> SelectionOption | None:
    """Use the shared prompt-toolkit picker for target selection."""
    return select_option(message, options, default=default)


def _parse_name_version(value: str) -> tuple[str, str | None]:
    """Parse a model identifier in name@version form."""
    if "@" in value:
        name, version = value.rsplit("@", 1)
        return name, version
    return value, None
