"""Tests for localagentcli.tools.registry."""

from __future__ import annotations

from localagentcli.tools import (
    DirectoryListTool,
    FileReadTool,
    FileSearchTool,
    FileWriteTool,
    GitCommitTool,
    GitDiffTool,
    GitStatusTool,
    PatchApplyTool,
    ShellExecuteTool,
    TestExecuteTool,
    ToolRegistry,
)


class TestToolRegistry:
    def test_register_and_lookup(self, tmp_path):
        registry = ToolRegistry(
            [
                FileReadTool(tmp_path),
                FileWriteTool(tmp_path),
                ShellExecuteTool(tmp_path),
            ]
        )

        assert registry.get_tool("file_read") is not None
        assert registry.get_tool("file_write") is not None
        assert registry.get_tool("missing") is None

    def test_list_tools_preserves_order(self, tmp_path):
        registry = ToolRegistry(
            [
                FileReadTool(tmp_path),
                FileSearchTool(tmp_path),
                DirectoryListTool(tmp_path),
            ]
        )

        assert [tool.name for tool in registry.list_tools()] == [
            "file_read",
            "file_search",
            "directory_list",
        ]

    def test_get_tool_definitions(self, tmp_path):
        registry = ToolRegistry(
            [
                FileReadTool(tmp_path),
                FileWriteTool(tmp_path),
                PatchApplyTool(tmp_path),
                ShellExecuteTool(tmp_path),
                TestExecuteTool(tmp_path),
                GitStatusTool(tmp_path),
                GitDiffTool(tmp_path),
                GitCommitTool(tmp_path),
            ]
        )

        names = [definition["name"] for definition in registry.get_tool_definitions()]
        assert names == [
            "file_read",
            "file_write",
            "patch_apply",
            "shell_execute",
            "test_execute",
            "git_status",
            "git_diff",
            "git_commit",
        ]

    def test_execute_missing_tool(self, tmp_path):
        registry = ToolRegistry([FileReadTool(tmp_path)])
        result = registry.execute("does_not_exist")

        assert result.status == "error"
        assert "Unknown tool" in result.summary
