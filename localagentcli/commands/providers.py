"""/providers command handlers — add, list, remove, use, test."""

from __future__ import annotations

from rich.console import Console

from localagentcli.commands.router import CommandHandler, CommandResult, CommandRouter, CommandSpec
from localagentcli.config.manager import ConfigManager
from localagentcli.models.provider_readiness import resolve_remote_model_readiness
from localagentcli.models.readiness import (
    build_target_readiness,
    format_capability_brief,
    format_readiness_tradeoff,
    readiness_posture_label,
    selection_state_label,
    unknown_capability_provenance,
)
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
        return CommandResult.error(
            "/providers requires a subcommand: list, add, remove, use, test. "
            "Use /help providers for details."
        )

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

    def __init__(
        self,
        registry: ProviderRegistry,
        session_manager: SessionManager | None = None,
        config: ConfigManager | None = None,
    ):
        self._registry = registry
        self._session_manager = session_manager
        self._config = config

    def execute(self, args: list[str]) -> CommandResult:
        entries = self._registry.list_providers()
        if not entries:
            return CommandResult.ok(
                "No providers configured. Use /providers add to set one up.",
                presentation="status",
            )

        active = self._registry.get_active_name()
        lines = ["Configured providers:", ""]
        lines.append(f"  {'Name':<20s} {'Type':<12s} {'Status':<12s} {'Model':<24s} Readiness")
        lines.append(f"  {'─' * 20} {'─' * 12} {'─' * 12} {'─' * 24} {'─' * 18}")
        for entry in entries:
            marker = " *" if entry.name == active else ""
            selected_model = self._selected_model_for_provider(entry.name)
            model_label = selected_model or "-"
            readiness_label = "model unselected"
            if selected_model:
                readiness = self._provider_readiness(entry.name, selected_model)
                readiness_label = (
                    f"{selection_state_label(readiness.selection_state)} "
                    f"[{readiness_posture_label(readiness)}]"
                )
            lines.append(
                f"  {entry.name:<20s} {entry.type:<12s} {entry.status:<12s} "
                f"{model_label:<24s} {readiness_label}{marker}"
            )
        if active:
            lines.append(f"\n  * = active provider ({active})")
        return CommandResult.ok("\n".join(lines))

    def describe(self) -> CommandSpec:
        return CommandSpec(
            group="Provider",
            summary="List configured remote providers.",
            usage="/providers list",
        )

    def _selected_model_for_provider(self, provider_name: str) -> str:
        """Return the selected model for a provider when one is known."""
        if self._session_manager is not None:
            session = self._session_manager.current
            if session.provider == provider_name and session.model:
                return session.model

        active_provider = (
            str(self._config.get("provider.active_provider", "") or "")
            if self._config is not None
            else ""
        )
        active_model = (
            str(self._config.get("model.active_model", "") or "")
            if self._config is not None
            else ""
        )
        if active_provider == provider_name and active_model:
            return active_model
        return ""

    def _provider_readiness(self, provider_name: str, model_name: str):
        """Resolve provider-model readiness without crashing the listing command."""
        provider = None
        try:
            provider = self._registry.create_provider(provider_name)
            return resolve_remote_model_readiness(provider, model_name)
        except Exception:
            return build_target_readiness(
                kind="provider",
                selection_state="unknown",
                capabilities={"tool_use": False, "reasoning": False, "streaming": True},
                capability_provenance=unknown_capability_provenance(
                    {"tool_use": False, "reasoning": False, "streaming": True},
                    reason=f"Provider '{provider_name}' could not be inspected.",
                ),
                summary=f"Provider '{provider_name}' could not be inspected.",
                guidance="Run /providers test to refresh provider readiness.",
            )
        finally:
            try:
                if provider is not None:
                    provider.close()
            except Exception:
                pass


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
        provider = None
        try:
            provider = self._registry.create_provider(name)
            models = provider.list_models()
        except Exception:
            models = []
        finally:
            try:
                if provider is not None:
                    provider.close()
            except Exception:
                pass
        if models:
            session.model = models[0].id or models[0].name
        session.touch()
        if session.model:
            if models[0].selection_state == "legacy_fallback":
                message = (
                    f"Active provider set to '{name}' (legacy fallback model: {session.model}). "
                    "Run /providers test to refresh discovery, then use /set to choose an "
                    "API-discovered model."
                )
            else:
                message = (
                    f"Active provider set to '{name}' "
                    f"(auto-selected {selection_state_label(models[0].selection_state)} "
                    f"model: {session.model})."
                )
            return CommandResult.ok(
                message,
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

        provider = None
        try:
            provider = self._registry.create_provider(name)
            result = provider.test_connection()
        except Exception as e:
            return CommandResult.error(
                f"Cannot test provider '{name}': {e}. "
                "Use /providers list to verify configured providers."
            )
        discovery_body = _provider_discovery_report(
            provider,
            selected_model=(
                self._session_manager.current.model
                if self._session_manager.current.provider == name
                else ""
            ),
        )
        try:
            provider.close()
        except Exception:
            pass

        if result.success:
            self._registry.update_status(name, "tested")
            return CommandResult.ok(
                f"Provider '{name}': {result.message} ({result.latency_ms:.0f}ms)",
                presentation="success",
                body=discovery_body,
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
    config: ConfigManager,
    console: Console,
) -> None:
    """Register all /providers subcommands."""
    router.register("providers", ProvidersParentHandler(), visible_in_menu=False)
    router.register("providers list", ProvidersListHandler(registry, session_manager, config))
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
        readiness = _remote_model_readiness(model)
        options.append(
            SelectionOption(
                value=model_id,
                label=model.name or model_id,
                description=(
                    f"{selection_state_label(model.selection_state)} • "
                    f"{format_capability_brief('tools', readiness.capabilities['tool_use'])} • "
                    f"{format_capability_brief('reasoning', readiness.capabilities['reasoning'])}"
                ),
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


def _provider_discovery_report(provider: RemoteProvider, *, selected_model: str) -> str:
    """Render a concise discovery/readiness report for /providers test."""
    models = provider.list_models()
    if not models:
        return "Model discovery: no models returned."

    if any(model.selection_state == "api_discovered" for model in models):
        lines = [f"Model discovery: api discovered ({len(models)} model(s))."]
    else:
        lines = [
            "Model discovery: legacy fallback (stored default model only).",
            (
                "Run /providers test to refresh discovery, then use /set to choose an "
                "API-discovered model."
            ),
        ]

    if selected_model:
        readiness = resolve_remote_model_readiness(provider, selected_model)
        selection_label = selection_state_label(readiness.selection_state)
        lines.append(f"Current target: {selected_model} [{selection_label}].")
        lines.append(f"Readiness posture: {readiness_posture_label(readiness)}.")
        lines.append(f"Tradeoff: {format_readiness_tradeoff(readiness)}.")
    return "\n".join(lines)


def _remote_model_readiness(model):
    """Build readiness details for one discovered provider model."""
    return build_target_readiness(
        kind="provider",
        selection_state=model.selection_state,
        capabilities=model.capabilities,
        capability_provenance=model.capability_provenance,
        guidance=(
            "Run /providers test to refresh discovery, then use /set to choose an "
            "API-discovered model."
            if model.selection_state == "legacy_fallback"
            else "Use /set to choose another model if this target does not fit the task."
        ),
    )
