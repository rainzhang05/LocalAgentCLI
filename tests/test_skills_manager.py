"""Tests for localagentcli.skills.manager."""

from __future__ import annotations

from pathlib import Path

from localagentcli.skills import SKILL_FILENAME, SkillsManager


def test_install_and_remove_skill_directory(tmp_path: Path):
    manager = SkillsManager(tmp_path / "skills")
    source = tmp_path / "source_skill"
    source.mkdir()
    (source / SKILL_FILENAME).write_text("Use ripgrep first.", encoding="utf-8")

    installed = manager.install_from_path(source, name="search")

    assert installed.name == "search"
    assert installed.source == "installed"
    assert (manager.skills_dir / "search" / SKILL_FILENAME).exists()

    removed = manager.remove("search")
    assert removed.name == "search"
    assert not (manager.skills_dir / "search").exists()


def test_list_installed_skills_reads_skill_md(tmp_path: Path):
    manager = SkillsManager(tmp_path / "skills")
    skill_dir = manager.skills_dir / "code-review"
    skill_dir.mkdir(parents=True)
    (skill_dir / SKILL_FILENAME).write_text("Review for security regressions.", encoding="utf-8")

    skills = manager.list_installed()

    assert len(skills) == 1
    assert skills[0].name == "code-review"
    assert "security" in skills[0].content


def test_discover_workspace_skills_scans_standard_locations(tmp_path: Path):
    manager = SkillsManager(tmp_path / "installed")
    workspace = tmp_path / "workspace"
    (workspace / "skills" / "lint").mkdir(parents=True)
    (workspace / "skills" / "lint" / SKILL_FILENAME).write_text(
        "Always run lint first.", encoding="utf-8"
    )
    (workspace / ".github" / "skills" / "tests").mkdir(parents=True)
    (workspace / ".github" / "skills" / "tests" / SKILL_FILENAME).write_text(
        "Run tests after edits.", encoding="utf-8"
    )

    docs = manager.discover_workspace_skills(workspace)
    names = {doc.name for doc in docs}

    assert names == {"lint", "tests"}
