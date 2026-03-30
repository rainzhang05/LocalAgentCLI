"""SQLite-backed session persistence."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

from localagentcli.session.memory import (
    LONG_HORIZON_MEMORY_KEY,
    extract_session_memory_entries,
    merge_long_horizon_memory,
)
from localagentcli.session.migrations import SqliteMigrationRunner
from localagentcli.session.state import Session
from localagentcli.session.store import (
    SESSION_FORMAT_VERSION,
    JsonSessionStore,
    SessionStore,
)


class SqliteSessionStore(SessionStore):
    """Persist sessions in SQLite with optional JSON fallback migration."""

    def __init__(
        self,
        db_path: Path,
        legacy_json_store: JsonSessionStore | None = None,
        migrations_dir: Path | None = None,
    ):
        self._db_path = db_path
        self._legacy_json_store = legacy_json_store
        self._migrations_dir = migrations_dir or Path(__file__).with_name("migrations")
        self._migration_runner = SqliteMigrationRunner(self._migrations_dir)
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
        replay_meta = session.metadata.get("runtime_replay", {})
        replay_count = _to_replay_count(replay_meta)
        replay_time = _to_replay_timestamp(replay_meta)
        memory_entries = extract_session_memory_entries(session)
        active_agents = _extract_active_agent_entries(session.metadata.get("active_agents", {}))
        if memory_entries:
            session.metadata[LONG_HORIZON_MEMORY_KEY] = memory_entries

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
                    format_version,
                    replay_last_record_count,
                    replay_last_replayed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at,
                    mode = excluded.mode,
                    model = excluded.model,
                    message_count = excluded.message_count,
                    format_version = excluded.format_version,
                    replay_last_record_count = excluded.replay_last_record_count,
                    replay_last_replayed_at = excluded.replay_last_replayed_at
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
                    replay_count,
                    replay_time,
                ),
            )
            self._persist_workspace_memory(
                conn,
                session_name=name,
                workspace=str(session.workspace),
                entries=memory_entries,
            )
            self._persist_active_agents(
                conn,
                session_name=name,
                entries=active_agents,
            )
        return self._db_path

    def load_session(self, name: str) -> Session:
        row: sqlite3.Row | None = None
        workspace_memories: list[dict[str, str]] = []
        active_agents: dict[str, dict[str, object]] = {}
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    payload_json,
                    replay_last_record_count,
                    replay_last_replayed_at
                FROM sessions
                WHERE name = ?
                """,
                (name,),
            ).fetchone()

            if row is None:
                if self._legacy_json_store is None:
                    raise FileNotFoundError(f"Session '{name}' not found")
                session = self._load_and_migrate_legacy_json(name)
                if session is None:
                    raise FileNotFoundError(f"Session '{name}' not found")
                return session

            payload_value = json.loads(str(row["payload_json"]))
            if isinstance(payload_value, dict):
                data: dict[str, object] = payload_value
            else:
                raise ValueError(f"Session '{name}' payload is malformed")

            data.pop("format_version", None)
            metadata = data.get("metadata")
            if isinstance(metadata, dict):
                replay_meta = metadata.setdefault("runtime_replay", {})
                if isinstance(replay_meta, dict):
                    replay_meta.setdefault(
                        "last_record_count",
                        int(row["replay_last_record_count"]),
                    )
                    replay_meta.setdefault(
                        "last_replayed_at",
                        str(row["replay_last_replayed_at"]),
                    )

            workspace_value = data.get("workspace", ".")
            workspace_memories = self._load_workspace_memory(conn, str(workspace_value))
            active_agents = self._load_active_agents(conn, name)

        session = Session.from_dict(data)
        if active_agents:
            session.metadata["active_agents"] = active_agents
        else:
            legacy_snapshot = session.metadata.get("active_agents", {})
            normalized_legacy = _extract_active_agent_entries(legacy_snapshot)
            if normalized_legacy:
                session.metadata["active_agents"] = {
                    entry["path"]: {
                        "path": entry["path"],
                        "name": entry["name"],
                        "status": entry["status"],
                        "nickname": entry["nickname"],
                        "role": entry["role"],
                        "last_error": entry["last_error"],
                        "task_count": entry["task_count"],
                        "updated_at": entry["updated_at"],
                    }
                    for entry in normalized_legacy
                }
            else:
                session.metadata.pop("active_agents", None)
        if workspace_memories:
            merge_long_horizon_memory(session, workspace_memories)
        return session

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

    def prune_unnamed_autosaves(self, prefix: str, cutoff: datetime) -> int:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT name
                FROM sessions
                WHERE name LIKE ?
                  AND datetime(created_at) < datetime(?)
                """,
                (f"{prefix}%", cutoff.isoformat()),
            ).fetchall()
            names = [str(row["name"]) for row in rows]
            if not names:
                return 0

            placeholders = ",".join("?" for _ in names)
            conn.execute(
                f"DELETE FROM session_memories WHERE session_name IN ({placeholders})",
                names,
            )
            conn.execute(
                f"DELETE FROM session_active_agents WHERE session_name IN ({placeholders})",
                names,
            )
            deleted = conn.execute(
                f"DELETE FROM sessions WHERE name IN ({placeholders})",
                names,
            ).rowcount
        return int(deleted or 0)

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
            self._migration_runner.apply_all(conn)

    def _persist_workspace_memory(
        self,
        conn: sqlite3.Connection,
        session_name: str,
        workspace: str,
        entries: list[dict[str, str]],
    ) -> None:
        if not entries:
            return

        conn.execute("DELETE FROM session_memories WHERE session_name = ?", (session_name,))
        for entry in entries:
            conn.execute(
                """
                INSERT OR REPLACE INTO session_memories(
                    session_name,
                    workspace,
                    memory_kind,
                    content,
                    source_timestamp,
                    updated_at,
                    usage_count
                ) VALUES (?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    session_name,
                    workspace,
                    str(entry.get("kind", "memory") or "memory"),
                    str(entry.get("content", "") or ""),
                    str(entry.get("source_timestamp", "") or ""),
                    str(entry.get("updated_at", "") or ""),
                ),
            )

    def _load_workspace_memory(
        self,
        conn: sqlite3.Connection,
        workspace: str,
    ) -> list[dict[str, str]]:
        rows = conn.execute(
            """
            SELECT id, memory_kind, content, source_timestamp, updated_at
            FROM session_memories
            WHERE workspace = ?
            ORDER BY datetime(updated_at) DESC, id DESC
            LIMIT 8
            """,
            (workspace,),
        ).fetchall()

        if not rows:
            return []

        ids = [int(row["id"]) for row in rows]
        placeholders = ",".join("?" for _ in ids)
        conn.execute(
            (
                "UPDATE session_memories "
                "SET usage_count = usage_count + 1 "
                f"WHERE id IN ({placeholders})"
            ),
            ids,
        )

        return [
            {
                "kind": str(row["memory_kind"]),
                "content": str(row["content"]),
                "source_timestamp": str(row["source_timestamp"]),
                "updated_at": str(row["updated_at"]),
            }
            for row in rows
        ]

    def _persist_active_agents(
        self,
        conn: sqlite3.Connection,
        session_name: str,
        entries: list[dict[str, object]],
    ) -> None:
        conn.execute("DELETE FROM session_active_agents WHERE session_name = ?", (session_name,))
        for entry in entries:
            conn.execute(
                """
                INSERT OR REPLACE INTO session_active_agents (
                    session_name,
                    agent_path,
                    agent_name,
                    status,
                    nickname,
                    role,
                    last_error,
                    task_count,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_name,
                    str(entry["path"]),
                    str(entry["name"]),
                    str(entry["status"]),
                    str(entry["nickname"]),
                    str(entry["role"]),
                    str(entry["last_error"]),
                    _as_non_negative_int(entry["task_count"]),
                    str(entry["updated_at"]),
                ),
            )

    def _load_active_agents(
        self,
        conn: sqlite3.Connection,
        session_name: str,
    ) -> dict[str, dict[str, object]]:
        rows = conn.execute(
            """
            SELECT
                agent_path,
                agent_name,
                status,
                nickname,
                role,
                last_error,
                task_count,
                updated_at
            FROM session_active_agents
            WHERE session_name = ?
            ORDER BY agent_path ASC
            """,
            (session_name,),
        ).fetchall()
        snapshot: dict[str, dict[str, object]] = {}
        for row in rows:
            path = str(row["agent_path"])
            snapshot[path] = {
                "path": path,
                "name": str(row["agent_name"]),
                "status": str(row["status"]),
                "nickname": str(row["nickname"]),
                "role": str(row["role"]),
                "last_error": str(row["last_error"]),
                "task_count": int(row["task_count"]),
                "updated_at": str(row["updated_at"]),
            }
        return snapshot


def _to_replay_count(value: object) -> int:
    if isinstance(value, dict):
        raw = value.get("last_record_count", 0)
    else:
        raw = 0
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        return 0
    return max(parsed, 0)


def _to_replay_timestamp(value: object) -> str:
    if isinstance(value, dict):
        raw = value.get("last_replayed_at", "")
    else:
        raw = ""
    if isinstance(raw, str):
        return raw
    return str(raw)


def _extract_active_agent_entries(value: object) -> list[dict[str, object]]:
    if not isinstance(value, Mapping):
        return []

    now_iso = datetime.now().isoformat()
    normalized: list[dict[str, object]] = []
    for path, raw in sorted(value.items(), key=lambda item: str(item[0])):
        if not isinstance(path, str):
            continue
        if not isinstance(raw, Mapping):
            continue

        status = str(raw.get("status", "shutdown") or "shutdown").strip().lower()
        if status not in {
            "pending_init",
            "running",
            "waiting_input",
            "completed",
            "failed",
            "timed_out",
            "shutdown",
            "not_found",
        }:
            status = "shutdown"

        updated_at = str(raw.get("updated_at", "") or "")
        if not _is_iso_datetime(updated_at):
            updated_at = now_iso

        normalized.append(
            {
                "path": path,
                "name": str(raw.get("name", path.rsplit("/", maxsplit=1)[-1] or "root") or ""),
                "status": status,
                "nickname": str(raw.get("nickname", "") or ""),
                "role": str(raw.get("role", "") or ""),
                "last_error": str(raw.get("last_error", "") or ""),
                "task_count": _as_non_negative_int(raw.get("task_count", 0)),
                "updated_at": updated_at,
            }
        )
    return normalized


def _as_non_negative_int(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return max(value, 0)
    if isinstance(value, str):
        try:
            parsed = int(value)
        except ValueError:
            return 0
        return max(parsed, 0)
    return 0


def _is_iso_datetime(value: str) -> bool:
    if not value:
        return False
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return False
    return True
