"""Create git commits from the workspace."""

from __future__ import annotations

import subprocess

from localagentcli.tools.base import Tool, ToolResult


class GitCommitTool(Tool):
    """Stage files and create a commit."""

    @property
    def name(self) -> str:
        return "git_commit"

    @property
    def description(self) -> str:
        return "Create a git commit, optionally staging specific files first."

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Commit message"},
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional files to stage before committing",
                },
            },
            "required": ["message"],
        }

    def execute(self, message: str, files: list[str] | None = None) -> ToolResult:
        started = self.started_at()
        try:
            staged_files = [self.relative_path(self.resolve_path(path)) for path in files or []]
            if staged_files:
                add_result = subprocess.run(
                    ["git", "add", "--", *staged_files],
                    cwd=self._workspace_root,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if add_result.returncode != 0:
                    return ToolResult.error_result(
                        "git add failed",
                        add_result.stderr.strip() or "git add returned a non-zero exit code",
                        output=add_result.stdout.strip(),
                        exit_code=add_result.returncode,
                        duration=self.started_at() - started,
                    )

            commit_result = subprocess.run(
                ["git", "commit", "-m", message],
                cwd=self._workspace_root,
                capture_output=True,
                text=True,
                check=False,
            )
            output = commit_result.stdout.strip() or commit_result.stderr.strip()
            if commit_result.returncode != 0:
                return ToolResult.error_result(
                    "git commit failed",
                    commit_result.stderr.strip() or "git commit returned a non-zero exit code",
                    output=output,
                    exit_code=commit_result.returncode,
                    duration=self.started_at() - started,
                )
            return ToolResult.success(
                f"Created commit: {message}",
                output=output,
                exit_code=0,
                files_changed=staged_files,
                duration=self.started_at() - started,
            )
        except Exception as exc:
            return ToolResult.error_result(
                "git commit failed",
                str(exc),
                duration=self.started_at() - started,
            )
