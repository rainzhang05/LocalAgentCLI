"""Registry for tools available to the agent loop."""

from __future__ import annotations

from collections.abc import Iterable

from localagentcli.models.model_info import ModelInfo
from localagentcli.tools.adaptation import adapt_tool_definitions
from localagentcli.tools.base import Tool, ToolResult


class ToolRegistry:
    """Store and expose tool implementations."""

    def __init__(self, tools: Iterable[Tool] | None = None):
        self._tools: dict[str, Tool] = {}
        if tools is not None:
            for tool in tools:
                self.register(tool)

    def register(self, tool: Tool) -> None:
        """Register a tool instance by name."""
        self._tools[tool.name] = tool

    def get_tool(self, name: str) -> Tool | None:
        """Return a tool by name, or None if unknown."""
        return self._tools.get(name)

    def list_tools(self) -> list[Tool]:
        """Return all registered tools."""
        return list(self._tools.values())

    def get_tool_definitions(self, model_info: ModelInfo | None = None) -> list[dict]:
        """Return model-facing tool definitions.

        When `model_info` is supplied, definitions are adapted for the active
        model (capability gates + minimum token budget for advanced tools).
        """
        tools = list(self._tools.values())
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
