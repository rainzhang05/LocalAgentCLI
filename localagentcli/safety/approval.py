"""Task-scoped approval mode management."""

from __future__ import annotations

from localagentcli.tools.base import Tool


class ApprovalManager:
    """Track balanced vs autonomous approval behavior for one task."""

    def __init__(self, mode: str = "balanced"):
        self._mode = mode

    @property
    def mode(self) -> str:
        """Return the current approval mode."""
        return self._mode

    def needs_approval(self, tool: Tool) -> bool:
        """Determine whether the tool needs explicit approval."""
        if tool.is_read_only:
            return False
        if not tool.requires_approval:
            return False
        return self._mode != "autonomous"

    def set_autonomous(self) -> None:
        """Enable autonomous approvals for the current task."""
        self._mode = "autonomous"

    def reset(self) -> None:
        """Return to the default balanced mode."""
        self._mode = "balanced"
