"""/providers command handlers — add, list, remove, use, test."""

from __future__ import annotations

from rich.console import Console

from localagentcli.commands.router import CommandHandler, CommandResult, CommandRouter, CommandSpec
from localagentcli.providers.base import RemoteProvider
from localagentcli.providers.keys import KeyManager
from localagentcli.providers.registry import ProviderEntry, ProviderRegistry
from localagentcli.session.manager import SessionManager
from localagentcli.shell.prompt import (
    SelectionOption,
    confirm_choice,
    prompt_secret,
    prompt_text,
    select_option,
    supports_interactive_prompt,
)

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

    def describe(self) -> CommandSpec:
        return CommandSpec(
            group="Provider",
            summary="Manage remote providers.",
            usage="/providers <list|add|remove|use|test>",
            argument_hint="<subcommand>",
            details="Use /set to choose the active provider model.",
        )


class ProvidersListHandler(CommandHandler):
    """List all configured providers."""

    def __init__(self, registry: ProviderRegistry):
        self._registry = registry

    def execute(self, args: list[str]) -> CommandResult:
        entries = self._registry.list_providers()
        if not entries:
            return CommandResult.ok(
                "No providers configured. Use /providers add to set one up.",
                presentation="status",
            )

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

    def describe(self) -> CommandSpec:
        return CommandSpec(
            group="Provider",
            summary="List configured remote providers.",
            usage="/providers list",
        )


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
        ptype_selection = select_option(
            "Choose a provider type",
            [
                SelectionOption(value="openai", label="openai"),
                SelectionOption(value="anthropic", label="anthropic"),
                SelectionOption(value="rest", label="rest"),
            ],
            default="openai",
        )
        if ptype_selection is None:
            return CommandResult.ok("Provider setup cancelled.", presentation="warning")
        ptype = ptype_selection.value
        defaults = _TYPE_DEFAULTS.get(ptype, _TYPE_DEFAULTS["rest"])

        name = prompt_text("Provider name", default=ptype)
        if name is None:
            return CommandResult.ok("Provider setup cancelled.", presentation="warning")

        if self._registry.get(name) is not None:
            return CommandResult.error(
                f"Provider '{name}' already exists. "
                "Use /providers remove first, or choose a different name."
            )

        base_url = prompt_text("Base URL", default=defaults["base_url"])
        if base_url is None:
            return CommandResult.ok("Provider setup cancelled.", presentation="warning")

        api_key = prompt_secret("API key")
        if api_key is None:
            return CommandResult.ok("Provider setup cancelled.", presentation="warning")
        if not api_key:
            return CommandResult.error("API key is required.")

        entry = ProviderEntry(
            name=name,
            type=ptype,
            base_url=base_url,
        )
        self._registry.add(entry, api_key)

        body: str | None = None
        test_now = confirm_choice("Test connection now?", default=True)
        if test_now is None:
            return CommandResult.ok(
                f"Provider '{name}' added.",
                presentation="success",
                body="Connection test skipped.",
            )
        if test_now:
            body = self._test_provider(name)

        return CommandResult.ok(
            f"Provider '{name}' added.",
            presentation="success",
            body=body,
        )

    def describe(self) -> CommandSpec:
        return CommandSpec(
            group="Provider",
            summary="Add a new remote provider with an interactive wizard.",
            usage="/providers add",
        )

    def _test_provider(self, name: str) -> str:
        """Run the optional provider connectivity test and summarize the result."""
        provider = None
        try:
            provider = self._registry.create_provider(name)
            result = provider.test_connection()
        except Exception as exc:
            return f"Connection test failed: {exc}"
        finally:
            try:
                if provider is not None:
                    provider.close()
            except Exception:
                pass

        if result.success:
            self._registry.update_status(name, "tested")
            return f"Connection test passed: {result.message} ({result.latency_ms:.0f}ms)"
        return f"Connection test failed: {result.message}"


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
                    "No providers configured. Use /providers add to set one up.",
                    presentation="status",
                )
            selection = _select_provider_option(
                self._registry,
                "Choose a provider to remove",
            )
            if selection is None:
                return CommandResult.ok("Provider removal cancelled.", presentation="warning")
            args = [selection.value]
        name = args[0]
        if supports_interactive_prompt():
            confirmed = confirm_choice(f"Remove provider '{name}'?", default=False)
            if confirmed is None or not confirmed:
                return CommandResult.ok("Provider removal cancelled.", presentation="warning")
        try:
            self._registry.remove(name)
            return CommandResult.ok(f"Provider '{name}' removed.", presentation="success")
        except KeyError:
            return CommandResult.error(
                f"Provider '{name}' not found.\nUse /providers list to see configured providers."
            )

    def describe(self) -> CommandSpec:
        return CommandSpec(
            group="Provider",
            summary="Remove a configured provider.",
            usage="/providers remove <name>",
            argument_hint="[name]",
        )


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
                    "No providers configured. Use /providers add to set one up.",
                    presentation="status",
                )
            selection = _select_provider_option(
                self._registry,
                "Choose a provider",
                default=self._session_manager.current.provider,
            )
            if selection is None:
                return CommandResult.ok("Provider selection cancelled.", presentation="warning")
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
            return CommandResult.ok(
                f"Active provider set to '{name}' (model: {session.model}).",
                presentation="success",
            )
        return CommandResult.ok(
            f"Active provider set to '{name}'. Use /set to choose a provider model.",
            presentation="success",
        )

    def describe(self) -> CommandSpec:
        return CommandSpec(
            group="Provider",
            summary="Set the active provider for this session.",
            usage="/providers use <name>",
            argument_hint="[name]",
            details="Prefer /set for interactive target selection.",
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
                        "No providers configured. Use /providers add to set one up.",
                        presentation="status",
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
                    return CommandResult.ok("Provider test cancelled.", presentation="warning")
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
                f"Provider '{name}': {result.message} ({result.latency_ms:.0f}ms)",
                presentation="success",
            )
        return CommandResult.error(f"Provider '{name}': {result.message}")

    def describe(self) -> CommandSpec:
        return CommandSpec(
            group="Provider",
            summary="Test connectivity to a provider.",
            usage="/providers test [name]",
            argument_hint="[name]",
            details="Without a name, tests the active provider.",
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
