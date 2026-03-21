"""Append-only runtime event log persistence."""

from __future__ import annotations

import json
from pathlib import Path

from filelock import FileLock

from localagentcli.runtime.protocol import RuntimeEvent, Submission


class SessionEventLog:
    """Persist runtime submissions and events as JSONL for one session."""

    def __init__(self, log_root: Path, session_id: str):
        self._path = log_root / f"{session_id}.jsonl"
        self._lock = FileLock(str(self._path) + ".lock")

    @property
    def path(self) -> Path:
        """Return the log file path."""
        return self._path

    def append_submission(self, submission: Submission) -> None:
        """Append one submission record."""
        self._append({"kind": "submission", "payload": submission.to_dict()})

    def append_event(self, event: RuntimeEvent) -> None:
        """Append one emitted runtime event."""
        self._append({"kind": "event", "payload": event.to_dict()})

    def read_records(self) -> list[dict[str, object]]:
        """Read all persisted records for the session."""
        with self._lock:
            if not self._path.exists():
                return []
            lines = self._path.read_text(encoding="utf-8").splitlines()
        records: list[dict[str, object]] = []
        for line in lines:
            if not line.strip():
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                records.append(parsed)
        return records

    def _append(self, record: dict[str, object]) -> None:
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
