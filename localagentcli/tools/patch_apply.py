"""Apply an exact-match text replacement to a file."""

from __future__ import annotations

from localagentcli.tools.base import Tool, ToolResult


class PatchApplyTool(Tool):
    """Apply a single exact replacement in a file."""

    @property
    def name(self) -> str:
        return "patch_apply"

    @property
    def description(self) -> str:
        return "Apply a targeted text replacement in an existing file."

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Target file path"},
                "old_text": {"type": "string", "description": "Text to replace exactly once"},
                "new_text": {"type": "string", "description": "Replacement text"},
            },
            "required": ["path", "old_text", "new_text"],
        }

    def execute(self, path: str, old_text: str, new_text: str) -> ToolResult:
        started = self.started_at()
        try:
            target = self.resolve_path(path)
            if not target.exists():
                raise FileNotFoundError(f"File '{path}' not found")
            original = target.read_text(encoding="utf-8")
            occurrences = original.count(old_text)
            if occurrences == 0:
                raise ValueError("old_text did not match any content")
            if occurrences > 1:
                raise ValueError("old_text matched more than one location")

            updated = original.replace(old_text, new_text, 1)
            target.write_text(updated, encoding="utf-8")
            rel = self.relative_path(target)
            return ToolResult.success(
                f"Patched {rel}",
                output=updated,
                files_changed=[rel],
                duration=self.started_at() - started,
            )
        except Exception as exc:
            return ToolResult.error_result(
                f"Failed to patch {path}",
                str(exc),
                duration=self.started_at() - started,
            )
