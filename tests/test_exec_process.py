"""Tests for execution process backends and OS-sandbox wrapping."""

from __future__ import annotations

from pathlib import Path

import pytest

from localagentcli.safety.policy import RuntimeSandboxPolicy
from localagentcli.safety.posture import SandboxPosture
from localagentcli.tools import exec_process


def test_resolve_os_sandbox_backend_auto_by_platform():
    assert (
        exec_process.resolve_os_sandbox_backend("auto", platform_name="darwin") == "macos-seatbelt"
    )
    assert exec_process.resolve_os_sandbox_backend("auto", platform_name="linux") == "linux-bwrap"
    assert exec_process.resolve_os_sandbox_backend("auto", platform_name="win32") == "off"


def test_resolve_os_sandbox_backend_rejects_unknown_values():
    with pytest.raises(ValueError, match="Invalid safety.os_sandbox_backend"):
        exec_process.resolve_os_sandbox_backend("invalid")


def test_build_shell_exec_process_auto_falls_back_when_backend_unavailable(
    monkeypatch, tmp_path: Path
):
    policy = RuntimeSandboxPolicy.from_posture(SandboxPosture.WORKSPACE_WRITE, tmp_path)
    monkeypatch.setattr(exec_process.sys, "platform", "darwin", raising=False)
    monkeypatch.setattr(exec_process.shutil, "which", lambda _name: None)

    built = exec_process.build_shell_exec_process(policy=policy, backend="auto")

    assert isinstance(built, exec_process.LocalExecProcess)


def test_build_shell_exec_process_explicit_backend_requires_binary(monkeypatch, tmp_path: Path):
    policy = RuntimeSandboxPolicy.from_posture(SandboxPosture.WORKSPACE_WRITE, tmp_path)
    monkeypatch.setattr(exec_process.shutil, "which", lambda _name: None)

    with pytest.raises(RuntimeError, match="unavailable"):
        exec_process.build_shell_exec_process(policy=policy, backend="macos-seatbelt")


def test_wrap_command_for_os_sandbox_danger_full_access_is_passthrough(tmp_path: Path):
    policy = RuntimeSandboxPolicy.from_posture(SandboxPosture.DANGER_FULL_ACCESS, tmp_path)

    wrapped = exec_process.wrap_command_for_os_sandbox(
        "echo hello",
        cwd=str(tmp_path),
        backend="macos-seatbelt",
        policy=policy,
    )

    assert wrapped == "echo hello"


def test_wrap_command_for_os_sandbox_macos_profile_includes_network_and_writes(tmp_path: Path):
    policy = RuntimeSandboxPolicy.from_posture(SandboxPosture.WORKSPACE_WRITE, tmp_path)

    wrapped = exec_process.wrap_command_for_os_sandbox(
        "echo hi",
        cwd=str(tmp_path),
        backend="macos-seatbelt",
        policy=policy,
    )

    assert "sandbox-exec -p" in wrapped
    assert "/bin/sh -lc" in wrapped
    profile = exec_process._build_macos_seatbelt_profile(policy)
    assert "(deny network*)" in profile
    assert "(deny file-write*)" in profile


def test_wrap_command_for_os_sandbox_linux_bwrap_includes_net_and_bind(tmp_path: Path):
    policy = RuntimeSandboxPolicy.from_posture(SandboxPosture.WORKSPACE_WRITE, tmp_path)

    wrapped = exec_process.wrap_command_for_os_sandbox(
        "echo hi",
        cwd=str(tmp_path),
        backend="linux-bwrap",
        policy=policy,
    )

    assert "bwrap" in wrapped
    assert "--unshare-net" in wrapped
    assert "--bind" in wrapped
