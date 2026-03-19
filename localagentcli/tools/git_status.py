"""Return repository status information."""

from __future__ import annotations

import subprocess

from localagentcli.tools.base import Tool, ToolResult


class GitStatusTool(Tool):
    """Show git status for the workspace."""

    @property
    def name(self) -> str:
        return "git_status"

    @property
    def description(self) -> str:
        return "Show git status for the current workspace."

    @property
    def parameters_schema(self) -> dict:
        return {"type": "object", "properties": {}}

    @property
    def requires_approval(self) -> bool:
        return False

    @property
    def is_read_only(self) -> bool:
        return True

    def execute(self) -> ToolResult:
        started = self.started_at()
        try:
            completed = subprocess.run(
                ["git", "status", "--short"],
                cwd=self._workspace_root,
                capture_output=True,
                text=True,
                check=False,
            )
            output = completed.stdout.strip()
            if completed.returncode != 0:
                return ToolResult.error_result(
                    "git status failed",
                    completed.stderr.strip() or "git status returned a non-zero exit code",
                    output=output,
                    exit_code=completed.returncode,
                    duration=self.started_at() - started,
                )

            staged = 0
            unstaged = 0
            untracked = 0
            for line in output.splitlines():
                if line.startswith("??"):
                    untracked += 1
                    continue
                if line[:1].strip():
                    staged += 1
                if line[1:2].strip():
                    unstaged += 1

            summary = f"git status: {staged} staged, {unstaged} unstaged, {untracked} untracked"
            return ToolResult.success(
                summary,
                output=output,
                exit_code=0,
                duration=self.started_at() - started,
            )
        except Exception as exc:
            return ToolResult.error_result(
                "git status failed",
                str(exc),
                duration=self.started_at() - started,
            )
