"""Tests for localagentcli.storage.manager."""

from __future__ import annotations

import os
import time
from pathlib import Path

from localagentcli.storage.manager import StorageManager, _rmtree


class TestStorageManagerInit:
    """Tests for StorageManager construction."""

    def test_default_root(self):
        sm = StorageManager()
        assert sm.root == Path.home() / ".localagent"

    def test_custom_root(self, tmp_path: Path):
        root = tmp_path / "custom"
        sm = StorageManager(root=root)
        assert sm.root == root


class TestStorageManagerInitialize:
    """Tests for directory creation."""

    def test_creates_all_directories(self, storage: StorageManager):
        assert storage.root.is_dir()
        assert storage.models_dir.is_dir()
        assert storage.sessions_dir.is_dir()
        assert storage.logs_dir.is_dir()
        assert (storage.logs_dir / "exports").is_dir()
        assert storage.cache_dir.is_dir()
        assert (storage.cache_dir / "rollback").is_dir()
        assert (storage.cache_dir / "downloads").is_dir()
        assert storage.secrets_dir.is_dir()

    def test_secrets_permissions(self, storage: StorageManager):
        mode = storage.secrets_dir.stat().st_mode & 0o777
        if os.name == "nt":
            assert mode & 0o700 == 0o700
        else:
            assert mode == 0o700

    def test_idempotent(self, storage: StorageManager):
        # Calling initialize twice should not raise
        storage.initialize()
        assert storage.root.is_dir()


class TestStorageManagerProperties:
    """Tests for path properties."""

    def test_config_path(self, storage: StorageManager):
        assert storage.config_path == storage.root / "config.toml"

    def test_registry_path(self, storage: StorageManager):
        assert storage.registry_path == storage.root / "registry.json"

    def test_models_dir(self, storage: StorageManager):
        assert storage.models_dir == storage.root / "models"

    def test_sessions_dir(self, storage: StorageManager):
        assert storage.sessions_dir == storage.root / "sessions"

    def test_logs_dir(self, storage: StorageManager):
        assert storage.logs_dir == storage.root / "logs"

    def test_cache_dir(self, storage: StorageManager):
        assert storage.cache_dir == storage.root / "cache"

    def test_secrets_dir(self, storage: StorageManager):
        assert storage.secrets_dir == storage.root / "secrets"


class TestCleanupCache:
    """Tests for cache cleanup."""

    def test_removes_old_files(self, storage: StorageManager):
        old_file = storage.cache_dir / "downloads" / "old.bin"
        old_file.write_text("data")
        # Set mtime to 48 hours ago
        old_time = time.time() - 48 * 3600
        os.utime(old_file, (old_time, old_time))

        storage.cleanup_cache(max_age_hours=24)
        assert not old_file.exists()

    def test_keeps_recent_files(self, storage: StorageManager):
        recent_file = storage.cache_dir / "downloads" / "recent.bin"
        recent_file.write_text("data")

        storage.cleanup_cache(max_age_hours=24)
        assert recent_file.exists()

    def test_removes_old_directories(self, storage: StorageManager):
        old_dir = storage.cache_dir / "rollback" / "old_session"
        old_dir.mkdir()
        (old_dir / "backup.txt").write_text("data")
        old_time = time.time() - 48 * 3600
        os.utime(old_dir, (old_time, old_time))

        storage.cleanup_cache(max_age_hours=24)
        assert not old_dir.exists()


class TestCleanupLogs:
    """Tests for log cleanup."""

    def test_removes_old_logs(self, storage: StorageManager):
        old_log = storage.logs_dir / "localagent_20200101.log"
        old_log.write_text("old log")
        old_time = time.time() - 60 * 86400
        os.utime(old_log, (old_time, old_time))

        storage.cleanup_logs(max_age_days=30)
        assert not old_log.exists()

    def test_keeps_recent_logs(self, storage: StorageManager):
        recent_log = storage.logs_dir / "localagent_20260317.log"
        recent_log.write_text("recent log")

        storage.cleanup_logs(max_age_days=30)
        assert recent_log.exists()

    def test_ignores_non_log_files(self, storage: StorageManager):
        txt_file = storage.logs_dir / "notes.txt"
        txt_file.write_text("not a log")
        old_time = time.time() - 60 * 86400
        os.utime(txt_file, (old_time, old_time))

        storage.cleanup_logs(max_age_days=30)
        assert txt_file.exists()

    def test_handles_missing_logs_dir(self, tmp_path: Path):
        sm = StorageManager(root=tmp_path / "nonexistent")
        sm.cleanup_logs()  # Should not raise


class TestDiskUsage:
    """Tests for disk usage reporting."""

    def test_returns_all_categories(self, storage: StorageManager):
        usage = storage.disk_usage()
        assert set(usage.keys()) == {"models", "sessions", "logs", "cache"}

    def test_counts_file_sizes(self, storage: StorageManager):
        (storage.models_dir / "test.bin").write_bytes(b"x" * 100)
        usage = storage.disk_usage()
        assert usage["models"] == 100

    def test_empty_directories(self, storage: StorageManager):
        usage = storage.disk_usage()
        assert usage["models"] == 0


class TestRmtree:
    """Tests for the _rmtree helper."""

    def test_removes_nested_directory(self, tmp_path: Path):
        d = tmp_path / "a" / "b" / "c"
        d.mkdir(parents=True)
        (d / "file.txt").write_text("data")
        (tmp_path / "a" / "b" / "file2.txt").write_text("data2")

        _rmtree(tmp_path / "a")
        assert not (tmp_path / "a").exists()
