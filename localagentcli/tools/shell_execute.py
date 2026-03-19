"""Run shell commands inside the workspace."""

from __future__ import annotations

import subprocess

from localagentcli.tools.base import Tool, ToolResult


def _stringify_output(data: bytes | str | None) -> str:
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return data or ""


def _combine_output(stdout: str, stderr: str) -> str:
    if stdout and stderr:
        return f"{stdout.rstrip()}\n{stderr.rstrip()}".strip()
    return (stdout or stderr).rstrip()


class ShellExecuteTool(Tool):
    """Run an arbitrary shell command."""

    @property
    def name(self) -> str:
        return "shell_execute"

    @property
    def description(self) -> str:
        return "Run a shell command inside the workspace."

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to execute"},
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds",
                    "default": 120,
                },
                "working_dir": {
                    "type": "string",
                    "description": "Working directory relative to the workspace",
                    "default": ".",
                },
            },
            "required": ["command"],
        }

    def execute(
        self,
        command: str,
        timeout: int = 120,
        working_dir: str = ".",
    ) -> ToolResult:
        started = self.started_at()
        try:
            cwd = self.resolve_path(working_dir)
            completed = subprocess.run(
                command,
                shell=True,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            output = _combine_output(completed.stdout, completed.stderr)
            duration = self.started_at() - started
            if completed.returncode == 0:
                return ToolResult.success(
                    f"Command succeeded: {command}",
                    output=output,
                    exit_code=completed.returncode,
                    duration=duration,
                )
            return ToolResult.error_result(
                f"Command failed: {command}",
                output=output,
                error=f"Command exited with status {completed.returncode}",
                exit_code=completed.returncode,
                duration=duration,
            )
        except subprocess.TimeoutExpired as exc:
            output = _combine_output(
                _stringify_output(exc.stdout),
                _stringify_output(exc.stderr),
            )
            return ToolResult.timeout_result(
                f"Command timed out: {command}",
                error=f"Command exceeded timeout of {timeout}s",
                output=output,
                duration=self.started_at() - started,
            )
        except Exception as exc:
            return ToolResult.error_result(
                f"Failed to run command: {command}",
                str(exc),
                duration=self.started_at() - started,
            )
