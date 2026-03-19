"""List directory contents from the workspace."""

from __future__ import annotations

from pathlib import Path

from localagentcli.tools.base import Tool, ToolResult


class DirectoryListTool(Tool):
    """List files and directories inside the workspace."""

    @property
    def name(self) -> str:
        return "directory_list"

    @property
    def description(self) -> str:
        return "List the contents of a directory, optionally recursively."

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path"},
                "recursive": {
                    "type": "boolean",
                    "description": "Whether to recurse into subdirectories",
                    "default": False,
                },
            },
            "required": ["path"],
        }

    @property
    def requires_approval(self) -> bool:
        return False

    @property
    def is_read_only(self) -> bool:
        return True

    def execute(self, path: str, recursive: bool = False) -> ToolResult:
        started = self.started_at()
        try:
            base = self.resolve_path(path)
            if not base.exists() or not base.is_dir():
                raise FileNotFoundError(f"Directory '{path}' not found")

            entries = sorted(base.rglob("*") if recursive else base.iterdir())
            output_lines = [
                self._format_entry(base),
                *[self._format_entry(entry) for entry in entries],
            ]
            summary = f"Listed {len(entries)} item(s) in {self.relative_path(base)}"
            return ToolResult.success(
                summary,
                output="\n".join(output_lines),
                duration=self.started_at() - started,
            )
        except Exception as exc:
            return ToolResult.error_result(
                f"Failed to list directory {path}",
                str(exc),
                duration=self.started_at() - started,
            )

    def _format_entry(self, entry: Path) -> str:
        rel = self.relative_path(entry)
        if entry.is_dir():
            return f"dir  {rel}/"
        size = entry.stat().st_size
        return f"file {size:>8d} {rel}"
