"""Skills manager for local SKILL.md discovery and installation."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

SKILL_FILENAME = "SKILL.md"


@dataclass(frozen=True)
class SkillDocument:
    """One resolved skill document."""

    name: str
    path: Path
    content: str
    source: str
    mtime_ns: int


class SkillsManager:
    """Manage installed skills and workspace skill discovery."""

    def __init__(self, skills_dir: Path):
        self._skills_dir = skills_dir
        self._skills_dir.mkdir(parents=True, exist_ok=True)

    @property
    def skills_dir(self) -> Path:
        return self._skills_dir

    def list_installed(self) -> list[SkillDocument]:
        docs: list[SkillDocument] = []
        for candidate in sorted(self._skills_dir.iterdir(), key=lambda item: item.name.lower()):
            if candidate.name.startswith("."):
                continue
            if candidate.is_dir():
                skill_file = candidate / SKILL_FILENAME
                if not skill_file.is_file():
                    continue
                docs.append(self._read_skill_file(skill_file, source="installed"))
                continue
            if candidate.is_file() and candidate.name.lower().endswith(".md"):
                docs.append(self._read_skill_file(candidate, source="installed"))
        return docs

    def discover_workspace_skills(self, workspace: Path) -> list[SkillDocument]:
        root = workspace.expanduser().resolve()
        if not root.exists():
            return []

        docs: dict[str, SkillDocument] = {}
        search_roots = [
            root / "skills",
            root / ".skills",
            root / ".github" / "skills",
        ]
        for base in search_roots:
            if not base.is_dir():
                continue
            for file_path in sorted(base.rglob(SKILL_FILENAME)):
                doc = self._read_skill_file(file_path, source="workspace")
                docs[str(doc.path)] = doc
        return list(docs.values())

    def install_from_path(self, source: Path, *, name: str | None = None) -> SkillDocument:
        src = source.expanduser().resolve()
        if not src.exists():
            raise FileNotFoundError(f"Skill source does not exist: {src}")

        skill_name = (name or src.stem or src.name).strip()
        if not skill_name:
            raise ValueError("Skill name could not be inferred; pass an explicit name.")
        if any(char in skill_name for char in "/\\"):
            raise ValueError("Skill name cannot contain path separators.")

        destination_dir = self._skills_dir / skill_name
        if destination_dir.exists():
            raise FileExistsError(f"Skill '{skill_name}' is already installed.")

        if src.is_dir():
            skill_src = src / SKILL_FILENAME
            if not skill_src.is_file():
                raise FileNotFoundError(f"Skill directory must contain {SKILL_FILENAME}: {src}")
            shutil.copytree(src, destination_dir)
            return self._read_skill_file(destination_dir / SKILL_FILENAME, source="installed")

        if src.name != SKILL_FILENAME:
            raise ValueError(f"Skill file must be named {SKILL_FILENAME}: {src}")

        destination_dir.mkdir(parents=True, exist_ok=False)
        shutil.copy2(src, destination_dir / SKILL_FILENAME)
        return self._read_skill_file(destination_dir / SKILL_FILENAME, source="installed")

    def remove(self, name: str) -> SkillDocument:
        skill_name = name.strip()
        if not skill_name:
            raise ValueError("Skill name is required.")

        skill_dir = self._skills_dir / skill_name
        skill_file = skill_dir / SKILL_FILENAME
        if not skill_file.is_file():
            raise FileNotFoundError(f"Skill '{skill_name}' is not installed.")

        doc = self._read_skill_file(skill_file, source="installed")
        shutil.rmtree(skill_dir)
        return doc

    def _read_skill_file(self, skill_file: Path, *, source: str) -> SkillDocument:
        content = skill_file.read_text(encoding="utf-8").strip()
        if not content:
            raise ValueError(f"Skill file is empty: {skill_file}")
        name = skill_file.parent.name if skill_file.name == SKILL_FILENAME else skill_file.stem
        stat = skill_file.stat()
        return SkillDocument(
            name=name,
            path=skill_file,
            content=content,
            source=source,
            mtime_ns=stat.st_mtime_ns,
        )
