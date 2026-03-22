"""/mode command handlers."""

from __future__ import annotations

from collections.abc import Callable

from localagentcli.commands.router import CommandHandler, CommandResult, CommandRouter, CommandSpec
from localagentcli.models.provider_readiness import resolve_remote_model_readiness
from localagentcli.models.readiness import (
    build_target_readiness,
    default_local_capability_provenance,
    format_capability_brief,
    format_readiness_tradeoff,
    readiness_posture_label,
    selection_state_label,
)
from localagentcli.models.registry import ModelRegistry
from localagentcli.providers.registry import ProviderRegistry
from localagentcli.session.manager import SessionManager


def _parse_name_version(name: str) -> tuple[str, str | None]:
    """Parse 'name@v1' into (name, version)."""
    if "@" in name:
        model_name, version = name.rsplit("@", 1)
        return model_name, version
    return name, None


class ModeParentHandler(CommandHandler):
    """Parent handler that shows subcommand help."""

    def execute(self, args: list[str]) -> CommandResult:
        return CommandResult.error(
            "/mode requires a subcommand: chat, agent. Use /help mode for details."
        )

    def describe(self) -> CommandSpec:
        return CommandSpec(
            group="Mode",
            summary="Switch between chat mode and agent mode.",
            usage="/mode <chat|agent>",
            argument_hint="<subcommand>",
            details=("Use /mode chat for conversation or /mode agent for tool-capable task work."),
        )


class ModeChatHandler(CommandHandler):
    """Switch the current session to chat mode."""

    def __init__(
        self,
        session_manager: SessionManager,
        stop_agent_callback: Callable[[], bool] | None = None,
    ):
        self._session_manager = session_manager
        self._stop_agent_callback = stop_agent_callback

    def execute(self, args: list[str]) -> CommandResult:
        if self._stop_agent_callback is not None and not self._stop_agent_callback():
            return CommandResult.ok("Mode change cancelled.", presentation="warning")
        session = self._session_manager.current
        session.mode = "chat"
        session.touch()
        return CommandResult.ok("Switched to chat mode.", presentation="success")

    def describe(self) -> CommandSpec:
        return CommandSpec(
            group="Mode",
            summary="Switch to conversational chat mode.",
            usage="/mode chat",
        )


class ModeAgentHandler(CommandHandler):
    """Switch the current session to agent mode when the active model supports tools."""

    def __init__(
        self,
        session_manager: SessionManager,
        model_registry: ModelRegistry,
        provider_registry: ProviderRegistry,
    ):
        self._session_manager = session_manager
        self._model_registry = model_registry
        self._provider_registry = provider_registry

    def execute(self, args: list[str]) -> CommandResult:
        session = self._session_manager.current
        if not session.model and not session.provider:
            session.mode = "agent"
            session.touch()
            return CommandResult.ok(
                "Switched to agent mode. Configure a tool-capable model or provider before "
                "running tasks.",
                presentation="status",
            )
        if session.provider:
            provider = self._provider_registry.get(session.provider)
            if provider is None:
                return CommandResult.error(
                    f"Cannot enter agent mode: provider '{session.provider}' is not configured."
                )
            if not session.model:
                return CommandResult.error(
                    "Cannot enter agent mode: no provider model is selected. "
                    "Use /set or /set default to choose one."
                )
            runtime = None
            try:
                runtime = self._provider_registry.create_provider(session.provider)
                runtime.set_active_model(session.model)
                readiness = resolve_remote_model_readiness(runtime, session.model)
            except Exception as exc:
                return CommandResult.error(
                    f"Cannot enter agent mode: failed to inspect provider "
                    f"'{session.provider}': {exc}. Run /providers test to verify connectivity."
                )
            finally:
                try:
                    if runtime is not None:
                        runtime.close()
                except Exception:
                    pass
            tool_use = readiness.capabilities["tool_use"]
            if readiness.selection_state in {"legacy_fallback", "unknown"}:
                return CommandResult.error(
                    "Cannot enter agent mode: active provider model "
                    f"'{session.model}' is {selection_state_label(readiness.selection_state)}. "
                    f"Readiness posture: {readiness_posture_label(readiness)}. "
                    f"Tradeoff: {format_readiness_tradeoff(readiness)}. "
                    f"{readiness.guidance}"
                )
            if not tool_use.supported:
                return CommandResult.error(
                    "Cannot enter agent mode: the active provider "
                    f"model ({session.model}) reports "
                    f"{format_capability_brief('tool use', tool_use)} - {tool_use.reason}. "
                    f"Readiness posture: {readiness_posture_label(readiness)}. "
                    f"Tradeoff: {format_readiness_tradeoff(readiness)}. "
                    "Use /set to switch to a tool-capable provider."
                )
        elif session.model:
            name, version = _parse_name_version(session.model)
            entry = self._model_registry.get_model(name, version)
            if entry is None:
                return CommandResult.error(
                    f"Cannot enter agent mode: model '{session.model}' is not installed."
                )
            readiness = build_target_readiness(
                kind="local",
                selection_state="local",
                capabilities=entry.capabilities,
                capability_provenance=entry.capability_provenance,
                default_builder=default_local_capability_provenance,
            )
            tool_use = readiness.capabilities["tool_use"]
            if not tool_use.supported:
                return CommandResult.error(
                    "Cannot enter agent mode: the active model "
                    f"({session.model}) reports "
                    f"{format_capability_brief('tool use', tool_use)} - {tool_use.reason}. "
                    f"Readiness posture: {readiness_posture_label(readiness)}. "
                    f"Tradeoff: {format_readiness_tradeoff(readiness)}. "
                    "Use /set to switch to a tool-capable target."
                )

        session.mode = "agent"
        session.touch()
        return CommandResult.ok("Switched to agent mode.", presentation="success")

    def describe(self) -> CommandSpec:
        return CommandSpec(
            group="Mode",
            summary="Switch to agent mode when the active target supports tool use.",
            usage="/mode agent",
        )


def register(
    router: CommandRouter,
    session_manager: SessionManager,
    model_registry: ModelRegistry,
    provider_registry: ProviderRegistry,
    stop_agent_callback: Callable[[], bool] | None = None,
) -> None:
    """Register all /mode subcommands."""
    router.register("mode", ModeParentHandler(), visible_in_menu=False)
    router.register("mode chat", ModeChatHandler(session_manager, stop_agent_callback))
    router.register(
        "mode agent",
        ModeAgentHandler(session_manager, model_registry, provider_registry),
    )
