"""Session persistence backends (JSON and store abstraction)."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path

from filelock import FileLock

from localagentcli.session.state import Session

SESSION_FORMAT_VERSION = 1


class SessionStore(ABC):
    """Persistence contract for session save/load/list operations."""

    @abstractmethod
    def save_session(self, session: Session, name: str) -> Path:
        """Persist a session and return a user-facing persistence path."""

    @abstractmethod
    def load_session(self, name: str) -> Session:
        """Load one session by name or raise FileNotFoundError."""

    @abstractmethod
    def list_sessions(self) -> list[dict]:
        """Return display-oriented summaries for known sessions."""


class JsonSessionStore(SessionStore):
    """Legacy session store backed by one JSON file per session."""

    def __init__(self, sessions_dir: Path):
        self._dir = sessions_dir

    @property
    def sessions_dir(self) -> Path:
        """Expose the backing directory for compatibility flows."""
        return self._dir

    def path_for_name(self, name: str) -> Path:
        """Return the JSON path for a named session."""
        return self._dir / f"{name}.json"

    def save_session(self, session: Session, name: str) -> Path:
        session.name = name
        path = self.path_for_name(name)
        payload = dict(session.to_dict())
        payload["format_version"] = SESSION_FORMAT_VERSION

        lock = FileLock(str(path) + ".lock")
        with lock:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)

        return path

    def load_session(self, name: str) -> Session:
        path = self.path_for_name(name)
        if not path.exists():
            raise FileNotFoundError(f"Session '{name}' not found")

        lock = FileLock(str(path) + ".lock")
        with lock:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)

        if isinstance(data, dict):
            data.pop("format_version", None)

        return Session.from_dict(data)

    def list_sessions(self) -> list[dict]:
        sessions: list[dict] = []
        if not self._dir.exists():
            return sessions

        for path in sorted(self._dir.glob("*.json")):
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
                sessions.append(
                    {
                        "name": data.get("name", path.stem),
                        "created_at": data.get("created_at", ""),
                        "model": data.get("model", ""),
                        "mode": data.get("mode", ""),
                        "message_count": len(data.get("history", [])),
                    }
                )
            except (json.JSONDecodeError, OSError):
                continue

        return sessions
