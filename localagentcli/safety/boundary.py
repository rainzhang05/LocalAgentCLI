"""Workspace boundary validation and path inspection helpers."""

from __future__ import annotations

import shlex
from pathlib import Path


class WorkspaceBoundaryError(ValueError):
    """Raised when an operation escapes the configured workspace root."""


class WorkspaceBoundary:
    """Validate filesystem access against a fixed workspace root."""

    def __init__(self, workspace_root: Path):
        self._root = workspace_root.expanduser().resolve()

    @property
    def root(self) -> Path:
        """Return the resolved workspace root."""
        return self._root

    def validate_path(self, path: str | Path) -> Path:
        """Resolve a path and reject anything outside the workspace root."""
        raw = Path(path)
        candidate = raw if raw.is_absolute() else self._root / raw
        resolved = candidate.expanduser().resolve(strict=False)
        if not resolved.is_relative_to(self._root):
            raise WorkspaceBoundaryError(
                f"Path '{path}' resolves to '{resolved}' which is outside "
                f"the workspace root '{self._root}'"
            )

        for existing_path in self._existing_components(candidate):
            self.validate_symlink(existing_path)

        return resolved

    def validate_symlink(self, path: Path) -> None:
        """Reject symlinks that resolve outside the workspace root."""
        if not path.is_symlink():
            return
        target = path.resolve(strict=False)
        if not target.is_relative_to(self._root):
            raise WorkspaceBoundaryError(
                f"Symlink '{path}' points to '{target}' outside the workspace root '{self._root}'"
            )

    def relative_path(self, path: Path) -> str:
        """Return a display path relative to the workspace root."""
        return str(path.resolve(strict=False).relative_to(self._root))

    def inspect_command(self, command: str) -> list[str]:
        """Return best-effort warnings for explicit outside-workspace path references."""
        try:
            tokens = shlex.split(command)
        except ValueError:
            return []

        warnings: list[str] = []
        for token in tokens:
            if not self._looks_like_path(token):
                continue
            try:
                self.validate_path(token)
            except WorkspaceBoundaryError:
                warning = f"Command references a path outside the workspace: {token}"
                if warning not in warnings:
                    warnings.append(warning)
        return warnings

    def _existing_components(self, candidate: Path) -> list[Path]:
        """Return existing path components from the candidate back to the root."""
        current = candidate.expanduser()
        components: list[Path] = []
        while True:
            if current.exists() or current.is_symlink():
                components.append(current)
            if current == self._root or current.parent == current:
                break
            current = current.parent
        return components

    def _looks_like_path(self, token: str) -> bool:
        """Best-effort path detection for shell command inspection."""
        if not token or token.startswith("-") or "://" in token:
            return False
        if token.startswith(("~", "/", "./", "../")):
            return True
        return "/" in token or "\\" in token
