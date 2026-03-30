"""Failure classification and recovery policy helpers for agent execution."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from localagentcli.tools.base import ToolResult


class FailureClass(str, Enum):
    """Normalized failure classes used by retry/replan policy."""

    MODEL_TRANSIENT = "model_transient"
    MODEL_TERMINAL = "model_terminal"
    TOOL_TIMEOUT = "tool_timeout"
    TOOL_DENIED = "tool_denied"
    TOOL_BLOCKED = "tool_blocked"
    TOOL_ERROR = "tool_error"
    UNKNOWN = "unknown"


_MODEL_TRANSIENT_KEYWORDS = (
    "timeout",
    "timed out",
    "temporar",
    "try again",
    "retry",
    "rate limit",
    "429",
    "503",
    "overload",
    "connection",
    "stream disconnected",
)

_MODEL_TERMINAL_KEYWORDS = (
    "unauthorized",
    "forbidden",
    "authentication",
    "api key",
    "invalid request",
    "malformed",
    "unsupported",
    "context window",
    "quota exceeded",
    "permission denied",
)


_DEFAULT_RETRY_BUDGETS: dict[FailureClass, int] = {
    FailureClass.MODEL_TRANSIENT: 3,
    FailureClass.MODEL_TERMINAL: 1,
    FailureClass.TOOL_TIMEOUT: 2,
    FailureClass.TOOL_DENIED: 1,
    FailureClass.TOOL_BLOCKED: 1,
    FailureClass.TOOL_ERROR: 2,
    FailureClass.UNKNOWN: 1,
}

_REPLAN_FAILURE_CLASSES: set[FailureClass] = {
    FailureClass.TOOL_DENIED,
    FailureClass.TOOL_BLOCKED,
    FailureClass.TOOL_ERROR,
}


def _tool_error_text(result: ToolResult) -> str:
    raw = result.error
    if isinstance(raw, str):
        return raw
    return ""


@dataclass(frozen=True)
class FailureContext:
    """Captured failure classification and associated detail."""

    failure_class: FailureClass
    detail: str


@dataclass(frozen=True)
class FailureBudgetState:
    """Current retry-budget state for a classified failure."""

    failure_class: FailureClass
    attempt: int
    retry_budget: int

    @property
    def exhausted(self) -> bool:
        return self.attempt >= self.retry_budget


def classify_model_failure(error_detail: str | None) -> FailureContext:
    """Classify model-side failures from textual provider/runtime detail."""
    detail = (error_detail or "").strip()
    normalized = detail.lower()
    if normalized:
        if any(token in normalized for token in _MODEL_TERMINAL_KEYWORDS):
            return FailureContext(FailureClass.MODEL_TERMINAL, detail)
        if any(token in normalized for token in _MODEL_TRANSIENT_KEYWORDS):
            return FailureContext(FailureClass.MODEL_TRANSIENT, detail)
    return FailureContext(FailureClass.MODEL_TRANSIENT, detail or "Unknown model error.")


def classify_tool_failure(result: ToolResult) -> FailureContext | None:
    """Classify tool execution failures from structured ToolResult payload."""
    if result.status == "success":
        return None

    if result.status == "timeout":
        detail = _tool_error_text(result) or result.summary or "Tool timed out."
        return FailureContext(FailureClass.TOOL_TIMEOUT, detail)

    if result.status == "denied":
        detail = _tool_error_text(result) or result.summary or "Tool execution denied."
        return FailureContext(FailureClass.TOOL_DENIED, detail)

    if result.status == "error":
        summary = (result.summary or "").strip()
        error = _tool_error_text(result).strip()
        detail = error or summary or "Tool execution failed."
        lower = f"{summary}\n{error}".lower()
        if summary.startswith("Blocked tool") or "violated a safety rule" in lower:
            return FailureContext(FailureClass.TOOL_BLOCKED, detail)
        return FailureContext(FailureClass.TOOL_ERROR, detail)

    detail = _tool_error_text(result) or result.summary or "Unknown tool failure."
    return FailureContext(FailureClass.UNKNOWN, detail)


def retry_budget_for_failure(failure_class: FailureClass) -> int:
    """Return retry budget for a given failure class."""
    return _DEFAULT_RETRY_BUDGETS.get(failure_class, 1)


def should_replan_after_failure(failure_class: FailureClass) -> bool:
    """Whether a failure class should trigger a planner revision attempt."""
    return failure_class in _REPLAN_FAILURE_CLASSES


def failure_class_hint(failure_class: FailureClass) -> str:
    """Short planner hint explaining what to do after a classified failure."""
    if failure_class is FailureClass.TOOL_DENIED:
        return "Avoid denied actions and prefer a safer, approval-friendly approach."
    if failure_class is FailureClass.TOOL_BLOCKED:
        return "Stay within sandbox and workspace safety policy constraints."
    if failure_class is FailureClass.TOOL_TIMEOUT:
        return "Use narrower or faster tool operations and verify partial output."
    if failure_class is FailureClass.TOOL_ERROR:
        return "Adjust tool arguments and add validation before retrying."
    if failure_class is FailureClass.MODEL_TERMINAL:
        return "Treat this as terminal model failure; avoid repeating the same request."
    if failure_class is FailureClass.MODEL_TRANSIENT:
        return "Treat this as transient and retry with minimal changes."
    return "Handle the failure conservatively and prefer low-risk recovery."


def failure_class_label(failure_class: FailureClass) -> str:
    """Human-readable class label for operator/model-facing summaries."""
    return failure_class.value.replace("_", " ")


def update_failure_counters(
    counters: dict[FailureClass, int],
    failure_context: FailureContext,
) -> FailureBudgetState:
    """Increment per-class counters and return current budget state."""
    attempt = counters.get(failure_context.failure_class, 0) + 1
    counters[failure_context.failure_class] = attempt
    return FailureBudgetState(
        failure_class=failure_context.failure_class,
        attempt=attempt,
        retry_budget=retry_budget_for_failure(failure_context.failure_class),
    )
