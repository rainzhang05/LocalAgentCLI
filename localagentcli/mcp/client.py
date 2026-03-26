"""Minimal MCP clients and tool discovery manager (stdio/http/sse)."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import dataclass, field
from typing import Any, Callable

from localagentcli.tools import DynamicToolSpec, ToolResult

BearerTokenResolver = Callable[[str], str | None]
ElicitationHandler = Callable[[str, str, dict[str, Any]], dict[str, Any] | None]


@dataclass
class McpTool:
    """A discovered MCP tool definition."""

    name: str
    description: str
    input_schema: dict[str, Any]
    annotations: dict[str, Any] = field(default_factory=dict)

    @property
    def is_read_only(self) -> bool:
        value = self.annotations.get("readOnlyHint")
        return bool(value) if isinstance(value, bool) else False


@dataclass
class McpServerConfig:
    """Configuration for one MCP server."""

    name: str
    transport: str = "stdio"
    command: str = ""
    args: list[str] = field(default_factory=list)
    cwd: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    url: str | None = None
    http_headers: dict[str, str] = field(default_factory=dict)
    bearer_token_env_var: str | None = None
    oauth_authorize_url: str | None = None
    oauth_token_url: str | None = None
    oauth_client_id: str | None = None
    oauth_client_secret_env_var: str | None = None
    oauth_redirect_uri: str | None = None
    oauth_scopes: list[str] = field(default_factory=list)
    timeout: float = 15.0


def _subprocess_env(config_env: dict[str, str]) -> dict[str, str] | None:
    """Child environment: inherit when no overrides; else merge over os.environ."""
    if not config_env:
        return None
    return {**os.environ, **config_env}


class StdioMcpClient:
    """Very small synchronous MCP stdio client for tools/list and tools/call."""

    def __init__(
        self,
        config: McpServerConfig,
        *,
        server_name: str,
        elicitation_handler: ElicitationHandler | None = None,
    ):
        self._config = config
        self._server_name = server_name
        self._elicitation_handler = elicitation_handler
        self._process: subprocess.Popen[str] | None = None
        self._request_id = 0

    def start(self) -> None:
        if self._process is not None:
            return
        command = [self._config.command, *self._config.args]
        self._process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=self._config.cwd,
            env=_subprocess_env(self._config.env),
            bufsize=1,
        )
        self.request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "LocalAgentCLI", "version": "0.2.0"},
            },
        )
        self.notify("notifications/initialized")

    def list_tools(self) -> list[McpTool]:
        payload = self.request("tools/list", {})
        tools = payload.get("tools", [])
        discovered: list[McpTool] = []
        if not isinstance(tools, list):
            return discovered
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            discovered.append(
                McpTool(
                    name=str(tool.get("name", "")),
                    description=str(tool.get("description", "")),
                    input_schema=_coerce_schema(tool.get("inputSchema")),
                    annotations=_coerce_annotations(tool.get("annotations")),
                )
            )
        return discovered

    def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        try:
            payload = self.request(
                "tools/call",
                {"name": name, "arguments": arguments},
            )
            payload = self._maybe_handle_elicitation(name, arguments, payload)
        except TimeoutError as exc:
            return ToolResult.error_result(
                f"MCP tool '{name}' timed out",
                str(exc),
            )
        except RuntimeError as exc:
            return ToolResult.error_result(
                f"MCP tool '{name}' failed",
                str(exc),
            )
        content = payload.get("content", [])
        text_parts: list[str] = []
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text_parts.append(str(item.get("text", "")))
        is_error = bool(payload.get("isError", False))
        output = "\n".join(part for part in text_parts if part)
        if is_error:
            return ToolResult.error_result(
                f"MCP tool '{name}' failed",
                output or "The MCP server returned an error.",
                output=output,
            )
        return ToolResult.success(
            f"MCP tool '{name}' completed.",
            output=output,
        )

    def _maybe_handle_elicitation(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        request = payload.get("elicitation") or payload.get("elicitationRequest")
        if not isinstance(request, dict):
            return payload
        if self._elicitation_handler is None:
            raise RuntimeError(
                "MCP server requested elicitation input but no elicitation handler is configured."
            )
        response = self._elicitation_handler(self._server_name, tool_name, request)
        if response is None:
            raise RuntimeError("MCP elicitation request was declined by the operator.")
        return self.request(
            "tools/call",
            {
                "name": tool_name,
                "arguments": arguments,
                "elicitationResponse": response,
            },
        )

    def close(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        try:
            if process.stdin is not None:
                process.stdin.close()
        except Exception:
            pass
        process.terminate()
        try:
            process.wait(timeout=1)
        except Exception:
            process.kill()

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.start()
        self._request_id += 1
        request_id = self._request_id
        message = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        self._write_message(message)
        while True:
            response = self._read_message()
            if response.get("id") != request_id:
                continue
            if "error" in response:
                error_payload = json.dumps(response["error"], ensure_ascii=False)
                raise RuntimeError(f"MCP request '{method}' failed: {error_payload}")
            result = response.get("result", {})
            return result if isinstance(result, dict) else {}

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self.start()
        message = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
        }
        self._write_message(message)

    def _write_message(self, message: dict[str, Any]) -> None:
        process = self._require_process()
        if process.stdin is None:
            raise RuntimeError("MCP process stdin is unavailable.")
        process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        process.stdin.flush()

    def _read_message(self) -> dict[str, Any]:
        line = self._read_line_bounded()
        if not line:
            process = self._require_process()
            stderr = ""
            if process.stderr is not None:
                stderr = process.stderr.read().strip()
            raise RuntimeError(stderr or "MCP server closed the connection unexpectedly.")
        parsed = json.loads(line)
        if not isinstance(parsed, dict):
            raise RuntimeError("MCP server returned a non-object JSON message.")
        return parsed

    def _read_line_bounded(self) -> str:
        process = self._require_process()
        if process.stdout is None:
            raise RuntimeError("MCP process stdout is unavailable.")
        stdout = process.stdout
        timeout = max(float(self._config.timeout), 0.1)

        def read_line() -> str:
            return stdout.readline()

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(read_line)
            try:
                line: str = future.result(timeout=timeout)
            except FuturesTimeoutError:
                self._reset_process_after_failure()
                raise TimeoutError(
                    f"MCP I/O timed out after {timeout} seconds (server={self._config.name!r})."
                ) from None
        return line

    def _reset_process_after_failure(self) -> None:
        """Terminate the server process so a subsequent start() can respawn."""
        process = self._process
        self._process = None
        if process is None:
            return
        try:
            if process.stdin is not None:
                process.stdin.close()
        except Exception:
            pass
        process.terminate()
        try:
            process.wait(timeout=1)
        except Exception:
            process.kill()

    def _require_process(self) -> subprocess.Popen[str]:
        process = self._process
        if process is None:
            raise RuntimeError("MCP process is not started.")
        return process


class HttpMcpClient:
    """Synchronous MCP HTTP client for JSON-RPC style requests."""

    def __init__(
        self,
        config: McpServerConfig,
        *,
        server_name: str,
        bearer_token_resolver: BearerTokenResolver | None = None,
        elicitation_handler: ElicitationHandler | None = None,
    ):
        self._config = config
        self._server_name = server_name
        self._bearer_token_resolver = bearer_token_resolver
        self._elicitation_handler = elicitation_handler
        self._request_id = 0

    def start(self) -> None:
        # HTTP transport does not need subprocess startup.
        return

    def list_tools(self) -> list[McpTool]:
        payload = self.request("tools/list", {})
        tools = payload.get("tools", [])
        discovered: list[McpTool] = []
        if not isinstance(tools, list):
            return discovered
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            discovered.append(
                McpTool(
                    name=str(tool.get("name", "")),
                    description=str(tool.get("description", "")),
                    input_schema=_coerce_schema(tool.get("inputSchema")),
                    annotations=_coerce_annotations(tool.get("annotations")),
                )
            )
        return discovered

    def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        try:
            payload = self.request("tools/call", {"name": name, "arguments": arguments})
            payload = self._maybe_handle_elicitation(name, arguments, payload)
        except TimeoutError as exc:
            return ToolResult.error_result(
                f"MCP tool '{name}' timed out",
                str(exc),
            )
        except RuntimeError as exc:
            return ToolResult.error_result(
                f"MCP tool '{name}' failed",
                str(exc),
            )
        content = payload.get("content", [])
        text_parts: list[str] = []
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text_parts.append(str(item.get("text", "")))
        is_error = bool(payload.get("isError", False))
        output = "\n".join(part for part in text_parts if part)
        if is_error:
            return ToolResult.error_result(
                f"MCP tool '{name}' failed",
                output or "The MCP server returned an error.",
                output=output,
            )
        return ToolResult.success(f"MCP tool '{name}' completed.", output=output)

    def close(self) -> None:
        return

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.start()
        self._request_id += 1
        request_id = self._request_id
        message = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        response = self._send_message(message)
        return self._extract_result(response, method, request_id)

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self.start()
        message = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
        }
        self._send_message(message)

    def _send_message(self, message: dict[str, Any]) -> Any:
        url = self._config.url
        if not url:
            raise RuntimeError(f"MCP server '{self._config.name}' has no URL configured.")

        payload = json.dumps(message, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **self._resolved_http_headers(),
        }
        request = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        timeout = max(float(self._config.timeout), 0.1)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                content_type = response.headers.get("Content-Type", "")
                body = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            raise RuntimeError(
                f"MCP HTTP request failed ({exc.code} {exc.reason}): {details or 'no details'}"
            ) from None
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, TimeoutError):
                raise TimeoutError(
                    f"MCP I/O timed out after {timeout} seconds (server={self._config.name!r})."
                ) from None
            raise RuntimeError(f"MCP HTTP request failed: {reason}") from None

        if "text/event-stream" in content_type.lower():
            return self._parse_sse_events(body)

        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"MCP server returned invalid JSON response: {exc.msg}") from None

    def _parse_sse_events(self, body: str) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        current_data: list[str] = []

        def flush_event() -> None:
            if not current_data:
                return
            payload = "\n".join(current_data)
            current_data.clear()
            try:
                parsed = json.loads(payload)
            except json.JSONDecodeError:
                return
            if isinstance(parsed, dict):
                events.append(parsed)

        for raw_line in body.splitlines():
            line = raw_line.strip("\r")
            if not line:
                flush_event()
                continue
            if line.startswith(":"):
                continue
            if line.startswith("data:"):
                current_data.append(line[5:].lstrip())

        flush_event()
        return events

    def _extract_result(self, response: Any, method: str, request_id: int) -> dict[str, Any]:
        if isinstance(response, list):
            for item in response:
                if isinstance(item, dict) and item.get("id") == request_id:
                    response = item
                    break
            else:
                raise RuntimeError(
                    f"MCP request '{method}' did not include a matching response id."
                )

        if not isinstance(response, dict):
            raise RuntimeError("MCP server returned a non-object JSON message.")

        if "error" in response:
            error_payload = json.dumps(response["error"], ensure_ascii=False)
            raise RuntimeError(f"MCP request '{method}' failed: {error_payload}")

        if "result" in response:
            result = response.get("result", {})
            return result if isinstance(result, dict) else {}

        # Some implementations may return the result object directly.
        return response

    def _resolved_http_headers(self) -> dict[str, str]:
        headers = dict(self._config.http_headers)
        if self._bearer_token_resolver is not None:
            token = (self._bearer_token_resolver(self._server_name) or "").strip()
            if token and "authorization" not in {key.lower() for key in headers}:
                headers["Authorization"] = f"Bearer {token}"
        token_env = self._config.bearer_token_env_var
        if isinstance(token_env, str) and token_env.strip():
            token = os.environ.get(token_env.strip(), "").strip()
            if token and "authorization" not in {key.lower() for key in headers}:
                headers["Authorization"] = f"Bearer {token}"
        return headers

    def _maybe_handle_elicitation(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        request = payload.get("elicitation") or payload.get("elicitationRequest")
        if not isinstance(request, dict):
            return payload
        if self._elicitation_handler is None:
            raise RuntimeError(
                "MCP server requested elicitation input but no elicitation handler is configured."
            )
        response = self._elicitation_handler(self._server_name, tool_name, request)
        if response is None:
            raise RuntimeError("MCP elicitation request was declined by the operator.")
        return self.request(
            "tools/call",
            {
                "name": tool_name,
                "arguments": arguments,
                "elicitationResponse": response,
            },
        )


class SseMcpClient(HttpMcpClient):
    """HTTP client variant that requires SSE responses from the server."""

    def _extract_result(self, response: Any, method: str, request_id: int) -> dict[str, Any]:
        if not isinstance(response, list):
            raise RuntimeError(
                f"MCP request '{method}' expected SSE response but received non-SSE payload."
            )
        return super()._extract_result(response, method, request_id)


class McpManager:
    """Discover and execute MCP tools from configured stdio servers."""

    def __init__(
        self,
        servers: list[McpServerConfig],
        *,
        bearer_token_resolver: BearerTokenResolver | None = None,
    ):
        self._servers = servers
        self._bearer_token_resolver = bearer_token_resolver
        self._elicitation_handler: ElicitationHandler | None = None
        self._clients: dict[str, StdioMcpClient | HttpMcpClient | SseMcpClient] = {}
        self._tools: dict[str, tuple[str, McpTool]] = {}

    @classmethod
    def from_config(
        cls,
        raw_config: dict[str, Any],
        *,
        bearer_token_resolver: BearerTokenResolver | None = None,
    ) -> McpManager:
        servers: list[McpServerConfig] = []
        for name, payload in raw_config.items():
            if not isinstance(payload, dict):
                continue
            transport = _coerce_transport(payload)
            command = payload.get("command", "")
            if transport == "stdio" and (not isinstance(command, str) or not command.strip()):
                continue
            url = payload.get("url")
            if transport in {"http", "sse"} and (not isinstance(url, str) or not url.strip()):
                continue
            args = payload.get("args", [])
            env = payload.get("env", {})
            headers = payload.get("http_headers", {})
            raw_bearer_env = payload.get("bearer_token_env_var")
            servers.append(
                McpServerConfig(
                    name=name,
                    transport=transport,
                    command=command.strip() if isinstance(command, str) else "",
                    args=[str(item) for item in args] if isinstance(args, list) else [],
                    cwd=str(payload["cwd"]) if isinstance(payload.get("cwd"), str) else None,
                    env={str(key): str(value) for key, value in env.items()}
                    if isinstance(env, dict)
                    else {},
                    url=url.strip() if isinstance(url, str) else None,
                    http_headers={str(key): str(value) for key, value in headers.items()}
                    if isinstance(headers, dict)
                    else {},
                    bearer_token_env_var=(
                        raw_bearer_env.strip() if isinstance(raw_bearer_env, str) else None
                    ),
                    oauth_authorize_url=(
                        payload.get("oauth_authorize_url", "").strip() or None
                        if isinstance(payload.get("oauth_authorize_url"), str)
                        else None
                    ),
                    oauth_token_url=(
                        payload.get("oauth_token_url", "").strip() or None
                        if isinstance(payload.get("oauth_token_url"), str)
                        else None
                    ),
                    oauth_client_id=(
                        payload.get("oauth_client_id", "").strip() or None
                        if isinstance(payload.get("oauth_client_id"), str)
                        else None
                    ),
                    oauth_client_secret_env_var=(
                        payload.get("oauth_client_secret_env_var", "").strip() or None
                        if isinstance(payload.get("oauth_client_secret_env_var"), str)
                        else None
                    ),
                    oauth_redirect_uri=(
                        payload.get("oauth_redirect_uri", "").strip() or None
                        if isinstance(payload.get("oauth_redirect_uri"), str)
                        else None
                    ),
                    oauth_scopes=(
                        [
                            str(item).strip()
                            for item in payload.get("oauth_scopes", [])
                            if str(item).strip()
                        ]
                        if isinstance(payload.get("oauth_scopes"), list)
                        else []
                    ),
                    timeout=float(payload.get("timeout", 15.0) or 15.0),
                )
            )
        return cls(servers, bearer_token_resolver=bearer_token_resolver)

    def set_elicitation_handler(self, handler: ElicitationHandler | None) -> None:
        """Set/clear the operator callback used for MCP elicitation requests."""
        self._elicitation_handler = handler
        self._clients.clear()

    def configured_server_names(self) -> list[str]:
        """Return configured MCP server names in declaration order."""
        return [server.name for server in self._servers]

    def get_server_config(self, server_name: str) -> McpServerConfig | None:
        """Return parsed server config for command-layer helpers (e.g., OAuth)."""
        for server in self._servers:
            if server.name == server_name:
                return server
        return None

    def build_dynamic_tool_specs(self) -> list[DynamicToolSpec]:
        specs: list[DynamicToolSpec] = []
        self._tools.clear()
        used_qualified: set[str] = set()
        for server in self._servers:
            client = self._client(server.name)
            for tool in client.list_tools():
                qualified = _make_qualified_tool_name(server.name, tool.name, used_qualified)
                used_qualified.add(qualified)
                self._tools[qualified] = (server.name, tool)
                specs.append(
                    DynamicToolSpec(
                        name=qualified,
                        description=tool.description or f"MCP tool {tool.name} from {server.name}.",
                        parameters_schema=tool.input_schema,
                        executor=self._build_executor(server.name, tool.name),
                        requires_approval=not tool.is_read_only,
                        is_read_only=tool.is_read_only,
                    )
                )
        return specs

    def close(self) -> None:
        for client in self._clients.values():
            client.close()
        self._clients.clear()

    def _build_executor(self, server_name: str, tool_name: str):
        def execute(**kwargs: object) -> ToolResult:
            return self._client(server_name).call_tool(tool_name, dict(kwargs))

        return execute

    def _client(self, server_name: str) -> StdioMcpClient | HttpMcpClient | SseMcpClient:
        if server_name in self._clients:
            return self._clients[server_name]
        config = next(server for server in self._servers if server.name == server_name)
        client: StdioMcpClient | HttpMcpClient | SseMcpClient
        if config.transport == "http":
            client = HttpMcpClient(
                config,
                server_name=server_name,
                bearer_token_resolver=self._bearer_token_resolver,
                elicitation_handler=self._elicitation_handler,
            )
        elif config.transport == "sse":
            client = SseMcpClient(
                config,
                server_name=server_name,
                bearer_token_resolver=self._bearer_token_resolver,
                elicitation_handler=self._elicitation_handler,
            )
        else:
            client = StdioMcpClient(
                config,
                server_name=server_name,
                elicitation_handler=self._elicitation_handler,
            )
        self._clients[server_name] = client
        return client


def _sanitize_tool_name(name: str) -> str:
    allowed = [char if char.isalnum() or char == "_" else "_" for char in name]
    collapsed = "".join(allowed).strip("_")
    return collapsed or "tool"


def _make_qualified_tool_name(server_name: str, tool_name: str, used: set[str]) -> str:
    """Build a model-visible tool name; disambiguate collisions deterministically."""
    base = _sanitize_tool_name(tool_name)
    qualified = f"mcp__{server_name}__{base}"
    if qualified not in used:
        return qualified
    suffix = hashlib.sha256(f"{server_name}\0{tool_name}".encode()).hexdigest()[:8]
    candidate = f"mcp__{server_name}__{base}__{suffix}"
    counter = 0
    while candidate in used:
        counter += 1
        candidate = f"mcp__{server_name}__{base}__{suffix}_{counter}"
    return candidate


def _coerce_schema(raw_schema: Any) -> dict[str, Any]:
    if isinstance(raw_schema, dict):
        return raw_schema
    return {"type": "object", "properties": {}}


def _coerce_annotations(raw_annotations: Any) -> dict[str, Any]:
    if isinstance(raw_annotations, dict):
        return raw_annotations
    return {}


def _coerce_transport(payload: dict[str, Any]) -> str:
    raw = payload.get("transport")
    if not isinstance(raw, str) or not raw.strip():
        if isinstance(payload.get("url"), str):
            return "http"
        return "stdio"

    normalized = raw.strip().lower().replace("-", "_")
    if normalized in {"http", "streamable_http"}:
        return "http"
    if normalized == "sse":
        return "sse"
    return "stdio"
