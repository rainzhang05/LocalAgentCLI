"""Best-effort session replay from append-only runtime event logs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from localagentcli.session.state import Message, Session

_REPLAY_META_KEY = "runtime_replay"


@dataclass(frozen=True)
class ReplayResult:
    """Outcome summary for one replay pass."""

    replayed_records: int = 0
    recovered_pairs: int = 0


def replay_session_from_event_log(session: Session, log_root: Path) -> ReplayResult:
    """Reconcile one session using its append-only runtime event log.

    This is intentionally conservative and only recovers missing user/assistant
    turn pairs from completed turns recorded in runtime events.
    """
    records = _read_runtime_records(log_root / f"{session.id}.jsonl")
    if not records:
        return ReplayResult()

    prompts_by_submission: dict[str, tuple[str, str]] = {}
    completions_by_submission: dict[str, tuple[str, str]] = {}
    ordered_submission_ids: list[str] = []

    for record in records:
        if not isinstance(record, dict):
            continue
        kind = record.get("kind")
        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue

        if kind == "submission":
            sid, prompt, timestamp = _parse_submission(payload)
            if not sid or not prompt:
                continue
            if sid not in prompts_by_submission:
                ordered_submission_ids.append(sid)
            prompts_by_submission[sid] = (prompt, timestamp)
            continue

        if kind == "event":
            sid, completion, timestamp = _parse_completion_event(payload)
            if sid and completion:
                completions_by_submission[sid] = (completion, timestamp)

    recovered_pairs = 0
    for sid in ordered_submission_ids:
        prompt_data = prompts_by_submission.get(sid)
        completion_data = completions_by_submission.get(sid)
        if prompt_data is None or completion_data is None:
            continue

        prompt, prompt_ts = prompt_data
        completion, completion_ts = completion_data

        if _has_user_assistant_pair(session.history, prompt, completion):
            continue

        prompt_time = _parse_iso_datetime(prompt_ts)
        completion_time = _parse_iso_datetime(completion_ts)

        if (
            session.history
            and session.history[-1].role == "user"
            and session.history[-1].content == prompt
        ):
            session.history.append(
                Message(
                    role="assistant",
                    content=completion,
                    timestamp=completion_time,
                    metadata={"recovered_from_runtime_log": True, "submission_id": sid},
                )
            )
            recovered_pairs += 1
            continue

        session.history.append(
            Message(
                role="user",
                content=prompt,
                timestamp=prompt_time,
                metadata={"recovered_from_runtime_log": True, "submission_id": sid},
            )
        )
        session.history.append(
            Message(
                role="assistant",
                content=completion,
                timestamp=completion_time,
                metadata={"recovered_from_runtime_log": True, "submission_id": sid},
            )
        )
        recovered_pairs += 1

    if recovered_pairs:
        session.touch()
        session.metadata["message_count"] = len(session.history)

    session.metadata[_REPLAY_META_KEY] = {
        "last_record_count": len(records),
        "last_replayed_at": datetime.now().isoformat(),
    }
    return ReplayResult(replayed_records=len(records), recovered_pairs=recovered_pairs)


def _parse_submission(payload: dict[str, Any]) -> tuple[str, str, str]:
    sid = str(payload.get("id", "") or "")
    timestamp = str(payload.get("timestamp", "") or "")
    op = payload.get("op")
    if not isinstance(op, dict):
        return "", "", ""
    if str(op.get("type", "") or "") != "user_turn":
        return "", "", ""
    prompt = str(op.get("prompt", "") or "").strip()
    if not prompt:
        return "", "", ""
    return sid, prompt, timestamp


def _parse_completion_event(payload: dict[str, Any]) -> tuple[str, str, str]:
    if str(payload.get("type", "") or "") != "turn_completed":
        return "", "", ""

    sid = str(payload.get("submission_id", "") or "")
    timestamp = str(payload.get("timestamp", "") or "")
    data = payload.get("data")
    completion = ""

    if isinstance(data, dict):
        completion = str(data.get("final_text", "") or data.get("summary", "") or "").strip()
    if not completion:
        completion = str(payload.get("message", "") or "").strip()

    if not sid or not completion:
        return "", "", ""
    return sid, completion, timestamp


def _parse_iso_datetime(value: str) -> datetime:
    if value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass
    return datetime.now()


def _has_user_assistant_pair(history: list[Message], user_text: str, assistant_text: str) -> bool:
    for index in range(len(history) - 1):
        user = history[index]
        assistant = history[index + 1]
        if (
            user.role == "user"
            and assistant.role == "assistant"
            and user.content == user_text
            and assistant.content == assistant_text
        ):
            return True
    return False


def _read_runtime_records(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

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
