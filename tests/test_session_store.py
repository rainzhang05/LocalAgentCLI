"""Tests for pluggable session persistence stores."""

from __future__ import annotations

import sqlite3
from datetime import datetime

from localagentcli.session.manager import SessionManager
from localagentcli.session.sqlite_store import SqliteSessionStore
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


def test_sqlite_store_records_applied_migrations(storage, config):
    config.set("features.sqlite_session_store", True)
    manager = SessionManager(storage.sessions_dir, config)
    manager.new_session()
    manager.save_session("migration-check")

    with sqlite3.connect(storage.root / "sessions.db") as conn:
        rows = conn.execute("SELECT name FROM schema_migrations ORDER BY name").fetchall()
        names = [str(row[0]) for row in rows]

    assert names == [
        "0001_create_sessions",
        "0002_add_replay_columns",
        "0003_create_session_memories",
    ]


def test_sqlite_store_upgrades_legacy_schema_meta_database(storage):
    db_path = storage.root / "sessions.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE schema_meta (
                key TEXT PRIMARY KEY,
                value INTEGER NOT NULL
            )
            """
        )
        conn.execute("INSERT INTO schema_meta(key, value) VALUES('schema_version', 1)")
        conn.execute(
            """
            CREATE TABLE sessions (
                name TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                mode TEXT NOT NULL,
                model TEXT NOT NULL,
                message_count INTEGER NOT NULL,
                format_version INTEGER NOT NULL DEFAULT 1
            )
            """
        )

    store = SqliteSessionStore(db_path)
    assert store.db_path == db_path

    with sqlite3.connect(db_path) as conn:
        migrations = conn.execute("SELECT name FROM schema_migrations ORDER BY name").fetchall()
        migration_names = [str(row[0]) for row in migrations]
        columns = conn.execute("PRAGMA table_info(sessions)").fetchall()
        column_names = {str(row[1]) for row in columns}

    assert migration_names == [
        "0001_create_sessions",
        "0002_add_replay_columns",
        "0003_create_session_memories",
    ]
    assert "replay_last_record_count" in column_names
    assert "replay_last_replayed_at" in column_names


def test_sqlite_store_persists_workspace_memory_rows(storage, config):
    config.set("features.sqlite_session_store", True)
    manager = SessionManager(storage.sessions_dir, config)
    manager.new_session()
    manager.current.history.append(
        Message(
            role="system",
            content="Summary: preferred style is concise and typed.",
            timestamp=datetime.now(),
            is_summary=True,
        )
    )

    manager.save_session("memory-source")

    with sqlite3.connect(storage.root / "sessions.db") as conn:
        row = conn.execute(
            "SELECT content FROM session_memories WHERE session_name = ?",
            ("memory-source",),
        ).fetchone()

    assert row is not None
    assert "preferred style" in str(row[0])


def test_sqlite_store_prunes_old_unnamed_autosaves(storage, config):
    config.set("features.sqlite_session_store", True)
    config.set("sessions.autosave_unnamed", True)
    config.set("sessions.autosave_unnamed_retention_days", 1)
    manager = SessionManager(storage.sessions_dir, config)
    manager.new_session()
    manager.current.history.append(
        Message(role="user", content="old autosave", timestamp=datetime.now())
    )
    manager.flush_named_autosave()

    generated = str(manager.current.metadata["autosave_generated_name"])
    with sqlite3.connect(storage.root / "sessions.db") as conn:
        conn.execute(
            "UPDATE sessions SET created_at = ? WHERE name = ?",
            ("2000-01-01T00:00:00", generated),
        )
        conn.commit()

    removed = manager.prune_unnamed_autosaves()
    assert removed >= 1

    with sqlite3.connect(storage.root / "sessions.db") as conn:
        row = conn.execute("SELECT name FROM sessions WHERE name = ?", (generated,)).fetchone()
    assert row is None
