"""Run shell commands inside the workspace."""

from __future__ import annotations

import os
import pty
import select
import subprocess
import time
from dataclasses import dataclass

from localagentcli.tools.base import Tool, ToolResult

_READ_CHUNK_SIZE = 4096
_READ_POLL_SECONDS = 0.05
_MAX_CAPTURE_BYTES = 250_000


@dataclass(frozen=True)
class _StreamingCommandResult:
    return_code: int
    output: str
    timed_out: bool = False


def _stringify_output(data: bytes | str | None) -> str:
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return data or ""


def _combine_output(stdout: str, stderr: str) -> str:
    if stdout and stderr:
        return f"{stdout.rstrip()}\n{stderr.rstrip()}".strip()
    return (stdout or stderr).rstrip()


def _truncate_captured_output(output: bytes) -> str:
    if len(output) <= _MAX_CAPTURE_BYTES:
        return output.decode("utf-8", errors="replace").rstrip()
    clipped = output[:_MAX_CAPTURE_BYTES].decode("utf-8", errors="replace").rstrip()
    return f"{clipped}\n...[output truncated at {_MAX_CAPTURE_BYTES} bytes]..."


def _run_streaming_command(command: str, cwd: str, timeout: int) -> _StreamingCommandResult:
    if os.name != "nt":
        return _run_streaming_command_posix(command, cwd, timeout)
    return _run_streaming_command_fallback(command, cwd, timeout)


def _run_streaming_command_posix(
    command: str,
    cwd: str,
    timeout: int,
) -> _StreamingCommandResult:
    master_fd, slave_fd = pty.openpty()
    process = subprocess.Popen(  # noqa: S602
        command,
        shell=True,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=slave_fd,
        stderr=slave_fd,
        close_fds=True,
    )
    os.close(slave_fd)

    captured = bytearray()
    timed_out = False
    start = time.monotonic()

    try:
        while True:
            elapsed = time.monotonic() - start
            if elapsed > timeout:
                timed_out = True
                process.kill()
                break

            ready, _write, _err = select.select([master_fd], [], [], _READ_POLL_SECONDS)
            if master_fd in ready:
                try:
                    chunk = os.read(master_fd, _READ_CHUNK_SIZE)
                except OSError:
                    chunk = b""
                if chunk:
                    remaining = _MAX_CAPTURE_BYTES - len(captured)
                    if remaining > 0:
                        captured.extend(chunk[:remaining])

            if process.poll() is not None and master_fd not in ready:
                break

        while True:
            try:
                chunk = os.read(master_fd, _READ_CHUNK_SIZE)
            except OSError:
                break
            if not chunk:
                break
            remaining = _MAX_CAPTURE_BYTES - len(captured)
            if remaining > 0:
                captured.extend(chunk[:remaining])
    finally:
        try:
            os.close(master_fd)
        except OSError:
            pass

    return_code = process.wait()
    output = _truncate_captured_output(bytes(captured))
    return _StreamingCommandResult(return_code=return_code, output=output, timed_out=timed_out)


def _run_streaming_command_fallback(
    command: str,
    cwd: str,
    timeout: int,
) -> _StreamingCommandResult:
    try:
        completed = subprocess.run(  # noqa: S602
            command,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        output = _combine_output(
            _stringify_output(exc.stdout),
            _stringify_output(exc.stderr),
        )
        return _StreamingCommandResult(return_code=124, output=output, timed_out=True)

    output = _combine_output(completed.stdout, completed.stderr)
    return _StreamingCommandResult(return_code=completed.returncode, output=output)


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
            streamed = _run_streaming_command(command, str(cwd), timeout)
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
