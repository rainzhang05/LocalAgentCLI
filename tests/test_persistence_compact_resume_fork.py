"""Behavior-level persistence tests for compact/resume/fork invariants."""

from __future__ import annotations

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


def test_compacted_prefix_preserved_across_save_load(storage, config):
    manager = SessionManager(storage.sessions_dir, config)
    manager.new_session()
    manager.current.history.append(
        Message(
            role="system",
            content="Summary of older conversation.",
            timestamp=datetime.now(),
            is_summary=True,
        )
    )
    manager.current.history.append(
        Message(role="user", content="latest question", timestamp=datetime.now())
    )
    manager.save_session("compact")

    restored = manager.load_session("compact")

    assert restored.history[0].is_summary is True
    assert restored.history[0].content == "Summary of older conversation."
    assert restored.history[1].content == "latest question"


def test_resume_replay_is_history_superset(storage, config):
    manager = SessionManager(storage.sessions_dir, config)
    manager.new_session()
    manager.current.history.append(
        Message(role="user", content="already saved", timestamp=datetime.now())
    )
    manager.current.history.append(
        Message(role="assistant", content="already answer", timestamp=datetime.now())
    )
    manager.save_session("resume")

    log = SessionEventLog(storage.cache_dir / "runtime-events", manager.current.id)
    _append_completed_turn(log, "new prompt", "new answer")

    restored = manager.load_session("resume")

    contents = [msg.content for msg in restored.history]
    assert "already saved" in contents
    assert "already answer" in contents
    assert "new prompt" in contents
    assert "new answer" in contents


def test_fork_divergence_does_not_mutate_parent(storage, config):
    manager = SessionManager(storage.sessions_dir, config)
    manager.new_session()
    manager.current.history.append(Message(role="user", content="parent", timestamp=datetime.now()))
    manager.save_session("parent")

    forked = manager.fork_session("parent", "child")
    forked.history.append(Message(role="assistant", content="child-only", timestamp=datetime.now()))
    manager.save_session("child")

    parent = manager.load_session("parent")
    child = manager.load_session("child")

    assert [m.content for m in parent.history] == ["parent"]
    assert [m.content for m in child.history] == ["parent", "child-only"]
