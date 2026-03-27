"""Tests for typed runtime sandbox policy behavior."""

from __future__ import annotations

from pathlib import Path

from localagentcli.safety.policy import RuntimeSandboxPolicy
from localagentcli.safety.posture import SandboxPosture


def test_policy_from_posture_workspace_write_includes_workspace_root(tmp_path: Path):
    policy = RuntimeSandboxPolicy.from_posture(SandboxPosture.WORKSPACE_WRITE, tmp_path)

    assert policy.posture is SandboxPosture.WORKSPACE_WRITE
    assert policy.network_access is False
    assert policy.writable_roots == (tmp_path.resolve(),)


def test_policy_from_posture_read_only_has_no_writable_roots(tmp_path: Path):
    policy = RuntimeSandboxPolicy.from_posture(SandboxPosture.READ_ONLY, tmp_path)

    assert policy.posture is SandboxPosture.READ_ONLY
    assert policy.network_access is False
    assert policy.writable_roots == ()


def test_policy_from_posture_danger_full_access_enables_network(tmp_path: Path):
    policy = RuntimeSandboxPolicy.from_posture(SandboxPosture.DANGER_FULL_ACCESS, tmp_path)

    assert policy.posture is SandboxPosture.DANGER_FULL_ACCESS
    assert policy.network_access is True
    assert policy.writable_roots == ()


def test_can_write_path_respects_posture_and_roots(tmp_path: Path):
    workspace_policy = RuntimeSandboxPolicy.from_posture(SandboxPosture.WORKSPACE_WRITE, tmp_path)
    inside = tmp_path / "src" / "app.py"
    outside = tmp_path.parent / "outside.txt"

    assert workspace_policy.can_write_path(inside) is True
    assert workspace_policy.can_write_path(outside) is False

    assert (
        RuntimeSandboxPolicy.from_posture(
            SandboxPosture.READ_ONLY,
            tmp_path,
        ).can_write_path(inside)
        is False
    )
    assert (
        RuntimeSandboxPolicy.from_posture(
            SandboxPosture.DANGER_FULL_ACCESS,
            tmp_path,
        ).can_write_path(outside)
        is True
    )


def test_policy_from_posture_applies_network_override(tmp_path: Path):
    allow_net = RuntimeSandboxPolicy.from_posture(
        SandboxPosture.WORKSPACE_WRITE,
        tmp_path,
        network_access_override=True,
    )
    deny_net = RuntimeSandboxPolicy.from_posture(
        SandboxPosture.DANGER_FULL_ACCESS,
        tmp_path,
        network_access_override=False,
    )

    assert allow_net.network_access is True
    assert deny_net.network_access is False


def test_policy_from_posture_adds_extra_writable_roots(tmp_path: Path):
    extra = tmp_path.parent / "shared"
    policy = RuntimeSandboxPolicy.from_posture(
        SandboxPosture.WORKSPACE_WRITE,
        tmp_path,
        writable_roots=(extra,),
    )

    assert tmp_path.resolve() in policy.writable_roots
    assert extra.resolve() in policy.writable_roots
