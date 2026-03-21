"""Tests for runtime-aware tool routing."""

from __future__ import annotations

from pathlib import Path

from localagentcli.tools import (
    DynamicToolSpec,
    ToolResult,
    ToolRouter,
    create_default_tool_registry,
)


class TestToolRouter:
    def test_dynamic_tool_appears_in_definitions_and_executes(self, tmp_path: Path):
        router = ToolRouter(
            workspace_root=tmp_path,
            builtins=create_default_tool_registry(tmp_path),
        )
        router.register_dynamic_tool(
            DynamicToolSpec(
                name="dynamic_echo",
                description="Return a payload for testing.",
                parameters_schema={
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                },
                executor=lambda value: ToolResult.success(
                    "Echoed value.",
                    output=str(value),
                ),
                requires_approval=False,
                is_read_only=True,
            )
        )

        definitions = router.get_tool_definitions()

        assert any(definition["name"] == "dynamic_echo" for definition in definitions)
        result = router.execute("dynamic_echo", value="hello")
        assert result.status == "success"
        assert result.output == "hello"
