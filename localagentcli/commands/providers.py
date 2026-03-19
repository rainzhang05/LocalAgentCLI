"""/providers command handlers — add, list, remove, use, test."""

from __future__ import annotations

from rich.console import Console
from rich.prompt import Confirm, Prompt

from localagentcli.commands.router import CommandHandler, CommandResult, CommandRouter
from localagentcli.providers.base import RemoteProvider
from localagentcli.providers.keys import KeyManager
from localagentcli.providers.registry import ProviderEntry, ProviderRegistry
from localagentcli.session.manager import SessionManager
from localagentcli.shell.prompt import SelectionOption, select_option, supports_interactive_prompt

# Default URLs per provider type
_TYPE_DEFAULTS: dict[str, dict[str, str]] = {
    "openai": {
        "base_url": "https://api.openai.com/v1",
    },
    "anthropic": {
        "base_url": "https://api.anthropic.com",
    },
    "rest": {
        "base_url": "http://localhost:8000",
    },
}


class ProvidersParentHandler(CommandHandler):
    """Parent handler that shows subcommand help."""

    def execute(self, args: list[str]) -> CommandResult:
        return CommandResult.error("/providers requires a subcommand: list, add, remove, use, test")

    def help_text(self) -> str:
        return (
            "Manage remote providers.\n"
            "Subcommands:\n"
            "  /providers list             List configured providers\n"
            "  /providers add              Add a new provider (wizard)\n"
            "  /providers remove <name>    Remove a provider\n"
            "  /providers test [name]      Test provider connectivity\n"
            "Use /set to choose the active provider model."
        )


class ProvidersListHandler(CommandHandler):
    """List all configured providers."""

    def __init__(self, registry: ProviderRegistry):
        self._registry = registry

    def execute(self, args: list[str]) -> CommandResult:
        entries = self._registry.list_providers()
        if not entries:
            return CommandResult.ok("No providers configured. Use /providers add to set one up.")

        active = self._registry.get_active_name()
        lines = ["Configured providers:", ""]
        lines.append(f"  {'Name':<20s} {'Type':<12s} {'Status'}")
        lines.append(f"  {'─' * 20} {'─' * 12} {'─' * 12}")
        for entry in entries:
            marker = " *" if entry.name == active else ""
            lines.append(f"  {entry.name:<20s} {entry.type:<12s} {entry.status}{marker}")
        if active:
            lines.append(f"\n  * = active provider ({active})")
        return CommandResult.ok("\n".join(lines))

    def help_text(self) -> str:
        return "List all configured remote providers.\nUsage: /providers list"


class ProvidersAddHandler(CommandHandler):
    """Interactive wizard to add a new provider."""

    def __init__(
        self,
        registry: ProviderRegistry,
        key_manager: KeyManager,
        console: Console,
    ):
        self._registry = registry
        self._key_manager = key_manager
        self._console = console

    def execute(self, args: list[str]) -> CommandResult:
        self._console.print()
        self._console.print("[bold]Add Provider[/bold]")
        self._console.print()

        try:
            ptype = Prompt.ask(
                "Provider type",
                choices=["openai", "anthropic", "rest"],
                default="openai",
                console=self._console,
            )
            defaults = _TYPE_DEFAULTS.get(ptype, _TYPE_DEFAULTS["rest"])

            name = Prompt.ask(
                "Provider name",
                default=ptype,
                console=self._console,
            )

            # Check if name already exists
            if self._registry.get(name) is not None:
                return CommandResult.error(
                    f"Provider '{name}' already exists. "
                    "Use /providers remove first, or choose a different name."
                )

            base_url = Prompt.ask(
                "Base URL",
                default=defaults["base_url"],
                console=self._console,
            )

            api_key = Prompt.ask("API key", console=self._console)
            if not api_key:
                return CommandResult.error("API key is required.")

            entry = ProviderEntry(
                name=name,
                type=ptype,
                base_url=base_url,
            )
            self._registry.add(entry, api_key)

            # Optionally test connection
            test_now = Confirm.ask(
                "Test connection now?",
                default=True,
                console=self._console,
            )
            if test_now:
                try:
                    provider = self._registry.create_provider(name)
                    result = provider.test_connection()
                    if result.success:
                        self._registry.update_status(name, "tested")
                        self._console.print(
                            f"[green]✓ {result.message} ({result.latency_ms:.0f}ms)[/green]"
                        )
                    else:
                        self._console.print(f"[yellow]⚠ {result.message}[/yellow]")
                except Exception as e:
                    self._console.print(f"[yellow]⚠ Test failed: {e}[/yellow]")

            self._console.print()
            return CommandResult.ok(f"Provider '{name}' added successfully.")
        except (KeyboardInterrupt, EOFError):
            return CommandResult.ok("Provider setup cancelled.")

    def help_text(self) -> str:
        return "Add a new remote provider (interactive wizard).\nUsage: /providers add"


class ProvidersRemoveHandler(CommandHandler):
    """Remove a configured provider."""

    def __init__(self, registry: ProviderRegistry):
        self._registry = registry

    def execute(self, args: list[str]) -> CommandResult:
        if not args:
            if not supports_interactive_prompt():
                return CommandResult.error(
                    "Provider name required.\nUsage: /providers remove <name>"
                )
            if not self._registry.list_providers():
                return CommandResult.ok(
                    "No providers configured. Use /providers add to set one up."
                )
            selection = _select_provider_option(
                self._registry,
                "Choose a provider to remove",
            )
            if selection is None:
                return CommandResult.ok("Provider removal cancelled.")
            args = [selection.value]
        name = args[0]
        try:
            self._registry.remove(name)
            return CommandResult.ok(f"Provider '{name}' removed.")
        except KeyError:
            return CommandResult.error(
                f"Provider '{name}' not found.\nUse /providers list to see configured providers."
            )

    def help_text(self) -> str:
        return "Remove a configured provider.\nUsage: /providers remove <name>"


class ProvidersUseHandler(CommandHandler):
    """Set the active provider for the current session."""

    def __init__(self, registry: ProviderRegistry, session_manager: SessionManager):
        self._registry = registry
        self._session_manager = session_manager

    def execute(self, args: list[str]) -> CommandResult:
        if not args:
            if not supports_interactive_prompt():
                return CommandResult.error("Provider name required.\nUsage: /providers use <name>")
            if not self._registry.list_providers():
                return CommandResult.ok(
                    "No providers configured. Use /providers add to set one up."
                )
            selection = _select_provider_option(
                self._registry,
                "Choose a provider",
                default=self._session_manager.current.provider,
            )
            if selection is None:
                return CommandResult.ok("Provider selection cancelled.")
            args = [selection.value]
        name = args[0]
        entry = self._registry.get(name)
        if entry is None:
            return CommandResult.error(
                f"Provider '{name}' not found.\nUse /providers list to see configured providers."
            )
        # Session-only override (not persisted to config)
        session = self._session_manager.current
        session.provider = name
        session.model = ""
        try:
            provider = self._registry.create_provider(name)
            models = provider.list_models()
        except Exception:
            models = []
        else:
            try:
                provider.close()
            except Exception:
                pass
        if models:
            session.model = models[0].id or models[0].name
        session.touch()
        if session.model:
            return CommandResult.ok(f"Active provider set to '{name}' (model: {session.model}).")
        return CommandResult.ok(
            f"Active provider set to '{name}'. Use /set to choose a provider model."
        )

    def help_text(self) -> str:
        return (
            "Set the active provider for this session.\n"
            "Usage: /providers use <name>\n"
            "Prefer /set for interactive target selection."
        )


class ProvidersTestHandler(CommandHandler):
    """Test connectivity to a provider."""

    def __init__(self, registry: ProviderRegistry, session_manager: SessionManager):
        self._registry = registry
        self._session_manager = session_manager

    def execute(self, args: list[str]) -> CommandResult:
        if args:
            name = args[0]
        else:
            if not supports_interactive_prompt():
                name = self._session_manager.current.provider
                if not name:
                    name = self._registry.get_active_name()
                if not name:
                    return CommandResult.error(
                        "No active provider. Specify a name or use /set first."
                    )
            else:
                if not self._registry.list_providers():
                    return CommandResult.ok(
                        "No providers configured. Use /providers add to set one up."
                    )
                default_name = (
                    self._session_manager.current.provider or self._registry.get_active_name()
                )
                selection = _select_provider_option(
                    self._registry,
                    "Choose a provider to test",
                    default=default_name,
                )
                if selection is None:
                    return CommandResult.ok("Provider test cancelled.")
                name = selection.value

        entry = self._registry.get(name)
        if entry is None:
            return CommandResult.error(f"Provider '{name}' not found.")

        try:
            provider = self._registry.create_provider(name)
            result = provider.test_connection()
        except Exception as e:
            return CommandResult.error(f"Failed to create provider: {e}")

        if result.success:
            self._registry.update_status(name, "tested")
            return CommandResult.ok(
                f"✓ Provider '{name}': {result.message} ({result.latency_ms:.0f}ms)"
            )
        return CommandResult.error(f"Provider '{name}': {result.message}")

    def help_text(self) -> str:
        return (
            "Test connectivity to a provider.\n"
            "Usage: /providers test [name]\n"
            "Without a name, tests the active provider."
        )


def register(
    router: CommandRouter,
    registry: ProviderRegistry,
    key_manager: KeyManager,
    session_manager: SessionManager,
    console: Console,
) -> None:
    """Register all /providers subcommands."""
    router.register("providers", ProvidersParentHandler(), visible_in_menu=False)
    router.register("providers list", ProvidersListHandler(registry))
    router.register("providers add", ProvidersAddHandler(registry, key_manager, console))
    router.register("providers remove", ProvidersRemoveHandler(registry))
    router.register(
        "providers use",
        ProvidersUseHandler(registry, session_manager),
        visible_in_menu=False,
    )
    router.register("providers test", ProvidersTestHandler(registry, session_manager))


def build_provider_selection_options(registry: ProviderRegistry) -> list[SelectionOption]:
    """Build interactive selection options for configured providers."""
    options: list[SelectionOption] = []
    for entry in registry.list_providers():
        options.append(
            SelectionOption(
                value=entry.name,
                label=entry.name,
                description=f"{entry.type} • {entry.base_url}",
                aliases=(entry.type, entry.base_url),
            )
        )
    return options


def build_remote_model_selection_options(
    provider: RemoteProvider,
) -> list[SelectionOption]:
    """Build interactive selection options for models available from a provider."""
    discovered = provider.list_models()

    options: list[SelectionOption] = []
    seen: set[str] = set()
    for model in discovered:
        model_id = model.id or model.name
        if not model_id or model_id in seen:
            continue
        seen.add(model_id)
        capabilities = model.capabilities or {}
        flags = [
            label
            for key, label in (
                ("tool_use", "tools"),
                ("reasoning", "reasoning"),
                ("streaming", "streaming"),
            )
            if capabilities.get(key)
        ]
        description = ", ".join(flags) if flags else "remote model"
        options.append(
            SelectionOption(
                value=model_id,
                label=model.name or model_id,
                description=description,
                aliases=(model_id,),
            )
        )
    return options


def _select_provider_option(
    registry: ProviderRegistry,
    message: str,
    *,
    default: str | None = None,
) -> SelectionOption | None:
    """Prompt for one configured provider."""
    options = build_provider_selection_options(registry)
    if not options:
        return None
    return select_option(message, options, default=default)
