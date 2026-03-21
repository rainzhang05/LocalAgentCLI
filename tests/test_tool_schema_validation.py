"""Tests for shared tool parameters_schema validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from localagentcli.tools import (
    DynamicToolSpec,
    ToolResult,
    ToolRouter,
    create_default_tool_registry,
)
from localagentcli.tools.file_read import FileReadTool
from localagentcli.tools.schema import validate_function_parameters_schema


class TestValidateFunctionParametersSchema:
    def test_accepts_minimal_object_schema(self):
        assert validate_function_parameters_schema({"type": "object", "properties": {}}) == []

    def test_rejects_non_dict(self):
        issues = validate_function_parameters_schema([])
        assert any("dict" in issue.lower() for issue in issues)

    def test_rejects_wrong_top_level_type(self):
        issues = validate_function_parameters_schema({"type": "string", "properties": {}})
        assert any("object" in issue for issue in issues)

    def test_rejects_non_object_property_entry(self):
        issues = validate_function_parameters_schema(
            {"type": "object", "properties": {"path": "not-an-object"}}
        )
        assert any("path" in issue for issue in issues)

    def test_rejects_property_missing_type(self):
        issues = validate_function_parameters_schema(
            {"type": "object", "properties": {"path": {"description": "only"}}}
        )
        assert any("path" in issue and "type" in issue for issue in issues)

    def test_rejects_required_unknown_key(self):
        issues = validate_function_parameters_schema(
            {
                "type": "object",
                "properties": {"a": {"type": "string"}},
                "required": ["missing"],
            }
        )
        assert any("missing" in issue for issue in issues)

    def test_rejects_non_list_required(self):
        issues = validate_function_parameters_schema(
            {"type": "object", "properties": {}, "required": "path"}
        )
        assert any("required" in issue.lower() for issue in issues)


def test_default_registry_tools_pass_validation(tmp_path: Path):
    registry = create_default_tool_registry(tmp_path)
    for tool in registry.list_tools():
        assert validate_function_parameters_schema(tool.parameters_schema) == []
        definition = tool.definition()
        assert definition["name"] == tool.name
        assert "parameters" in definition


def test_tool_definition_raises_on_invalid_subclass(tmp_path: Path):
    class BadSchemaTool(FileReadTool):
        @property
        def name(self) -> str:
            return "bad_schema_tool"

        @property
        def parameters_schema(self) -> dict:
            return {"type": "array"}

    tool = BadSchemaTool(tmp_path)
    with pytest.raises(ValueError, match="invalid parameters_schema"):
        tool.definition()


def test_tool_router_rejects_invalid_dynamic_schema(tmp_path: Path):
    router = ToolRouter(workspace_root=tmp_path)
    with pytest.raises(ValueError, match="invalid parameters_schema"):
        router.register_dynamic_tool(
            DynamicToolSpec(
                name="bad",
                description="x",
                parameters_schema={"type": "string"},
                executor=lambda: ToolResult.success("ok"),
            )
        )
