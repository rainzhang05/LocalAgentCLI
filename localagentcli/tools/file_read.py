"""Read file contents from the workspace."""

from __future__ import annotations

from localagentcli.tools.base import Tool, ToolResult


class FileReadTool(Tool):
    """Read a file as text, with optional line slicing."""

    @property
    def name(self) -> str:
        return "file_read"

    @property
    def description(self) -> str:
        return "Read the contents of a file relative to the workspace."

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Workspace-relative file path"},
                "offset": {"type": "integer", "description": "Starting line number", "default": 0},
                "limit": {"type": "integer", "description": "Maximum number of lines to read"},
            },
            "required": ["path"],
        }

    @property
    def requires_approval(self) -> bool:
        return False

    @property
    def is_read_only(self) -> bool:
        return True

    def execute(self, path: str, offset: int = 0, limit: int | None = None) -> ToolResult:
        started = self.started_at()
        try:
            target = self.resolve_path(path)
            if not target.exists():
                raise FileNotFoundError(f"File '{path}' not found")
            if not target.is_file():
                raise IsADirectoryError(f"Path '{path}' is not a file")

            data = target.read_bytes()
            if b"\x00" in data:
                output = f"{len(data)} bytes (binary)"
                return ToolResult.success(
                    f"Read binary file {self.relative_path(target)}",
                    output=output,
                    duration=self.started_at() - started,
                )

            text = data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
            lines = text.splitlines(keepends=True)
            start_line = max(offset, 0)
            end_line = len(lines) if limit is None else start_line + max(limit, 0)
            selected = lines[start_line:end_line]
            output = "".join(selected)
            summary = f"Read {len(selected)} line(s) from {self.relative_path(target)}"
            return ToolResult.success(
                summary,
                output=output,
                duration=self.started_at() - started,
            )
        except UnicodeDecodeError:
            output = f"{len(data)} bytes (binary)"
            return ToolResult.success(
                f"Read binary file {path}",
                output=output,
                duration=self.started_at() - started,
            )
        except Exception as exc:
            return ToolResult.error_result(
                f"Failed to read {path}",
                str(exc),
                duration=self.started_at() - started,
            )
