"""Tests for runtime-event replay reconciliation into session history."""

from __future__ import annotations

import json
from datetime import datetime

from localagentcli.runtime.event_log import SessionEventLog
from localagentcli.runtime.protocol import RuntimeEvent, Submission, UserTurnOp
from localagentcli.session.manager import SessionManager
from localagentcli.session.state import Message


def _append_completed_turn(log: SessionEventLog, prompt: str, final_text: str) -> None:
    submission = Submission(op=UserTurnOp(prompt=prompt, mode="chat"))
    log.append_submission(submission)
    log.append_event(
        RuntimeEvent(
            type="turn_completed",
            submission_id=submission.id,
            data={"mode": "chat", "final_text": final_text},
            message=final_text,
        )
    )


def test_load_session_replays_missing_turn_pair(storage, config):
    manager = SessionManager(storage.sessions_dir, config)
    manager.new_session()
    manager.save_session("replay-me")
    session_id = manager.current.id

    log = SessionEventLog(storage.cache_dir / "runtime-events", session_id)
    _append_completed_turn(log, "hello", "world")

    loaded = manager.load_session("replay-me")

    assert len(loaded.history) == 2
    assert loaded.history[0].role == "user"
    assert loaded.history[0].content == "hello"
    assert loaded.history[1].role == "assistant"
    assert loaded.history[1].content == "world"
    assert loaded.metadata.get("runtime_replay", {}).get("last_record_count") == 2


def test_load_session_replay_no_duplicate_pair_when_saved(storage, config):
    manager = SessionManager(storage.sessions_dir, config)
    manager.new_session()
    manager.save_session("dedupe")
    session_id = manager.current.id

    log = SessionEventLog(storage.cache_dir / "runtime-events", session_id)
    _append_completed_turn(log, "same", "response")

    first = manager.load_session("dedupe")
    assert len(first.history) == 2
    manager.save_session("dedupe")

    second = manager.load_session("dedupe")
    assert len(second.history) == 2


def test_load_session_replay_appends_assistant_to_tail_user(storage, config):
    manager = SessionManager(storage.sessions_dir, config)
    manager.new_session()
    manager.current.history.append(
        Message(role="user", content="tail prompt", timestamp=datetime.now())
    )
    manager.save_session("tail-assistant")
    session_id = manager.current.id

    log = SessionEventLog(storage.cache_dir / "runtime-events", session_id)
    _append_completed_turn(log, "tail prompt", "tail answer")

    loaded = manager.load_session("tail-assistant")

    assert len(loaded.history) == 2
    assert loaded.history[-2].role == "user"
    assert loaded.history[-1].role == "assistant"
    assert loaded.history[-1].content == "tail answer"


def test_load_session_replay_ignores_incomplete_submission(storage, config):
    manager = SessionManager(storage.sessions_dir, config)
    manager.new_session()
    manager.save_session("incomplete")
    session_id = manager.current.id

    log = SessionEventLog(storage.cache_dir / "runtime-events", session_id)
    submission = Submission(op=UserTurnOp(prompt="pending", mode="chat"))
    log.append_submission(submission)

    loaded = manager.load_session("incomplete")

    assert loaded.history == []


def test_load_session_replay_tolerates_invalid_json_lines(storage, config):
    manager = SessionManager(storage.sessions_dir, config)
    manager.new_session()
    manager.save_session("invalid-log")
    session_id = manager.current.id

    path = storage.cache_dir / "runtime-events" / f"{session_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"kind":"submission"}\nnot-json\n', encoding="utf-8")

    loaded = manager.load_session("invalid-log")

    assert loaded.name == "invalid-log"
    assert isinstance(json.dumps(loaded.to_dict()), str)
