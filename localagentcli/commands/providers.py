"""/providers command handlers — add, list, remove, use, test."""

from __future__ import annotations

from rich.console import Console
from rich.prompt import Confirm, Prompt

from localagentcli.commands.router import CommandHandler, CommandResult, CommandRouter
from localagentcli.providers.keys import KeyManager
from localagentcli.providers.registry import ProviderEntry, ProviderRegistry
from localagentcli.session.manager import SessionManager

# Default URLs and models per provider type
_TYPE_DEFAULTS: dict[str, dict[str, str]] = {
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o",
    },
    "anthropic": {
        "base_url": "https://api.anthropic.com",
        "default_model": "claude-sonnet-4-20250514",
    },
    "rest": {
        "base_url": "http://localhost:8000",
        "default_model": "default",
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
            "  /providers use <name>       Set active provider for session\n"
            "  /providers test [name]      Test provider connectivity"
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
        lines.append(f"  {'Name':<20s} {'Type':<12s} {'Default Model':<25s} {'Status'}")
        lines.append(f"  {'─' * 20} {'─' * 12} {'─' * 25} {'─' * 12}")
        for entry in entries:
            marker = " *" if entry.name == active else ""
            lines.append(
                f"  {entry.name:<20s} {entry.type:<12s} "
                f"{entry.default_model:<25s} {entry.status}{marker}"
            )
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

            default_model = Prompt.ask(
                "Default model",
                default=defaults["default_model"],
                console=self._console,
            )

            entry = ProviderEntry(
                name=name,
                type=ptype,
                base_url=base_url,
                default_model=default_model,
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
            return CommandResult.error("Provider name required.\nUsage: /providers remove <name>")
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
            return CommandResult.error("Provider name required.\nUsage: /providers use <name>")
        name = args[0]
        entry = self._registry.get(name)
        if entry is None:
            return CommandResult.error(
                f"Provider '{name}' not found.\nUse /providers list to see configured providers."
            )
        # Session-only override (not persisted to config)
        session = self._session_manager.current
        session.provider = name
        session.model = entry.default_model
        return CommandResult.ok(f"Active provider set to '{name}' (model: {entry.default_model}).")

    def help_text(self) -> str:
        return "Set the active provider for this session.\nUsage: /providers use <name>"


class ProvidersTestHandler(CommandHandler):
    """Test connectivity to a provider."""

    def __init__(self, registry: ProviderRegistry, session_manager: SessionManager):
        self._registry = registry
        self._session_manager = session_manager

    def execute(self, args: list[str]) -> CommandResult:
        if args:
            name = args[0]
        else:
            # Use session provider or global active
            name = self._session_manager.current.provider
            if not name:
                name = self._registry.get_active_name()
            if not name:
                return CommandResult.error(
                    "No active provider. Specify a name or use /providers use first."
                )

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
    router.register("providers", ProvidersParentHandler())
    router.register("providers list", ProvidersListHandler(registry))
    router.register("providers add", ProvidersAddHandler(registry, key_manager, console))
    router.register("providers remove", ProvidersRemoveHandler(registry))
    router.register("providers use", ProvidersUseHandler(registry, session_manager))
    router.register("providers test", ProvidersTestHandler(registry, session_manager))
