"""Execution process abstraction for local and remote command runners."""

from __future__ import annotations

import os
import subprocess
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass

_READ_CHUNK_SIZE = 4096
_READ_POLL_SECONDS = 0.05
_MAX_CAPTURE_BYTES = 250_000


@dataclass(frozen=True)
class ExecProcessResult:
    """Normalized process execution result."""

    return_code: int
    output: str
    timed_out: bool = False


class ExecProcess(ABC):
    """Abstract execution process interface."""

    @abstractmethod
    def run(self, command: str, cwd: str, timeout: int) -> ExecProcessResult:
        """Run a command and return a normalized execution result."""


class LocalExecProcess(ExecProcess):
    """Execute commands in the current runtime process (default behavior)."""

    def run(self, command: str, cwd: str, timeout: int) -> ExecProcessResult:
        return run_streaming_command(command, cwd, timeout)


class RemoteExecProcess(ExecProcess):
    """Execution seam for delegating command runs to a remote transport."""

    def __init__(self, runner: Callable[[str, str, int], ExecProcessResult]):
        self._runner = runner

    def run(self, command: str, cwd: str, timeout: int) -> ExecProcessResult:
        return self._runner(command, cwd, timeout)


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


def run_streaming_command(command: str, cwd: str, timeout: int) -> ExecProcessResult:
    """Run a command with POSIX streaming capture or a Windows fallback."""
    if os.name != "nt":
        return run_streaming_command_posix(command, cwd, timeout)
    return run_streaming_command_fallback(command, cwd, timeout)


def run_streaming_command_posix(
    command: str,
    cwd: str,
    timeout: int,
) -> ExecProcessResult:
    """POSIX command execution with PTY capture."""
    # POSIX-only imports are kept local so Windows can import this module
    # without requiring termios/tty support.
    import pty
    import select

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
    return ExecProcessResult(return_code=return_code, output=output, timed_out=timed_out)


def run_streaming_command_fallback(
    command: str,
    cwd: str,
    timeout: int,
) -> ExecProcessResult:
    """Fallback command execution path for Windows."""
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
        return ExecProcessResult(return_code=124, output=output, timed_out=True)

    output = _combine_output(completed.stdout, completed.stderr)
    return ExecProcessResult(return_code=completed.returncode, output=output)
