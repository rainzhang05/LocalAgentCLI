"""Tests for MCP-backed dynamic tool discovery and safety integration."""

from __future__ import annotations

import json
from pathlib import Path

from rich.console import Console

from localagentcli.runtime import RuntimeServices
from localagentcli.safety.approval import ApprovalManager, RiskLevel
from localagentcli.safety.boundary import WorkspaceBoundary
from localagentcli.safety.layer import SafetyLayer
from localagentcli.safety.rollback import RollbackManager


def _write_fake_mcp_server(
    tmp_path: Path,
    tools: list[dict],
    *,
    env_probe: bool = False,
) -> Path:
    tools_literal = json.dumps(tools)
    env_branch = ""
    if env_probe:
        env_branch = """
    elif method == "tools/call":
        import os
        p = os.environ.get("LOCALAGENT_MCP_PARENT", "")
        c = os.environ.get("LOCALAGENT_MCP_CHILD", "")
        response = {
            "jsonrpc": "2.0",
            "id": message["id"],
            "result": {
                "content": [{"type": "text", "text": p + "|" + c}],
                "isError": False,
            },
        }
        print(json.dumps(response), flush=True)
"""
    else:
        env_branch = """
    elif method == "tools/call":
        params = message.get("params", {})
        name = params.get("name", "")
        value = params.get("arguments", {}).get("value", "")
        response = {
            "jsonrpc": "2.0",
            "id": message["id"],
            "result": {
                "content": [{"type": "text", "text": name + ":" + str(value)}],
                "isError": False,
            },
        }
        print(json.dumps(response), flush=True)
"""
    script = tmp_path / "fake_mcp_server.py"
    script.write_text(
        f"""
import json
import sys

TOOLS = json.loads({tools_literal!r})

for line in sys.stdin:
    message = json.loads(line)
    method = message.get("method")
    if method == "initialize":
        response = {{
            "jsonrpc": "2.0",
            "id": message["id"],
            "result": {{"protocolVersion": "2024-11-05", "capabilities": {{}}}},
        }}
        print(json.dumps(response), flush=True)
    elif method == "tools/list":
        response = {{
            "jsonrpc": "2.0",
            "id": message["id"],
            "result": {{"tools": TOOLS}},
        }}
        print(json.dumps(response), flush=True)
{env_branch}
    elif method == "notifications/initialized":
        continue
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return script


_DEFAULT_ECHO_TOOL = {
    "name": "echo",
    "description": "Echo input text.",
    "inputSchema": {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
    },
    "annotations": {"readOnlyHint": True},
}


class TestMcpRuntime:
    def test_runtime_services_exposes_mcp_dynamic_tool(self, config, storage, tmp_path: Path):
        server_path = _write_fake_mcp_server(tmp_path, [_DEFAULT_ECHO_TOOL])
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
        assert "hello" in (result.output or "")
        if services.mcp_manager is not None:
            services.mcp_manager.close()

    def test_mcp_subprocess_env_merges_with_os_environ(
        self, monkeypatch, config, storage, tmp_path: Path
    ):
        monkeypatch.setenv("LOCALAGENT_MCP_PARENT", "from_parent")
        server_path = _write_fake_mcp_server(tmp_path, [_DEFAULT_ECHO_TOOL], env_probe=True)
        config._config["mcp_servers"] = {
            "demo": {
                "command": "python",
                "args": [str(server_path)],
                "cwd": str(tmp_path),
                "env": {"LOCALAGENT_MCP_CHILD": "from_child"},
            }
        }
        services = RuntimeServices.create(config, storage, Console(record=True))
        router = services.build_tool_router(tmp_path)
        try:
            result = router.execute("mcp__demo__echo", value="ignored")
            assert result.status == "success"
            assert result.output == "from_parent|from_child"
        finally:
            if services.mcp_manager is not None:
                services.mcp_manager.close()

    def test_mcp_side_effect_tool_requires_approval_under_balanced(
        self, config, storage, tmp_path: Path
    ):
        mutate_tool = {
            "name": "mutate",
            "description": "Side effect.",
            "inputSchema": {
                "type": "object",
                "properties": {"x": {"type": "string"}},
                "required": ["x"],
            },
        }
        server_path = _write_fake_mcp_server(tmp_path, [mutate_tool])
        config._config["mcp_servers"] = {
            "demo": {
                "command": "python",
                "args": [str(server_path)],
                "cwd": str(tmp_path),
            }
        }
        services = RuntimeServices.create(config, storage, Console(record=True))
        router = services.build_tool_router(tmp_path)
        try:
            tool = router.get_tool("mcp__demo__mutate")
            assert tool is not None
            assert tool.requires_approval is True
            assert tool.is_read_only is False
            balanced = ApprovalManager(mode="balanced")
            assert balanced.needs_approval(tool, RiskLevel.NORMAL) is True
            autonomous = ApprovalManager(mode="autonomous")
            assert autonomous.needs_approval(tool, RiskLevel.NORMAL) is False
        finally:
            if services.mcp_manager is not None:
                services.mcp_manager.close()

    def test_mcp_mutating_tool_blocked_in_read_only_sandbox(self, config, storage, tmp_path: Path):
        mutate_tool = {
            "name": "mutate",
            "description": "Side effect.",
            "inputSchema": {
                "type": "object",
                "properties": {"x": {"type": "string"}},
                "required": ["x"],
            },
        }
        server_path = _write_fake_mcp_server(tmp_path, [mutate_tool])
        config._config["mcp_servers"] = {
            "demo": {
                "command": "python",
                "args": [str(server_path)],
                "cwd": str(tmp_path),
            }
        }
        services = RuntimeServices.create(config, storage, Console(record=True))
        router = services.build_tool_router(tmp_path)
        try:
            tool = router.get_tool("mcp__demo__mutate")
            assert tool is not None
            root = tmp_path.resolve()
            approval = ApprovalManager(mode="balanced")
            safety = SafetyLayer(
                approval,
                WorkspaceBoundary(root),
                RollbackManager("test", root / ".cache"),
                sandbox_mode="read-only",
            )
            decision = safety.check_and_approve(tool, {"x": "1"})
            assert decision.blocked
            assert "read-only" in (decision.reason or "").lower()
        finally:
            if services.mcp_manager is not None:
                services.mcp_manager.close()

    def test_mcp_colliding_sanitized_names_get_distinct_qualified_names(
        self, config, storage, tmp_path: Path
    ):
        tools = [
            {
                "name": "foo-bar",
                "description": "A",
                "inputSchema": {
                    "type": "object",
                    "properties": {"v": {"type": "string"}},
                    "required": ["v"],
                },
                "annotations": {"readOnlyHint": True},
            },
            {
                "name": "foo_bar",
                "description": "B",
                "inputSchema": {
                    "type": "object",
                    "properties": {"v": {"type": "string"}},
                    "required": ["v"],
                },
                "annotations": {"readOnlyHint": True},
            },
        ]
        server_path = _write_fake_mcp_server(tmp_path, tools)
        config._config["mcp_servers"] = {
            "demo": {
                "command": "python",
                "args": [str(server_path)],
                "cwd": str(tmp_path),
            }
        }
        services = RuntimeServices.create(config, storage, Console(record=True))
        router = services.build_tool_router(tmp_path)
        try:
            definitions = router.get_tool_definitions()
            names = {d["name"] for d in definitions if d["name"].startswith("mcp__demo__")}
            assert len(names) == 2
            assert "mcp__demo__foo_bar" in names
            other = next(n for n in names if n != "mcp__demo__foo_bar")
            assert other.startswith("mcp__demo__foo_bar__")
            r1 = router.execute("mcp__demo__foo_bar", v="1")
            assert r1.status == "success"
            r2 = router.execute(other, v="2")
            assert r2.status == "success"
            assert "foo-bar" in (r1.output or "") or "foo_bar" in (r1.output or "")
        finally:
            if services.mcp_manager is not None:
                services.mcp_manager.close()
