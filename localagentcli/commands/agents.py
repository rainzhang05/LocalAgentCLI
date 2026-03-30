"""/agents command handlers for multi-agent observability and lifecycle control."""

from __future__ import annotations

from collections.abc import Callable

from localagentcli.commands.router import CommandHandler, CommandResult, CommandRouter, CommandSpec
from localagentcli.runtime.core import SessionExecutionRuntime


class AgentsParentHandler(CommandHandler):
    """Parent handler that explains available /agents subcommands."""

    def __init__(
        self,
        runtime_provider: Callable[[], SessionExecutionRuntime | None],
    ):
        self._runtime_provider = runtime_provider

    def execute(self, args: list[str]) -> CommandResult:
        if args and args[0] == "list":
            return AgentsListHandler(self._runtime_provider).execute(args[1:])
        return CommandResult.error(
            "/agents requires a subcommand: list, inspect, clear. Use /help agents for details."
        )

    def describe(self) -> CommandSpec:
        return CommandSpec(
            group="Agent",
            summary="Inspect and manage path-addressable sub-agents.",
            usage="/agents <list|inspect|clear>",
            argument_hint="<subcommand>",
            details=(
                "Use /agents list to view active sub-agents, /agents inspect to view one "
                "agent's metadata, and /agents clear to close and remove all active agents."
            ),
            examples=(
                "/agents list",
                "/agents inspect worker",
                "/agents inspect /root/researcher",
                "/agents clear",
            ),
        )


class AgentsListHandler(CommandHandler):
    """List active sub-agents in a compact table."""

    def __init__(
        self,
        runtime_provider: Callable[[], SessionExecutionRuntime | None],
    ):
        self._runtime_provider = runtime_provider

    def execute(self, args: list[str]) -> CommandResult:
        runtime = self._runtime_provider()
        if runtime is None:
            return CommandResult.error("Runtime is unavailable.")
        if not runtime.is_multi_agent_path_routing_enabled():
            return CommandResult.error(
                "Multi-agent path routing is disabled. "
                "Enable features.multi_agent_path_routing to use /agents."
            )

        snapshot = runtime.active_agents_snapshot()
        if not snapshot:
            return CommandResult.ok("No active sub-agents.", presentation="status")

        lines = [
            "Active sub-agents:",
            "",
            f"  {'Path':<28s} {'Status':<12s} {'Tasks':<7s} {'Updated':<20s} {'Role':<14s}",
            f"  {'─' * 28} {'─' * 12} {'─' * 7} {'─' * 20} {'─' * 14}",
        ]
        for path, info in snapshot.items():
            status = str(info.get("status", ""))
            tasks = _as_non_negative_int(info.get("task_count", 0))
            updated = str(info.get("updated_at", ""))[:19]
            role = str(info.get("role", "") or "")
            lines.append(f"  {path:<28s} {status:<12s} {tasks:<7d} {updated:<20s} {role:<14s}")
        return CommandResult.ok("\n".join(lines))

    def describe(self) -> CommandSpec:
        return CommandSpec(
            group="Agent",
            summary="List active path-addressable sub-agents.",
            usage="/agents list",
        )


class AgentsInspectHandler(CommandHandler):
    """Inspect one sub-agent by path reference."""

    def __init__(
        self,
        runtime_provider: Callable[[], SessionExecutionRuntime | None],
    ):
        self._runtime_provider = runtime_provider

    def execute(self, args: list[str]) -> CommandResult:
        runtime = self._runtime_provider()
        if runtime is None:
            return CommandResult.error("Runtime is unavailable.")
        if not runtime.is_multi_agent_path_routing_enabled():
            return CommandResult.error(
                "Multi-agent path routing is disabled. "
                "Enable features.multi_agent_path_routing to use /agents."
            )
        if not args:
            return CommandResult.error("Usage: /agents inspect <path>")

        target = args[0].strip()
        if not target:
            return CommandResult.error("Usage: /agents inspect <path>")

        try:
            resolved_path, summary = runtime.inspect_active_agent(target)
        except ValueError as exc:
            return CommandResult.error(str(exc))

        lines = [
            f"Agent: {resolved_path}",
            "",
            f"  Name:        {summary.get('name', '')}",
            f"  Status:      {summary.get('status', '')}",
            f"  Nickname:    {summary.get('nickname', '')}",
            f"  Role:        {summary.get('role', '')}",
            f"  Tasks:       {summary.get('task_count', 0)}",
            f"  Last error:  {summary.get('last_error', '')}",
            f"  Updated:     {summary.get('updated_at', '')}",
        ]
        return CommandResult.ok("\n".join(lines))

    def describe(self) -> CommandSpec:
        return CommandSpec(
            group="Agent",
            summary="Inspect one sub-agent by relative or absolute path.",
            usage="/agents inspect <path>",
            argument_hint="<path>",
        )


class AgentsClearHandler(CommandHandler):
    """Close and remove all active sub-agents."""

    def __init__(
        self,
        runtime_provider: Callable[[], SessionExecutionRuntime | None],
    ):
        self._runtime_provider = runtime_provider

    def execute(self, args: list[str]) -> CommandResult:
        runtime = self._runtime_provider()
        if runtime is None:
            return CommandResult.error("Runtime is unavailable.")
        if not runtime.is_multi_agent_path_routing_enabled():
            return CommandResult.error(
                "Multi-agent path routing is disabled. "
                "Enable features.multi_agent_path_routing to use /agents."
            )

        removed = runtime.clear_active_agents()
        if removed == 0:
            return CommandResult.ok("No active sub-agents to clear.", presentation="status")
        return CommandResult.ok(
            f"Cleared {removed} active sub-agent(s).",
            presentation="success",
        )

    def describe(self) -> CommandSpec:
        return CommandSpec(
            group="Agent",
            summary="Close and remove all active sub-agents.",
            usage="/agents clear",
        )


def register(
    router: CommandRouter,
    runtime_provider: Callable[[], SessionExecutionRuntime | None],
) -> None:
    """Register /agents commands."""
    router.register("agents", AgentsParentHandler(runtime_provider))
    router.register("agents list", AgentsListHandler(runtime_provider))
    router.register("agents inspect", AgentsInspectHandler(runtime_provider))
    router.register("agents clear", AgentsClearHandler(runtime_provider))


def _as_non_negative_int(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return max(value, 0)
    if isinstance(value, str):
        try:
            parsed = int(value)
        except ValueError:
            return 0
        return max(parsed, 0)
    return 0
