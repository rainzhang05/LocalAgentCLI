"""/mode command handlers."""

from __future__ import annotations

from localagentcli.commands.router import CommandHandler, CommandResult, CommandRouter
from localagentcli.models.registry import ModelRegistry
from localagentcli.providers.registry import ProviderRegistry
from localagentcli.session.manager import SessionManager

_TOOL_CAPABLE_PROVIDER_TYPES = {"openai", "anthropic"}


def _parse_name_version(name: str) -> tuple[str, str | None]:
    """Parse 'name@v1' into (name, version)."""
    if "@" in name:
        model_name, version = name.rsplit("@", 1)
        return model_name, version
    return name, None


class ModeParentHandler(CommandHandler):
    """Parent handler that shows subcommand help."""

    def execute(self, args: list[str]) -> CommandResult:
        return CommandResult.error("/mode requires a subcommand: chat, agent")

    def help_text(self) -> str:
        return (
            "Switch execution mode.\n"
            "Subcommands:\n"
            "  /mode chat               Switch to conversational chat mode\n"
            "  /mode agent              Switch to agent mode (tool-capable model required)"
        )


class ModeChatHandler(CommandHandler):
    """Switch the current session to chat mode."""

    def __init__(self, session_manager: SessionManager):
        self._session_manager = session_manager

    def execute(self, args: list[str]) -> CommandResult:
        session = self._session_manager.current
        session.mode = "chat"
        session.touch()
        return CommandResult.ok("Switched to chat mode.")

    def help_text(self) -> str:
        return "Switch to chat mode.\nUsage: /mode chat"


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
                "running tasks."
            )
        if session.provider:
            provider = self._provider_registry.get(session.provider)
            if provider is None:
                return CommandResult.error(
                    f"Cannot enter agent mode: provider '{session.provider}' is not configured."
                )
            if provider.type not in _TOOL_CAPABLE_PROVIDER_TYPES:
                return CommandResult.error(
                    "Cannot enter agent mode: the active provider "
                    f"({session.provider}) does not support tool use. "
                    "Use /providers use <name> to switch to a tool-capable provider."
                )
        elif session.model:
            name, version = _parse_name_version(session.model)
            entry = self._model_registry.get_model(name, version)
            if entry is None:
                return CommandResult.error(
                    f"Cannot enter agent mode: model '{session.model}' is not installed."
                )
            if not bool(entry.capabilities.get("tool_use", False)):
                return CommandResult.error(
                    "Cannot enter agent mode: the active model "
                    f"({session.model}) does not support tool use. "
                    "Use /models use <name> or /providers use <name> to switch "
                    "to a tool-capable target."
                )

        session.mode = "agent"
        session.touch()
        return CommandResult.ok("Switched to agent mode.")

    def help_text(self) -> str:
        return "Switch to agent mode.\nUsage: /mode agent"


def register(
    router: CommandRouter,
    session_manager: SessionManager,
    model_registry: ModelRegistry,
    provider_registry: ProviderRegistry,
) -> None:
    """Register all /mode subcommands."""
    router.register("mode", ModeParentHandler())
    router.register("mode chat", ModeChatHandler(session_manager))
    router.register(
        "mode agent",
        ModeAgentHandler(session_manager, model_registry, provider_registry),
    )
