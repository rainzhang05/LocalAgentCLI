"""Tests for structured turn-context diff helpers."""

from __future__ import annotations

from localagentcli.session.context_diff import (
    ContextDiffTracker,
    render_context_diff_for_prompt,
)


def test_diff_initial_snapshot_marks_initial_context():
    current = {
        "session": {"mode": "agent", "model": "m1"},
        "task_state": {"phase": "planning"},
    }

    diff = ContextDiffTracker.diff(None, current)

    assert diff.initial is True
    assert diff.has_changes is True
    assert diff.previous_fingerprint == ""
    assert diff.current_fingerprint
    assert diff.changes == current


def test_diff_no_changes_returns_empty_delta():
    baseline = {
        "session": {"mode": "agent", "model": "m1"},
        "task_state": {"phase": "planning"},
    }

    diff = ContextDiffTracker.diff(baseline, baseline)

    assert diff.initial is False
    assert diff.has_changes is False
    assert diff.changes == {}


def test_diff_tracks_nested_field_changes_only():
    previous = {
        "session": {"mode": "agent", "model": "m1"},
        "task_state": {"phase": "planning", "retry_count": 0},
    }
    current = {
        "session": {"mode": "agent", "model": "m2"},
        "task_state": {"phase": "executing", "retry_count": 1},
    }

    diff = ContextDiffTracker.diff(previous, current)

    assert diff.initial is False
    assert diff.changes["session"]["model"]["before"] == "m1"
    assert diff.changes["session"]["model"]["after"] == "m2"
    assert diff.changes["task_state"]["phase"]["before"] == "planning"
    assert diff.changes["task_state"]["phase"]["after"] == "executing"
    assert diff.changes["task_state"]["retry_count"]["before"] == 0
    assert diff.changes["task_state"]["retry_count"]["after"] == 1


def test_tracker_compute_advances_baseline_between_turns():
    tracker = ContextDiffTracker()
    first = {
        "session": {"mode": "agent", "model": "m1"},
        "task_state": {"phase": "planning"},
    }
    second = {
        "session": {"mode": "agent", "model": "m1"},
        "task_state": {"phase": "executing"},
    }

    first_diff = tracker.compute(first)
    second_diff = tracker.compute(second)

    assert first_diff.initial is True
    assert second_diff.initial is False
    assert second_diff.changes == {
        "task_state": {
            "phase": {
                "before": "planning",
                "after": "executing",
            }
        }
    }


def test_render_context_diff_for_prompt_handles_initial_and_updates():
    tracker = ContextDiffTracker()
    tracker.compute({"session": {"mode": "agent"}, "task_state": {"phase": "planning"}})
    second_diff = tracker.compute(
        {
            "session": {"mode": "agent"},
            "task_state": {"phase": "executing", "retry_count": 1},
        }
    )

    rendered = render_context_diff_for_prompt(second_diff)

    assert rendered is not None
    assert "task_state.phase" in rendered
    assert "planning" in rendered
    assert "executing" in rendered
    assert "task_state.retry_count" in rendered


def test_render_context_diff_for_prompt_returns_none_without_changes():
    diff = ContextDiffTracker.diff(
        {"session": {"mode": "agent"}},
        {"session": {"mode": "agent"}},
    )

    assert render_context_diff_for_prompt(diff) is None
