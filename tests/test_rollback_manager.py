"""Tests for rollback history and undo support."""

from __future__ import annotations

import json
from pathlib import Path

from localagentcli.safety.rollback import RollbackManager


class TestRollbackManager:
    def test_undo_last_restores_modified_file(self, tmp_path: Path):
        cache_dir = tmp_path / "cache"
        file_path = tmp_path / "src" / "app.py"
        file_path.parent.mkdir(parents=True)
        file_path.write_text("before", encoding="utf-8")
        manager = RollbackManager("session-1", cache_dir)

        backup = manager.backup_file(file_path)
        file_path.write_text("after", encoding="utf-8")
        manager.record_modification(file_path, backup, "patch_apply", "Updated app.py")

        entry = manager.undo_last()

        assert entry.action == "modified"
        assert file_path.read_text(encoding="utf-8") == "before"
        assert manager.get_history() == []

    def test_undo_all_deletes_created_files(self, tmp_path: Path):
        cache_dir = tmp_path / "cache"
        file_path = tmp_path / "new.txt"
        file_path.write_text("hello", encoding="utf-8")
        manager = RollbackManager("session-1", cache_dir)
        manager.record_creation(file_path, "file_write", "Created new.txt")

        undone = manager.undo_all()

        assert [entry.action for entry in undone] == ["created"]
        assert not file_path.exists()

    def test_writes_json_log(self, tmp_path: Path):
        cache_dir = tmp_path / "cache"
        file_path = tmp_path / "notes.txt"
        file_path.write_text("hello", encoding="utf-8")
        manager = RollbackManager("session-1", cache_dir)
        backup = manager.backup_file(file_path)
        manager.record_modification(file_path, backup, "file_write", "Updated notes")

        log_path = cache_dir / "rollback" / "session-1" / "rollback_log.json"
        data = json.loads(log_path.read_text(encoding="utf-8"))

        assert data["session_id"] == "session-1"
        assert data["entries"][0]["file_path"] == str(file_path)
