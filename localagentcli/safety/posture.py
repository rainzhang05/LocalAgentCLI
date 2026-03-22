"""Typed runtime sandbox posture (config strings map to a single enum)."""

from __future__ import annotations

from enum import StrEnum

from localagentcli.tools.base import Tool


class SandboxPosture(StrEnum):
    """Application-layer sandbox posture for tool execution.

    This does not imply OS-level process isolation; see product docs for
    containment guarantees.
    """

    WORKSPACE_WRITE = "workspace-write"
    READ_ONLY = "read-only"
    DANGER_FULL_ACCESS = "danger-full-access"

    def side_effect_block_reason(self, tool: Tool) -> str | None:
        """Return a block message if this posture forbids the tool, else None."""
        if self is SandboxPosture.DANGER_FULL_ACCESS:
            return None
        if self is SandboxPosture.READ_ONLY and not tool.is_read_only:
            return "Runtime sandbox mode 'read-only' blocks side-effecting tools."
        return None


def parse_sandbox_mode(value: str) -> SandboxPosture:
    """Parse a config or CLI sandbox mode string into a posture."""
    try:
        return SandboxPosture(value)
    except ValueError as exc:
        valid = ", ".join(sorted(m.value for m in SandboxPosture))
        raise ValueError(
            f"Invalid sandbox mode {value!r}. Expected one of: {valid}",
        ) from exc
