"""Execution approval policy decisions for safety checks."""

from __future__ import annotations

from localagentcli.safety.posture import SandboxPosture


def requires_tool_approval(
    *,
    tool_name: str,
    tool_is_read_only: bool,
    tool_requires_approval: bool,
    risk_level: str,
    approval_mode: str,
    sandbox_posture: SandboxPosture,
) -> bool:
    """Return whether a tool call requires interactive approval.

    High-risk actions generally require approval. The Phase 15 Slice 1 policy
    exception is for dangerous shell execution in explicit unsandboxed posture
    (`danger-full-access`) while autonomous approvals are enabled.
    """
    if risk_level == "high":
        if (
            tool_name == "shell_execute"
            and approval_mode == "autonomous"
            and sandbox_posture is SandboxPosture.DANGER_FULL_ACCESS
        ):
            return False
        return True

    if tool_is_read_only:
        return False
    if not tool_requires_approval:
        return False
    return approval_mode != "autonomous"
