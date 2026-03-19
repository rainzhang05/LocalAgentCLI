"""Return workspace diffs."""

from __future__ import annotations

import subprocess

from localagentcli.tools.base import Tool, ToolResult


class GitDiffTool(Tool):
    """Show git diff output."""

    @property
    def name(self) -> str:
        return "git_diff"

    @property
    def description(self) -> str:
        return "Show git diff output, optionally staged or path-scoped."

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "staged": {"type": "boolean", "description": "Show staged diff", "default": False},
                "path": {"type": "string", "description": "Optional file path"},
            },
        }

    @property
    def requires_approval(self) -> bool:
        return False

    @property
    def is_read_only(self) -> bool:
        return True

    def execute(self, staged: bool = False, path: str | None = None) -> ToolResult:
        started = self.started_at()
        try:
            command = ["git", "diff"]
            if staged:
                command.append("--staged")
            if path:
                command.extend(["--", self.relative_path(self.resolve_path(path))])
            completed = subprocess.run(
                command,
                cwd=self._workspace_root,
                capture_output=True,
                text=True,
                check=False,
            )
            output = completed.stdout.strip()
            if completed.returncode != 0:
                return ToolResult.error_result(
                    "git diff failed",
                    completed.stderr.strip() or "git diff returned a non-zero exit code",
                    output=output,
                    exit_code=completed.returncode,
                    duration=self.started_at() - started,
                )
            label = "staged" if staged else "working tree"
            return ToolResult.success(
                f"Read git diff ({label})",
                output=output,
                exit_code=0,
                duration=self.started_at() - started,
            )
        except Exception as exc:
            return ToolResult.error_result(
                "git diff failed",
                str(exc),
                duration=self.started_at() - started,
            )
