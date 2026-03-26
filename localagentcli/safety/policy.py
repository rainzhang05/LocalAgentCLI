"""Typed runtime sandbox policy derived from sandbox posture."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from localagentcli.safety.posture import SandboxPosture


@dataclass(frozen=True)
class RuntimeSandboxPolicy:
    """Normalized sandbox policy for runtime safety decisions.

    This model keeps current LocalAgentCLI postures while adding explicit
    writable-roots and network-access fields needed by later containment slices.
    """

    posture: SandboxPosture
    writable_roots: tuple[Path, ...]
    network_access: bool

    @classmethod
    def from_posture(cls, posture: SandboxPosture, workspace_root: Path) -> RuntimeSandboxPolicy:
        root = workspace_root.expanduser().resolve()
        if posture is SandboxPosture.DANGER_FULL_ACCESS:
            return cls(
                posture=posture,
                writable_roots=(),
                network_access=True,
            )
        if posture is SandboxPosture.READ_ONLY:
            return cls(
                posture=posture,
                writable_roots=(),
                network_access=False,
            )
        return cls(
            posture=posture,
            writable_roots=(root,),
            network_access=False,
        )

    def can_write_path(self, path: Path) -> bool:
        """Return whether this policy allows writing to the given path."""
        if self.posture is SandboxPosture.DANGER_FULL_ACCESS:
            return True
        if self.posture is SandboxPosture.READ_ONLY:
            return False

        resolved = path.expanduser().resolve(strict=False)
        return any(
            resolved == writable_root or writable_root in resolved.parents
            for writable_root in self.writable_roots
        )
