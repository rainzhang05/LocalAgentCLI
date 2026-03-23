"""Runtime-aware tool routing and dynamic tool definitions."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from localagentcli.models.model_info import ModelInfo
from localagentcli.tools.adaptation import adapt_tool_definitions
from localagentcli.tools.base import Tool, ToolResult
from localagentcli.tools.registry import ToolRegistry
from localagentcli.tools.schema import validate_function_parameters_schema


@dataclass
class DynamicToolSpec:
    """Model-visible tool definition plus an execution callback."""

    name: str
    description: str
    parameters_schema: dict
    executor: Callable[..., ToolResult]
    requires_approval: bool = True
    is_read_only: bool = False
    required_model_capabilities: tuple[str, ...] = ()
    minimum_model_default_max_tokens: int = 0


class DynamicTool(Tool):
    """Adapter exposing a callback-backed tool through the standard Tool API."""

    def __init__(self, workspace_root: Path, spec: DynamicToolSpec):
        super().__init__(workspace_root)
        self._spec = spec

    @property
    def name(self) -> str:
        return self._spec.name

    @property
    def description(self) -> str:
        return self._spec.description

    @property
    def parameters_schema(self) -> dict:
        return self._spec.parameters_schema

    @property
    def requires_approval(self) -> bool:
        return self._spec.requires_approval

    @property
    def is_read_only(self) -> bool:
        return self._spec.is_read_only

    @property
    def required_model_capabilities(self) -> tuple[str, ...]:
        return self._spec.required_model_capabilities

    @property
    def minimum_model_default_max_tokens(self) -> int:
        return self._spec.minimum_model_default_max_tokens

    def execute(self, **kwargs: object) -> ToolResult:
        return self._spec.executor(**kwargs)


class ToolRouter:
    """Unify built-in and dynamically supplied tools for one runtime."""

    def __init__(
        self,
        workspace_root: Path,
        builtins: ToolRegistry | None = None,
        dynamic_tools: Iterable[DynamicToolSpec] | None = None,
    ) -> None:
        self._workspace_root = workspace_root
        self._builtins = builtins or ToolRegistry()
        self._dynamic: dict[str, DynamicTool] = {}
        if dynamic_tools is not None:
            for tool in dynamic_tools:
                self.register_dynamic_tool(tool)

    def register(self, tool: Tool) -> None:
        """Register a built-in tool implementation."""
        self._builtins.register(tool)

    def register_dynamic_tool(self, spec: DynamicToolSpec) -> None:
        """Register one callback-backed dynamic tool."""
        issues = validate_function_parameters_schema(spec.parameters_schema)
        if issues:
            raise ValueError(
                f"Dynamic tool {spec.name!r} has invalid parameters_schema: {'; '.join(issues)}"
            )
        self._dynamic[spec.name] = DynamicTool(self._workspace_root, spec)

    def get_tool(self, name: str) -> Tool | None:
        """Return a built-in or dynamic tool by name."""
        return self._builtins.get_tool(name) or self._dynamic.get(name)

    def list_tools(self) -> list[Tool]:
        """Return all visible tool implementations."""
        return [*self._builtins.list_tools(), *self._dynamic.values()]

    def get_tool_definitions(self, model_info: ModelInfo | None = None) -> list[dict]:
        """Return model-facing tool definitions for the current turn.

        When `model_info` is supplied, definitions are adapted for the active
        model (capability gates + minimum token budget for advanced tools).
        """
        tools = self.list_tools()
        if model_info is None:
            return [tool.definition() for tool in tools]
        return adapt_tool_definitions(tools, model_info)

    def execute(self, name: str, **kwargs: object) -> ToolResult:
        """Execute a named tool or return an error result if it is unknown."""
        tool = self.get_tool(name)
        if tool is None:
            return ToolResult.error_result(
                f"Unknown tool '{name}'",
                "The requested tool is not registered.",
            )
        return tool.execute(**kwargs)
