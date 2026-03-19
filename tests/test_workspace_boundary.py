"""Tests for workspace boundary enforcement."""

from __future__ import annotations

from pathlib import Path

import pytest

from localagentcli.safety.boundary import WorkspaceBoundary, WorkspaceBoundaryError


class TestWorkspaceBoundary:
    def test_validate_path_accepts_inside_workspace(self, tmp_path: Path):
        boundary = WorkspaceBoundary(tmp_path)
        target = boundary.validate_path("src/app.py")

        assert target == (tmp_path / "src" / "app.py").resolve(strict=False)

    def test_validate_path_rejects_escape(self, tmp_path: Path):
        boundary = WorkspaceBoundary(tmp_path)

        with pytest.raises(WorkspaceBoundaryError):
            boundary.validate_path("../outside.txt")

    def test_validate_path_rejects_symlink_outside_workspace(self, tmp_path: Path):
        outside = tmp_path.parent / "outside.txt"
        outside.write_text("secret", encoding="utf-8")
        link = tmp_path / "link.txt"
        link.symlink_to(outside)
        boundary = WorkspaceBoundary(tmp_path)

        with pytest.raises(WorkspaceBoundaryError):
            boundary.validate_path("link.txt")

    def test_inspect_command_warns_for_outside_workspace_paths(self, tmp_path: Path):
        boundary = WorkspaceBoundary(tmp_path)

        warnings = boundary.inspect_command(f"cat {tmp_path.parent / 'outside.txt'} ../other.txt")

        assert len(warnings) == 2
        assert all("outside the workspace" in warning for warning in warnings)
