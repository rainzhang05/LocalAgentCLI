"""Rollback state and undo support for file modifications."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast


@dataclass
class RollbackEntry:
    """A single reversible file change."""

    index: int
    timestamp: str
    tool: str
    file_path: str
    backup_path: str | None
    action: str
    summary: str

    def to_dict(self) -> dict[str, object]:
        """Serialize the entry to a JSON-compatible dict."""
        return {
            "index": self.index,
            "timestamp": self.timestamp,
            "tool": self.tool,
            "file_path": self.file_path,
            "backup_path": self.backup_path,
            "action": self.action,
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> RollbackEntry:
        """Deserialize an entry from a stored rollback log."""
        index = cast(int | str, data["index"])
        return cls(
            index=int(index),
            timestamp=str(data["timestamp"]),
            tool=str(data["tool"]),
            file_path=str(data["file_path"]),
            backup_path=str(data["backup_path"]) if data.get("backup_path") else None,
            action=str(data["action"]),
            summary=str(data["summary"]),
        )


class RollbackManager:
    """Track and undo file modifications for one session."""

    def __init__(self, session_id: str, storage_path: Path):
        self._session_id = session_id
        self._storage = storage_path / "rollback" / session_id
        self._storage.mkdir(parents=True, exist_ok=True)
        self._log_path = self._storage / "rollback_log.json"
        self._entries = self._load_log()

    def backup_file(self, file_path: Path) -> Path:
        """Create a backup copy of an existing file before modification."""
        if not file_path.exists():
            raise FileNotFoundError(f"Cannot back up missing file '{file_path}'")

        backup_name = f"{len(self._entries) + 1:03d}_{self._safe_name(file_path)}"
        backup_path = self._storage / backup_name
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file_path, backup_path)
        return backup_path

    def record_creation(self, file_path: Path, tool: str, summary: str) -> RollbackEntry:
        """Record creation of a new file so undo can delete it."""
        entry = self._append_entry(
            tool=tool,
            file_path=file_path,
            backup_path=None,
            action="created",
            summary=summary,
        )
        return entry

    def record_modification(
        self,
        file_path: Path,
        backup_path: Path,
        tool: str,
        summary: str,
    ) -> RollbackEntry:
        """Record modification of an existing file so undo can restore the backup."""
        entry = self._append_entry(
            tool=tool,
            file_path=file_path,
            backup_path=backup_path,
            action="modified",
            summary=summary,
        )
        return entry

    def undo_last(self) -> RollbackEntry:
        """Undo the most recent recorded file change."""
        if not self._entries:
            raise ValueError("No rollback history is available for this session.")

        entry = self._entries.pop()
        target = Path(entry.file_path)
        if entry.action == "created":
            if target.exists():
                target.unlink()
        elif entry.action == "modified":
            if entry.backup_path is None:
                raise ValueError("Rollback entry is missing a backup path.")
            backup = Path(entry.backup_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(backup, target)
        else:
            raise ValueError(f"Unsupported rollback action '{entry.action}'")

        self._write_log()
        return entry

    def undo_all(self) -> list[RollbackEntry]:
        """Undo every recorded change in reverse order."""
        undone: list[RollbackEntry] = []
        while self._entries:
            undone.append(self.undo_last())
        return undone

    def get_history(self) -> list[RollbackEntry]:
        """Return the current rollback history."""
        return list(self._entries)

    def _append_entry(
        self,
        *,
        tool: str,
        file_path: Path,
        backup_path: Path | None,
        action: str,
        summary: str,
    ) -> RollbackEntry:
        entry = RollbackEntry(
            index=len(self._entries) + 1,
            timestamp=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            tool=tool,
            file_path=str(file_path),
            backup_path=str(backup_path) if backup_path is not None else None,
            action=action,
            summary=summary,
        )
        self._entries.append(entry)
        self._write_log()
        return entry

    def _load_log(self) -> list[RollbackEntry]:
        if not self._log_path.exists():
            return []
        data = json.loads(self._log_path.read_text(encoding="utf-8"))
        entries = data.get("entries", [])
        return [RollbackEntry.from_dict(entry) for entry in entries if isinstance(entry, dict)]

    def _write_log(self) -> None:
        payload = {
            "session_id": self._session_id,
            "entries": [entry.to_dict() for entry in self._entries],
        }
        self._log_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _safe_name(self, file_path: Path) -> str:
        return str(file_path).replace("\\", "_").replace("/", "_")
