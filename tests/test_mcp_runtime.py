"""Tests for MCP-backed dynamic tool discovery."""

from __future__ import annotations

from pathlib import Path

from rich.console import Console

from localagentcli.runtime import RuntimeServices


def _write_fake_mcp_server(tmp_path: Path) -> Path:
    script = tmp_path / "fake_mcp_server.py"
    script.write_text(
        """
import json
import sys

for line in sys.stdin:
    message = json.loads(line)
    method = message.get("method")
    if method == "initialize":
        response = {
            "jsonrpc": "2.0",
            "id": message["id"],
            "result": {"protocolVersion": "2024-11-05", "capabilities": {}},
        }
        print(json.dumps(response), flush=True)
    elif method == "tools/list":
        response = {
            "jsonrpc": "2.0",
            "id": message["id"],
            "result": {
                "tools": [
                    {
                        "name": "echo",
                        "description": "Echo input text.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"value": {"type": "string"}},
                            "required": ["value"],
                        },
                        "annotations": {"readOnlyHint": True},
                    }
                ]
            },
        }
        print(json.dumps(response), flush=True)
    elif method == "tools/call":
        value = message.get("params", {}).get("arguments", {}).get("value", "")
        response = {
            "jsonrpc": "2.0",
            "id": message["id"],
            "result": {"content": [{"type": "text", "text": str(value)}], "isError": False},
        }
        print(json.dumps(response), flush=True)
    elif method == "notifications/initialized":
        continue
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return script


class TestMcpRuntime:
    def test_runtime_services_exposes_mcp_dynamic_tool(self, config, storage, tmp_path: Path):
        server_path = _write_fake_mcp_server(tmp_path)
        config._config["mcp_servers"] = {
            "demo": {
                "command": "python",
                "args": [str(server_path)],
                "cwd": str(tmp_path),
            }
        }
        services = RuntimeServices.create(config, storage, Console(record=True))

        router = services.build_tool_router(tmp_path)
        definitions = router.get_tool_definitions()

        assert any(definition["name"] == "mcp__demo__echo" for definition in definitions)
        result = router.execute("mcp__demo__echo", value="hello")
        assert result.status == "success"
        assert result.output == "hello"
        if services.mcp_manager is not None:
            services.mcp_manager.close()
