"""Execution process abstraction for local and remote command runners."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from localagentcli.safety.policy import RuntimeSandboxPolicy

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


class OSSandboxExecProcess(ExecProcess):
    """Wrap command execution with an OS-sandbox command prefix."""

    def __init__(
        self,
        base: ExecProcess,
        *,
        backend: str,
        policy: RuntimeSandboxPolicy,
        container_image: str = "python:3.12-slim",
        container_cpu_limit: str = "",
        container_memory_limit: str = "",
    ):
        self._base = base
        self._backend = backend
        self._policy = policy
        self._container_image = container_image
        self._container_cpu_limit = container_cpu_limit
        self._container_memory_limit = container_memory_limit

    def run(self, command: str, cwd: str, timeout: int) -> ExecProcessResult:
        wrapped = wrap_command_for_os_sandbox(
            command,
            cwd=cwd,
            backend=self._backend,
            policy=self._policy,
            container_image=self._container_image,
            container_cpu_limit=self._container_cpu_limit,
            container_memory_limit=self._container_memory_limit,
        )
        return self._base.run(wrapped, cwd, timeout)


def resolve_os_sandbox_backend(requested_backend: str, *, platform_name: str | None = None) -> str:
    """Resolve configured backend into a concrete backend name."""
    normalized = (requested_backend or "off").strip().lower()
    valid = {"off", "auto", "macos-seatbelt", "linux-bwrap", "container-docker"}
    if normalized not in valid:
        raise ValueError(
            "Invalid safety.os_sandbox_backend. "
            "Expected one of: off, auto, macos-seatbelt, linux-bwrap, container-docker"
        )

    if normalized == "off":
        return "off"
    if normalized == "auto":
        platform_value = (platform_name or sys.platform).lower()
        if platform_value.startswith("darwin"):
            return "macos-seatbelt"
        if platform_value.startswith("linux"):
            return "linux-bwrap"
        return "off"
    return normalized


def build_shell_exec_process(
    *,
    policy: RuntimeSandboxPolicy,
    backend: str,
    container_image: str = "python:3.12-slim",
    container_cpu_limit: str = "",
    container_memory_limit: str = "",
) -> ExecProcess:
    """Build an execution process for shell commands from config + policy.

    - `off` returns a plain `LocalExecProcess`.
    - `auto` prefers platform backend and silently falls back to local when
      backend binaries are unavailable.
    - explicit backends raise when required binaries are unavailable.
    """
    base = LocalExecProcess()
    resolved = resolve_os_sandbox_backend(backend)
    if resolved == "off":
        return base

    if not is_os_sandbox_backend_available(resolved):
        if (backend or "").strip().lower() == "auto":
            return base
        raise RuntimeError(f"Configured OS sandbox backend '{resolved}' is unavailable")

    return OSSandboxExecProcess(
        base,
        backend=resolved,
        policy=policy,
        container_image=container_image,
        container_cpu_limit=container_cpu_limit,
        container_memory_limit=container_memory_limit,
    )


def _is_backend_available(backend: str) -> bool:
    if backend == "macos-seatbelt":
        return shutil.which("sandbox-exec") is not None
    if backend == "linux-bwrap":
        return shutil.which("bwrap") is not None
    if backend == "container-docker":
        return shutil.which("docker") is not None
    return True


def is_os_sandbox_backend_available(backend: str) -> bool:
    """Return whether the resolved backend is currently available."""
    return _is_backend_available(backend)


def wrap_command_for_os_sandbox(
    command: str,
    *,
    cwd: str,
    backend: str,
    policy: RuntimeSandboxPolicy,
    container_image: str = "python:3.12-slim",
    container_cpu_limit: str = "",
    container_memory_limit: str = "",
) -> str:
    """Return a shell command wrapped for the selected OS sandbox backend."""
    posture = _posture_value(policy)
    if posture == "danger-full-access":
        return command

    if backend == "macos-seatbelt":
        return _wrap_command_macos_seatbelt(command, policy)
    if backend == "linux-bwrap":
        return _wrap_command_linux_bwrap(command, cwd, policy)
    if backend == "container-docker":
        return _wrap_command_container_docker(
            command,
            cwd,
            policy,
            image=container_image,
            cpu_limit=container_cpu_limit,
            memory_limit=container_memory_limit,
        )
    return command


def _wrap_command_macos_seatbelt(command: str, policy: RuntimeSandboxPolicy) -> str:
    profile = _build_macos_seatbelt_profile(policy)
    return f"sandbox-exec -p {shlex.quote(profile)} /bin/sh -lc {shlex.quote(command)}"


def _build_macos_seatbelt_profile(policy: RuntimeSandboxPolicy) -> str:
    profile_lines = ["(version 1)", "(allow default)"]
    posture = _posture_value(policy)

    if not policy.network_access:
        profile_lines.append("(deny network*)")

    if posture == "read-only":
        profile_lines.append("(deny file-write*)")
    elif posture == "workspace-write":
        profile_lines.append("(deny file-write*)")
        for root in policy.writable_roots:
            escaped = str(root).replace("\\", "\\\\").replace('"', '\\"')
            profile_lines.append(f'(allow file-write* (subpath "{escaped}"))')

    return "\n".join(profile_lines)


def _wrap_command_linux_bwrap(command: str, cwd: str, policy: RuntimeSandboxPolicy) -> str:
    command_cwd = Path(cwd).expanduser().resolve(strict=False)
    parts = [
        "bwrap",
        "--new-session",
        "--die-with-parent",
        "--unshare-user",
        "--unshare-pid",
        "--ro-bind",
        "/",
        "/",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--chdir",
        str(command_cwd),
    ]

    if not policy.network_access:
        parts.append("--unshare-net")

    if _posture_value(policy) == "workspace-write":
        roots: list[Path] = [command_cwd, *policy.writable_roots]
        for root in _unique_paths(roots):
            root_str = str(root)
            parts.extend(["--bind", root_str, root_str])

    parts.extend(["--", "/bin/sh", "-lc", command])
    return " ".join(shlex.quote(part) for part in parts)


def _wrap_command_container_docker(
    command: str,
    cwd: str,
    policy: RuntimeSandboxPolicy,
    *,
    image: str,
    cpu_limit: str,
    memory_limit: str,
) -> str:
    command_cwd = Path(cwd).expanduser().resolve(strict=False)
    posture = _posture_value(policy)
    mounts: list[tuple[Path, str]] = []

    if posture == "workspace-write":
        writable = set(_unique_paths([command_cwd, *policy.writable_roots]))
        mounts.append((command_cwd, "rw" if command_cwd in writable else "ro"))
        for root in sorted(writable):
            mounts.append((root, "rw"))
    else:
        mounts.append((command_cwd, "ro"))

    parts = [
        "docker",
        "run",
        "--rm",
        "--init",
        "-i",
        "--workdir",
        str(command_cwd),
    ]

    if not policy.network_access:
        parts.extend(["--network", "none"])

    normalized_cpu = cpu_limit.strip()
    if normalized_cpu:
        parts.extend(["--cpus", normalized_cpu])

    normalized_memory = memory_limit.strip()
    if normalized_memory:
        parts.extend(["--memory", normalized_memory])

    seen_mounts: set[tuple[str, str]] = set()
    for root, mode in mounts:
        if not root.exists():
            continue
        key = (str(root), mode)
        if key in seen_mounts:
            continue
        seen_mounts.add(key)
        parts.extend(["--volume", f"{root}:{root}:{mode}"])

    image_name = image.strip() or "python:3.12-slim"
    parts.extend([image_name, "/bin/sh", "-lc", command])
    return " ".join(shlex.quote(part) for part in parts)


def _unique_paths(paths: list[Path]) -> tuple[Path, ...]:
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        unique.append(path)
    return tuple(unique)


def _posture_value(policy: RuntimeSandboxPolicy) -> str:
    posture = policy.posture
    return getattr(posture, "value", str(posture))


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
