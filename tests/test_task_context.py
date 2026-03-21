"""Tests for agent task runtime prompt section formatting."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from localagentcli.session.state import Session
from localagentcli.session.task_context import (
    AGENT_TASK_RUNTIME_HEADING,
    format_agent_task_runtime_section,
)


def _session(workspace: Path, *, mode: str = "agent", **kwargs: object) -> Session:
    now = datetime(2025, 1, 15, 10, 0, 0)
    base = dict(
        id="s1",
        name=None,
        mode=mode,
        model="m",
        provider="p",
        workspace=str(workspace),
        created_at=now,
        updated_at=now,
    )
    base.update(kwargs)
    return Session(**base)


def test_format_returns_none_for_chat_mode(tmp_path: Path):
    session = _session(tmp_path, mode="chat", metadata={})
    assert format_agent_task_runtime_section(session) is None


def test_format_returns_none_when_no_agent_task_state(tmp_path: Path):
    session = _session(tmp_path, metadata={})
    assert format_agent_task_runtime_section(session) is None


def test_format_returns_none_when_agent_task_state_not_a_dict(tmp_path: Path):
    session = _session(tmp_path, metadata={"agent_task_state": "invalid"})
    assert format_agent_task_runtime_section(session) is None


def test_format_returns_none_when_inactive(tmp_path: Path):
    session = _session(
        tmp_path,
        metadata={
            "agent_task_state": {
                "active": False,
                "phase": "completed",
                "summary": "Done.",
            }
        },
    )
    assert format_agent_task_runtime_section(session) is None


def test_format_returns_none_when_active_but_empty_fields(tmp_path: Path):
    session = _session(
        tmp_path,
        metadata={"agent_task_state": {"active": True}},
    )
    assert format_agent_task_runtime_section(session) is None


def test_format_includes_fields_in_stable_order(tmp_path: Path):
    session = _session(
        tmp_path,
        metadata={
            "agent_task_state": {
                "active": True,
                "phase": "waiting_approval",
                "route": "multi_step_task",
                "step_index": 2,
                "step_description": "Edit file",
                "pending_tool": "file_write",
                "approval_mode": "balanced",
                "rollback_count": 1,
                "summary": "Working on edits.",
                "updated_at": "2025-01-15T12:00:00",
            }
        },
    )
    text = format_agent_task_runtime_section(session)
    assert text is not None
    lines = text.split("\n")
    assert lines[0] == "route: multi_step_task"
    assert lines[1] == "phase: waiting_approval"
    assert "step_index: 2" in text
    assert "pending_tool: file_write" in text
    assert "summary: Working on edits." in text


def test_format_truncates_long_summary(tmp_path: Path):
    long_summary = "x" * 300
    session = _session(
        tmp_path,
        metadata={
            "agent_task_state": {
                "active": True,
                "phase": "executing",
                "summary": long_summary,
            }
        },
    )
    text = format_agent_task_runtime_section(session)
    assert text is not None
    summary_line = [ln for ln in text.split("\n") if ln.startswith("summary:")][0]
    assert len(summary_line) < len(long_summary) + 20
    assert summary_line.endswith("...")


def test_heading_constant_for_loop_use():
    assert "runtime" in AGENT_TASK_RUNTIME_HEADING.lower()
