"""Registry for tools available to the agent loop."""

from __future__ import annotations

from collections.abc import Iterable

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

    def get_tool_definitions(self) -> list[dict]:
        """Return model-facing tool definitions."""
        return [tool.definition() for tool in self._tools.values()]

    def execute(self, name: str, **kwargs: object) -> ToolResult:
        """Execute a named tool or return an error result if it is unknown."""
        tool = self.get_tool(name)
        if tool is None:
            return ToolResult.error_result(
                f"Unknown tool '{name}'",
                "The requested tool is not registered.",
            )
        return tool.execute(**kwargs)
