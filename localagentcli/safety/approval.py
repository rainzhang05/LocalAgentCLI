"""Task-scoped approval mode management."""

from __future__ import annotations

from enum import Enum

from localagentcli.safety.exec_policy import requires_tool_approval
from localagentcli.safety.posture import SandboxPosture
from localagentcli.tools.base import Tool


class RiskLevel(str, Enum):
    """Risk classification used by the safety layer."""

    NORMAL = "normal"
    HIGH = "high"


class ApprovalManager:
    """Track balanced vs autonomous approval behavior for one task."""

    def __init__(self, mode: str = "balanced"):
        self._default_mode = mode
        self._mode = mode

    @property
    def mode(self) -> str:
        """Return the current approval mode."""
        return self._mode

    def needs_approval(
        self,
        tool: Tool,
        risk_level: RiskLevel,
        *,
        sandbox_posture: SandboxPosture = SandboxPosture.WORKSPACE_WRITE,
    ) -> bool:
        """Determine whether the tool needs explicit approval."""
        return requires_tool_approval(
            tool_name=tool.name,
            tool_is_read_only=tool.is_read_only,
            tool_requires_approval=tool.requires_approval,
            risk_level=risk_level.value,
            approval_mode=self._mode,
            sandbox_posture=sandbox_posture,
        )

    def set_autonomous(self, *, persist_default: bool = True) -> None:
        """Enable autonomous approvals for the current task."""
        self._mode = "autonomous"
        if persist_default:
            self._default_mode = "autonomous"

    def set_balanced(self, *, persist_default: bool = True) -> None:
        """Enable balanced approvals for the current task."""
        self._mode = "balanced"
        if persist_default:
            self._default_mode = "balanced"

    def reset(self) -> None:
        """Return to the configured default approval mode."""
        self._mode = self._default_mode
