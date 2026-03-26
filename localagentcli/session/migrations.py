"""SQLite migration helpers for session persistence."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path


class SqliteMigrationRunner:
    """Apply ordered SQL migrations and track applied versions."""

    def __init__(self, migrations_dir: Path):
        self._dir = migrations_dir

    def apply_all(self, conn: sqlite3.Connection) -> None:
        """Apply all pending migrations in filename order."""
        self._ensure_migration_table(conn)
        self._backfill_legacy_version_markers(conn)

        applied = self._applied_migrations(conn)
        for name, path in self._migration_files():
            if name in applied:
                continue
            sql_text = path.read_text(encoding="utf-8")
            conn.executescript(sql_text)
            conn.execute(
                """
                INSERT INTO schema_migrations(name, applied_at)
                VALUES (?, ?)
                """,
                (name, datetime.now().isoformat()),
            )

    def _ensure_migration_table(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                name TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
            """
        )

    def _applied_migrations(self, conn: sqlite3.Connection) -> set[str]:
        rows = conn.execute("SELECT name FROM schema_migrations").fetchall()
        return {str(row[0]) for row in rows}

    def _migration_files(self) -> list[tuple[str, Path]]:
        if not self._dir.exists():
            return []
        files = sorted(path for path in self._dir.glob("*.sql") if path.is_file())
        return [(path.stem, path) for path in files]

    def _backfill_legacy_version_markers(self, conn: sqlite3.Connection) -> None:
        """Mark migrations already represented by legacy schema metadata.

        Legacy SQLite bootstrap used `schema_meta(schema_version=1)` plus a
        pre-created `sessions` table. Preserve that work by marking migration
        `0001_create_sessions` as already applied.
        """
        if not _table_exists(conn, "schema_meta"):
            return

        row = conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
        if row is None:
            return

        try:
            schema_version = int(row[0])
        except (TypeError, ValueError):
            return

        if schema_version < 1:
            return
        if not _table_exists(conn, "sessions"):
            return

        conn.execute(
            """
            INSERT OR IGNORE INTO schema_migrations(name, applied_at)
            VALUES (?, ?)
            """,
            ("0001_create_sessions", datetime.now().isoformat()),
        )


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = ?
        """,
        (table_name,),
    ).fetchone()
    return row is not None
