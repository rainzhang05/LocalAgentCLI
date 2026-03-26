"""Run shell commands inside the workspace."""

from __future__ import annotations

from pathlib import Path

from localagentcli.tools.base import Tool, ToolResult
from localagentcli.tools.exec_process import (
    ExecProcess,
    ExecProcessResult,
    LocalExecProcess,
    run_streaming_command,
    run_streaming_command_fallback,
    run_streaming_command_posix,
)


def _run_streaming_command(command: str, cwd: str, timeout: int) -> ExecProcessResult:
    """Backwards-compatible wrapper for local streaming execution."""
    return run_streaming_command(command, cwd, timeout)


def _run_streaming_command_posix(command: str, cwd: str, timeout: int) -> ExecProcessResult:
    """Backwards-compatible wrapper for POSIX streaming execution."""
    return run_streaming_command_posix(command, cwd, timeout)


def _run_streaming_command_fallback(
    command: str,
    cwd: str,
    timeout: int,
) -> ExecProcessResult:
    """Backwards-compatible wrapper for fallback command execution."""
    return run_streaming_command_fallback(command, cwd, timeout)


class ShellExecuteTool(Tool):
    """Run an arbitrary shell command."""

    def __init__(self, workspace_root: Path, exec_process: ExecProcess | None = None):
        super().__init__(workspace_root)
        self._exec_process = exec_process or LocalExecProcess()

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
            streamed = self._exec_process.run(command, str(cwd), timeout)
            duration = self.started_at() - started
            if streamed.timed_out:
                return ToolResult.timeout_result(
                    f"Command timed out: {command}",
                    error=f"Command exceeded timeout of {timeout}s",
                    output=streamed.output,
                    duration=duration,
                )
            if streamed.return_code == 0:
                return ToolResult.success(
                    f"Command succeeded: {command}",
                    output=streamed.output,
                    exit_code=streamed.return_code,
                    duration=duration,
                )
            return ToolResult.error_result(
                f"Command failed: {command}",
                output=streamed.output,
                error=f"Command exited with status {streamed.return_code}",
                exit_code=streamed.return_code,
                duration=duration,
            )
        except Exception as exc:
            return ToolResult.error_result(
                f"Failed to run command: {command}",
                str(exc),
                duration=self.started_at() - started,
            )
