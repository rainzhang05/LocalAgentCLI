"""Tests for packaging metadata defined in pyproject.toml."""

from __future__ import annotations

import tomllib
from pathlib import Path


def test_project_scripts_expose_primary_and_alias_commands():
    pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))

    scripts = data["project"]["scripts"]

    assert scripts["localagentcli"] == "localagentcli.__main__:main"
    assert scripts["localagent"] == "localagentcli.__main__:main"
