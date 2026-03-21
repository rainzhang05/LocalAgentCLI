"""Minimal stdio MCP client and tool discovery manager."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from typing import Any

from localagentcli.tools import DynamicToolSpec, ToolResult


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
    """Configuration for one MCP stdio server."""

    name: str
    command: str
    args: list[str] = field(default_factory=list)
    cwd: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    timeout: float = 15.0


class StdioMcpClient:
    """Very small synchronous MCP stdio client for tools/list and tools/call."""

    def __init__(self, config: McpServerConfig):
        self._config = config
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
            env={**self._config.env} or None,
            bufsize=1,
        )
        self.request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "LocalAgentCLI", "version": "0.1.0"},
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
        payload = self.request(
            "tools/call",
            {"name": name, "arguments": arguments},
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
        process = self._require_process()
        if process.stdout is None:
            raise RuntimeError("MCP process stdout is unavailable.")
        line = process.stdout.readline()
        if not line:
            stderr = ""
            if process.stderr is not None:
                stderr = process.stderr.read().strip()
            raise RuntimeError(stderr or "MCP server closed the connection unexpectedly.")
        parsed = json.loads(line)
        if not isinstance(parsed, dict):
            raise RuntimeError("MCP server returned a non-object JSON message.")
        return parsed

    def _require_process(self) -> subprocess.Popen[str]:
        process = self._process
        if process is None:
            raise RuntimeError("MCP process is not started.")
        return process


class McpManager:
    """Discover and execute MCP tools from configured stdio servers."""

    def __init__(self, servers: list[McpServerConfig]):
        self._servers = servers
        self._clients: dict[str, StdioMcpClient] = {}
        self._tools: dict[str, tuple[str, McpTool]] = {}

    @classmethod
    def from_config(cls, raw_config: dict[str, Any]) -> McpManager:
        servers: list[McpServerConfig] = []
        for name, payload in raw_config.items():
            if not isinstance(payload, dict):
                continue
            command = payload.get("command")
            if not isinstance(command, str) or not command.strip():
                continue
            args = payload.get("args", [])
            env = payload.get("env", {})
            servers.append(
                McpServerConfig(
                    name=name,
                    command=command,
                    args=[str(item) for item in args] if isinstance(args, list) else [],
                    cwd=str(payload["cwd"]) if isinstance(payload.get("cwd"), str) else None,
                    env={str(key): str(value) for key, value in env.items()}
                    if isinstance(env, dict)
                    else {},
                    timeout=float(payload.get("timeout", 15.0) or 15.0),
                )
            )
        return cls(servers)

    def build_dynamic_tool_specs(self) -> list[DynamicToolSpec]:
        specs: list[DynamicToolSpec] = []
        self._tools.clear()
        for server in self._servers:
            client = self._client(server.name)
            for tool in client.list_tools():
                qualified = f"mcp__{server.name}__{_sanitize_tool_name(tool.name)}"
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

    def _client(self, server_name: str) -> StdioMcpClient:
        if server_name in self._clients:
            return self._clients[server_name]
        config = next(server for server in self._servers if server.name == server_name)
        client = StdioMcpClient(config)
        self._clients[server_name] = client
        return client


def _sanitize_tool_name(name: str) -> str:
    allowed = [char if char.isalnum() or char == "_" else "_" for char in name]
    collapsed = "".join(allowed).strip("_")
    return collapsed or "tool"


def _coerce_schema(raw_schema: Any) -> dict[str, Any]:
    if isinstance(raw_schema, dict):
        return raw_schema
    return {"type": "object", "properties": {}}


def _coerce_annotations(raw_annotations: Any) -> dict[str, Any]:
    if isinstance(raw_annotations, dict):
        return raw_annotations
    return {}
