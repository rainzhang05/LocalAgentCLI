"""Tool registry construction helpers."""

from __future__ import annotations

from pathlib import Path

from localagentcli.tools.base import Tool, ToolResult
from localagentcli.tools.directory_list import DirectoryListTool
from localagentcli.tools.file_read import FileReadTool
from localagentcli.tools.file_search import FileSearchTool
from localagentcli.tools.file_write import FileWriteTool
from localagentcli.tools.git_commit import GitCommitTool
from localagentcli.tools.git_diff import GitDiffTool
from localagentcli.tools.git_status import GitStatusTool
from localagentcli.tools.patch_apply import PatchApplyTool
from localagentcli.tools.python_repl import PythonReplTool
from localagentcli.tools.registry import ToolRegistry
from localagentcli.tools.router import DynamicToolSpec, ToolRouter
from localagentcli.tools.shell_execute import ShellExecuteTool
from localagentcli.tools.test_execute import TestExecuteTool


def create_default_tool_registry(workspace_root: Path) -> ToolRegistry:
    """Build a registry containing the core Phase 5 tools."""
    registry = ToolRegistry()
    for tool in [
        FileReadTool(workspace_root),
        FileSearchTool(workspace_root),
        DirectoryListTool(workspace_root),
        FileWriteTool(workspace_root),
        PatchApplyTool(workspace_root),
        PythonReplTool(workspace_root),
        ShellExecuteTool(workspace_root),
        TestExecuteTool(workspace_root),
        GitStatusTool(workspace_root),
        GitDiffTool(workspace_root),
        GitCommitTool(workspace_root),
    ]:
        registry.register(tool)
    return registry


__all__ = [
    "Tool",
    "ToolRegistry",
    "ToolRouter",
    "ToolResult",
    "DynamicToolSpec",
    "PythonReplTool",
    "create_default_tool_registry",
]
