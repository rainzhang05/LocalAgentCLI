"""Typed runtime sandbox policy derived from sandbox posture."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

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
    def from_posture(
        cls,
        posture: SandboxPosture,
        workspace_root: Path,
        *,
        writable_roots: Iterable[Path] | None = None,
        network_access_override: bool | None = None,
    ) -> RuntimeSandboxPolicy:
        root = workspace_root.expanduser().resolve()
        extra_roots = _normalize_roots(writable_roots)

        if posture is SandboxPosture.DANGER_FULL_ACCESS:
            network_access = (
                network_access_override if network_access_override is not None else True
            )
            return cls(
                posture=posture,
                writable_roots=(),
                network_access=network_access,
            )

        if posture is SandboxPosture.READ_ONLY:
            network_access = (
                network_access_override if network_access_override is not None else False
            )
            return cls(
                posture=posture,
                writable_roots=(),
                network_access=network_access,
            )

        roots = _normalize_roots((root, *extra_roots))
        network_access = network_access_override if network_access_override is not None else False
        return cls(
            posture=posture,
            writable_roots=roots,
            network_access=network_access,
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


def _normalize_roots(roots: Iterable[Path] | None) -> tuple[Path, ...]:
    if roots is None:
        return ()
    normalized: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        resolved = root.expanduser().resolve(strict=False)
        if resolved in seen:
            continue
        seen.add(resolved)
        normalized.append(resolved)
    return tuple(normalized)
