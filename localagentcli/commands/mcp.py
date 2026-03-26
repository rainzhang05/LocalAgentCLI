"""/mcp command handlers — list servers, manage auth tokens."""

from __future__ import annotations

from localagentcli.commands.router import CommandHandler, CommandResult, CommandRouter, CommandSpec
from localagentcli.mcp import McpManager
from localagentcli.providers.keys import KeyManager
from localagentcli.shell.prompt import prompt_secret


class McpParentHandler(CommandHandler):
    """Parent handler that shows subcommand help."""

    def execute(self, args: list[str]) -> CommandResult:
        return CommandResult.error(
            "/mcp requires a subcommand: list, login, logout. Use /help mcp for details."
        )

    def describe(self) -> CommandSpec:
        return CommandSpec(
            group="MCP",
            summary="Inspect MCP servers and manage MCP auth tokens.",
            usage="/mcp <list|login|logout>",
            argument_hint="<subcommand>",
            details=("Use /mcp login <server> to store a bearer token for HTTP/SSE MCP servers."),
        )


class McpListHandler(CommandHandler):
    """List configured MCP servers and transport modes."""

    def __init__(self, manager: McpManager | None):
        self._manager = manager

    def execute(self, args: list[str]) -> CommandResult:
        if self._manager is None:
            return CommandResult.ok("MCP is disabled for this runtime.", presentation="status")

        names = self._manager.configured_server_names()
        if not names:
            return CommandResult.ok(
                "No MCP servers configured. Add entries under [mcp_servers] in config.toml.",
                presentation="status",
            )

        lines = ["Configured MCP servers:", ""]
        for server_name in names:
            lines.append(f"- {server_name}")
        return CommandResult.ok("\n".join(lines))

    def describe(self) -> CommandSpec:
        return CommandSpec(
            group="MCP",
            summary="List configured MCP servers.",
            usage="/mcp list",
        )


class McpLoginHandler(CommandHandler):
    """Store a bearer token for one MCP server."""

    def __init__(self, manager: McpManager | None, key_manager: KeyManager):
        self._manager = manager
        self._key_manager = key_manager

    def execute(self, args: list[str]) -> CommandResult:
        if self._manager is None:
            return CommandResult.error("MCP manager is not available.")
        if not args:
            return CommandResult.error("MCP server name is required. Usage: /mcp login <server>")

        server_name = args[0]
        if server_name not in set(self._manager.configured_server_names()):
            return CommandResult.error(
                f"Unknown MCP server '{server_name}'. Use /mcp list to inspect configured servers."
            )

        token = " ".join(args[1:]).strip() if len(args) > 1 else ""
        if not token:
            prompted = prompt_secret(f"Bearer token for MCP server '{server_name}'")
            if prompted is None:
                return CommandResult.ok("MCP login cancelled.", presentation="warning")
            token = prompted.strip()
        if not token:
            return CommandResult.error("A non-empty bearer token is required.")

        self._key_manager.store_key(f"mcp_server:{server_name}", token)
        return CommandResult.ok(
            f"Stored MCP bearer token for server '{server_name}'.",
            presentation="success",
        )

    def describe(self) -> CommandSpec:
        return CommandSpec(
            group="MCP",
            summary="Store a bearer token for one MCP server.",
            usage="/mcp login <server> [token]",
            argument_hint="<server> [token]",
            details=(
                "When [token] is omitted, LocalAgentCLI prompts securely and stores the token "
                "in keychain/file fallback via KeyManager."
            ),
        )


class McpLogoutHandler(CommandHandler):
    """Delete a stored bearer token for one MCP server."""

    def __init__(self, key_manager: KeyManager):
        self._key_manager = key_manager

    def execute(self, args: list[str]) -> CommandResult:
        if not args:
            return CommandResult.error("MCP server name is required. Usage: /mcp logout <server>")
        server_name = args[0]
        self._key_manager.delete_key(f"mcp_server:{server_name}")
        return CommandResult.ok(
            f"Removed MCP bearer token for server '{server_name}'.",
            presentation="success",
        )

    def describe(self) -> CommandSpec:
        return CommandSpec(
            group="MCP",
            summary="Delete a stored bearer token for one MCP server.",
            usage="/mcp logout <server>",
            argument_hint="<server>",
        )


def register(router: CommandRouter, manager: McpManager | None, key_manager: KeyManager) -> None:
    """Register /mcp command group."""
    router.register("mcp", McpParentHandler(), visible_in_menu=False)
    router.register("mcp list", McpListHandler(manager))
    router.register("mcp login", McpLoginHandler(manager, key_manager))
    router.register("mcp logout", McpLogoutHandler(key_manager))
