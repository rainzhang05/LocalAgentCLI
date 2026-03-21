"""Runtime-aware tool routing and dynamic tool definitions."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from localagentcli.tools.base import Tool, ToolResult
from localagentcli.tools.registry import ToolRegistry


@dataclass
class DynamicToolSpec:
    """Model-visible tool definition plus an execution callback."""

    name: str
    description: str
    parameters_schema: dict
    executor: Callable[..., ToolResult]
    requires_approval: bool = True
    is_read_only: bool = False


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
        self._dynamic[spec.name] = DynamicTool(self._workspace_root, spec)

    def get_tool(self, name: str) -> Tool | None:
        """Return a built-in or dynamic tool by name."""
        return self._builtins.get_tool(name) or self._dynamic.get(name)

    def list_tools(self) -> list[Tool]:
        """Return all visible tool implementations."""
        return [*self._builtins.list_tools(), *self._dynamic.values()]

    def get_tool_definitions(self) -> list[dict]:
        """Return model-facing tool definitions for the current turn."""
        return [tool.definition() for tool in self.list_tools()]

    def execute(self, name: str, **kwargs: object) -> ToolResult:
        """Execute a named tool or return an error result if it is unknown."""
        tool = self.get_tool(name)
        if tool is None:
            return ToolResult.error_result(
                f"Unknown tool '{name}'",
                "The requested tool is not registered.",
            )
        return tool.execute(**kwargs)
