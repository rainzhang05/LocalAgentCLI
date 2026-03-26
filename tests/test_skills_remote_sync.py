"""Tests for remote skills manifest sync behavior."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from localagentcli.skills import SkillsManager


def test_skills_manager_sync_from_manifest_url_downloads_and_installs(tmp_path: Path):
    manager = SkillsManager(tmp_path / "skills_store")

    class _FakeResponse:
        def __init__(self, payload: bytes):
            self._payload = payload

        def read(self) -> bytes:
            return self._payload

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def _fake_urlopen(url: str, timeout: float):
        if url.endswith("manifest.json"):
            return _FakeResponse(
                b'{"skills":[{"name":"remote_skill","url":"https://example/SKILL.md"}]}'
            )
        return _FakeResponse(b"Always run tests after edits.\n")

    with patch("localagentcli.skills.manager.urllib.request.urlopen", side_effect=_fake_urlopen):
        synced = manager.sync_from_manifest_url("https://example/manifest.json")

    assert len(synced) == 1
    assert synced[0].name == "remote_skill"
    installed = manager.skills_dir / "remote_skill" / "SKILL.md"
    assert installed.exists()
    assert "Always run tests" in installed.read_text(encoding="utf-8")
