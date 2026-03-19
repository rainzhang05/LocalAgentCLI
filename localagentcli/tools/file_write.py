"""Create or overwrite files inside the workspace."""

from __future__ import annotations

from localagentcli.tools.base import Tool, ToolResult


class FileWriteTool(Tool):
    """Write full file contents."""

    @property
    def name(self) -> str:
        return "file_write"

    @property
    def description(self) -> str:
        return "Create or overwrite a file with the provided content."

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Target file path"},
                "content": {"type": "string", "description": "Full file content"},
            },
            "required": ["path", "content"],
        }

    def execute(self, path: str, content: str) -> ToolResult:
        started = self.started_at()
        try:
            target = self.resolve_path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            rel = self.relative_path(target)
            return ToolResult.success(
                f"Wrote {rel}",
                output=content,
                files_changed=[rel],
                duration=self.started_at() - started,
            )
        except Exception as exc:
            return ToolResult.error_result(
                f"Failed to write {path}",
                str(exc),
                duration=self.started_at() - started,
            )
