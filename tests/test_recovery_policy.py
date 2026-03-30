"""Tests for agent retry/recovery classification policy."""

from __future__ import annotations

from localagentcli.agents.recovery import (
    FailureClass,
    classify_model_failure,
    classify_tool_failure,
    should_replan_after_failure,
    update_failure_counters,
)
from localagentcli.tools.base import ToolResult


def test_classify_model_failure_terminal_keyword():
    context = classify_model_failure("Invalid API key for provider")
    assert context.failure_class is FailureClass.MODEL_TERMINAL


def test_classify_model_failure_transient_keyword():
    context = classify_model_failure("Rate limit exceeded, retry later")
    assert context.failure_class is FailureClass.MODEL_TRANSIENT


def test_classify_tool_failure_blocked_detection():
    blocked = ToolResult.error_result(
        "Blocked tool 'file_write'",
        "The requested action violated a safety rule.",
    )
    context = classify_tool_failure(blocked)
    assert context is not None
    assert context.failure_class is FailureClass.TOOL_BLOCKED


def test_replan_policy_targets_tool_failures_not_transient_model_failures():
    assert should_replan_after_failure(FailureClass.TOOL_DENIED) is True
    assert should_replan_after_failure(FailureClass.TOOL_ERROR) is True
    assert should_replan_after_failure(FailureClass.MODEL_TRANSIENT) is False


def test_update_failure_counters_tracks_attempts():
    counters: dict[FailureClass, int] = {}
    context = classify_model_failure("temporary timeout")

    first = update_failure_counters(counters, context)
    second = update_failure_counters(counters, context)

    assert first.attempt == 1
    assert second.attempt == 2
    assert second.retry_budget >= 1
