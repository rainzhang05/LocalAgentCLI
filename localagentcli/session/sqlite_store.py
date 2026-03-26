"""SQLite-backed session persistence."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from localagentcli.session.state import Session
from localagentcli.session.store import SESSION_FORMAT_VERSION, JsonSessionStore, SessionStore

_DB_SCHEMA_VERSION = 1


class SqliteSessionStore(SessionStore):
    """Persist sessions in SQLite with optional JSON fallback migration."""

    def __init__(self, db_path: Path, legacy_json_store: JsonSessionStore | None = None):
        self._db_path = db_path
        self._legacy_json_store = legacy_json_store
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_db()

    @property
    def db_path(self) -> Path:
        """Return the backing SQLite database path."""
        return self._db_path

    def save_session(self, session: Session, name: str) -> Path:
        session.name = name
        payload = dict(session.to_dict())
        payload["format_version"] = SESSION_FORMAT_VERSION

        serialized = json.dumps(payload, ensure_ascii=False)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions (
                    name,
                    payload_json,
                    created_at,
                    updated_at,
                    mode,
                    model,
                    message_count,
                    format_version
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at,
                    mode = excluded.mode,
                    model = excluded.model,
                    message_count = excluded.message_count,
                    format_version = excluded.format_version
                """,
                (
                    name,
                    serialized,
                    session.created_at.isoformat(),
                    session.updated_at.isoformat(),
                    session.mode,
                    session.model,
                    len(session.history),
                    SESSION_FORMAT_VERSION,
                ),
            )
        return self._db_path

    def load_session(self, name: str) -> Session:
        row = None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM sessions WHERE name = ?",
                (name,),
            ).fetchone()

        if row is None:
            if self._legacy_json_store is None:
                raise FileNotFoundError(f"Session '{name}' not found")
            session = self._load_and_migrate_legacy_json(name)
            if session is None:
                raise FileNotFoundError(f"Session '{name}' not found")
            return session

        data = json.loads(str(row["payload_json"]))
        if isinstance(data, dict):
            data.pop("format_version", None)
        return Session.from_dict(data)

    def list_sessions(self) -> list[dict]:
        rows: list[sqlite3.Row] = []
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT name, created_at, model, mode, message_count
                FROM sessions
                ORDER BY datetime(created_at) DESC, name DESC
                """
            ).fetchall()

        sessions: list[dict] = [
            {
                "name": str(row["name"]),
                "created_at": str(row["created_at"]),
                "model": str(row["model"]),
                "mode": str(row["mode"]),
                "message_count": int(row["message_count"]),
            }
            for row in rows
        ]

        # Keep not-yet-migrated JSON sessions discoverable while SQLite is enabled.
        if self._legacy_json_store is not None:
            known = {entry["name"] for entry in sessions}
            for entry in self._legacy_json_store.list_sessions():
                name = entry.get("name")
                if isinstance(name, str) and name not in known:
                    sessions.append(entry)

        sessions.sort(
            key=lambda entry: (str(entry.get("created_at", "")), str(entry.get("name", ""))),
            reverse=True,
        )
        return sessions

    def _load_and_migrate_legacy_json(self, name: str) -> Session | None:
        if self._legacy_json_store is None:
            return None
        try:
            session = self._legacy_json_store.load_session(name)
        except FileNotFoundError:
            return None
        # Best-effort auto-migration to SQLite after successful legacy read.
        try:
            self.save_session(session, session.name or name)
        except Exception:
            pass
        return session

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _initialize_db(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key TEXT PRIMARY KEY,
                    value INTEGER NOT NULL
                )
                """
            )

            current = conn.execute(
                "SELECT value FROM schema_meta WHERE key = 'schema_version'"
            ).fetchone()
            if current is None:
                conn.execute("INSERT INTO schema_meta(key, value) VALUES('schema_version', 0)")
                schema_version = 0
            else:
                schema_version = int(current["value"])

            if schema_version < 1:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS sessions (
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
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_sessions_updated_at
                    ON sessions(updated_at DESC, name DESC)
                    """
                )
                conn.execute(
                    "UPDATE schema_meta SET value = ? WHERE key = 'schema_version'",
                    (_DB_SCHEMA_VERSION,),
                )
