"""Tests for pluggable session persistence stores."""

from __future__ import annotations

import sqlite3
from datetime import datetime

from localagentcli.session.manager import SessionManager
from localagentcli.session.state import Message


def test_session_manager_uses_json_store_by_default(storage, config):
    manager = SessionManager(storage.sessions_dir, config)
    manager.new_session()

    path = manager.save_session("json-default")

    assert path == storage.sessions_dir / "json-default.json"
    assert path.exists()


def test_session_manager_uses_sqlite_store_when_feature_enabled(storage, config):
    config.set("features.sqlite_session_store", True)
    manager = SessionManager(storage.sessions_dir, config)
    manager.new_session()

    path = manager.save_session("sqlite-enabled")

    assert path == storage.root / "sessions.db"
    assert path.exists()
    assert not (storage.sessions_dir / "sqlite-enabled.json").exists()


def test_sqlite_store_auto_migrates_legacy_json_on_load(storage, config):
    legacy_manager = SessionManager(storage.sessions_dir, config)
    legacy_manager.new_session()
    legacy_manager.current.history.append(
        Message(role="user", content="legacy", timestamp=datetime.now())
    )
    legacy_manager.save_session("legacy")

    config.set("features.sqlite_session_store", True)
    migrated_manager = SessionManager(storage.sessions_dir, config)
    migrated_manager.new_session()

    loaded = migrated_manager.load_session("legacy")

    assert loaded.name == "legacy"
    assert len(loaded.history) == 1
    assert loaded.history[0].content == "legacy"

    with sqlite3.connect(storage.root / "sessions.db") as conn:
        row = conn.execute("SELECT name FROM sessions WHERE name = ?", ("legacy",)).fetchone()
    assert row is not None


def test_sqlite_store_lists_unmigrated_json_sessions(storage, config):
    legacy_manager = SessionManager(storage.sessions_dir, config)
    legacy_manager.new_session()
    legacy_manager.save_session("legacy-list")

    config.set("features.sqlite_session_store", True)
    sqlite_manager = SessionManager(storage.sessions_dir, config)
    sqlite_manager.new_session()

    names = {entry["name"] for entry in sqlite_manager.list_sessions()}

    assert "legacy-list" in names
