"""/mcp command handlers — list servers, manage auth tokens."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import urllib.parse
import urllib.request
import webbrowser

from localagentcli.commands.router import CommandHandler, CommandResult, CommandRouter, CommandSpec
from localagentcli.mcp import McpManager
from localagentcli.providers.keys import KeyManager
from localagentcli.shell.prompt import prompt_secret, prompt_text


class McpParentHandler(CommandHandler):
    """Parent handler that shows subcommand help."""

    def execute(self, args: list[str]) -> CommandResult:
        return CommandResult.error(
            "/mcp requires a subcommand: list, login, logout, oauth. Use /help mcp for details."
        )

    def describe(self) -> CommandSpec:
        return CommandSpec(
            group="MCP",
            summary="Inspect MCP servers and manage MCP auth tokens.",
            usage="/mcp <list|login|logout|oauth>",
            argument_hint="<subcommand>",
            details=(
                "Use /mcp login <server> to store a bearer token manually, or "
                "/mcp oauth <server> for OAuth code flow on configured servers."
            ),
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


class McpOauthHandler(CommandHandler):
    """Run OAuth authorization-code flow for one configured MCP server."""

    def __init__(self, manager: McpManager | None, key_manager: KeyManager):
        self._manager = manager
        self._key_manager = key_manager

    def execute(self, args: list[str]) -> CommandResult:
        if self._manager is None:
            return CommandResult.error("MCP manager is not available.")
        if not args:
            return CommandResult.error("MCP server name is required. Usage: /mcp oauth <server>")

        server_name = args[0]
        server = self._manager.get_server_config(server_name)
        if server is None:
            return CommandResult.error(
                f"Unknown MCP server '{server_name}'. Use /mcp list to inspect configured servers."
            )

        if not (server.oauth_authorize_url and server.oauth_token_url and server.oauth_client_id):
            return CommandResult.error(
                "OAuth config is incomplete. Required fields: oauth_authorize_url, "
                "oauth_token_url, oauth_client_id."
            )

        redirect_uri = server.oauth_redirect_uri or "urn:ietf:wg:oauth:2.0:oob"
        code_verifier = _generate_code_verifier()
        code_challenge = _pkce_s256_challenge(code_verifier)
        state = secrets.token_urlsafe(16)

        query: dict[str, str] = {
            "response_type": "code",
            "client_id": server.oauth_client_id,
            "redirect_uri": redirect_uri,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "state": state,
        }
        if server.oauth_scopes:
            query["scope"] = " ".join(server.oauth_scopes)
        authorize_url = f"{server.oauth_authorize_url}?{urllib.parse.urlencode(query)}"

        opened = webbrowser.open(authorize_url)
        if not opened:
            # Browser may fail in headless environments; continue with manual copy.
            pass

        auth_code = prompt_text("OAuth authorization code", default="")
        if auth_code is None:
            return CommandResult.ok("MCP OAuth cancelled.", presentation="warning")
        auth_code = auth_code.strip()
        if not auth_code:
            return CommandResult.error("OAuth authorization code is required.")

        try:
            token = _exchange_oauth_code(
                token_url=server.oauth_token_url,
                client_id=server.oauth_client_id,
                redirect_uri=redirect_uri,
                code=auth_code,
                code_verifier=code_verifier,
                client_secret_env_var=server.oauth_client_secret_env_var,
                timeout=max(float(server.timeout), 0.1),
            )
        except ValueError as exc:
            return CommandResult.error(str(exc))
        self._key_manager.store_key(f"mcp_server:{server_name}", token)
        return CommandResult.ok(
            f"Stored MCP OAuth bearer token for server '{server_name}'.",
            presentation="success",
            body=f"Authorization URL:\n{authorize_url}",
        )

    def describe(self) -> CommandSpec:
        return CommandSpec(
            group="MCP",
            summary="Run OAuth browser flow for one MCP server and store the bearer token.",
            usage="/mcp oauth <server>",
            argument_hint="<server>",
            details=(
                "Server must define oauth_authorize_url, oauth_token_url, and oauth_client_id "
                "under [mcp_servers.<name>] in config.toml."
            ),
        )


def register(router: CommandRouter, manager: McpManager | None, key_manager: KeyManager) -> None:
    """Register /mcp command group."""
    router.register("mcp", McpParentHandler(), visible_in_menu=False)
    router.register("mcp list", McpListHandler(manager))
    router.register("mcp login", McpLoginHandler(manager, key_manager))
    router.register("mcp logout", McpLogoutHandler(key_manager))
    router.register("mcp oauth", McpOauthHandler(manager, key_manager))


def _generate_code_verifier() -> str:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")
    return verifier.rstrip("=")


def _pkce_s256_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _exchange_oauth_code(
    *,
    token_url: str,
    client_id: str,
    redirect_uri: str,
    code: str,
    code_verifier: str,
    client_secret_env_var: str | None,
    timeout: float,
) -> str:
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,
    }
    if isinstance(client_secret_env_var, str) and client_secret_env_var.strip():
        secret = prompt_secret(f"OAuth client secret ({client_secret_env_var})")
        if secret:
            payload["client_secret"] = secret.strip()

    body = urllib.parse.urlencode(payload).encode("utf-8")
    request = urllib.request.Request(
        token_url,
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_text = response.read().decode("utf-8", errors="replace")
    except Exception as exc:  # pragma: no cover - thin wrapper around stdlib transport
        raise ValueError(f"OAuth token exchange failed: {exc}") from exc

    try:
        parsed = json.loads(response_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"OAuth token response was not valid JSON: {exc.msg}") from exc

    token = parsed.get("access_token") if isinstance(parsed, dict) else None
    if not isinstance(token, str) or not token.strip():
        raise ValueError("OAuth token response did not include a non-empty access_token.")
    return token.strip()
