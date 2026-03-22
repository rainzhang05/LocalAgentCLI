"""Format session agent task metadata for model-visible runtime context."""

from __future__ import annotations

from localagentcli.session.state import Session

AGENT_TASK_RUNTIME_HEADING = "Agent task status (runtime):"

_SUMMARY_MAX_LEN = 240

# Emit fields in this order for stable, testable prompts.
_RUNTIME_FIELD_KEYS = (
    "route",
    "phase",
    "step_index",
    "step_description",
    "pending_tool",
    "wait_reason",
    "retry_count",
    "last_error",
    "approval_mode",
    "rollback_count",
    "summary",
    "updated_at",
)


def format_agent_task_runtime_section(session: Session) -> str | None:
    """Return a plain-text snapshot of ``metadata['agent_task_state']`` for prompts.

    Used during agent loop steps so the model sees current phase, step, approvals,
    etc. Returns ``None`` when not in agent mode, when state is missing, when the
    task is not active, or when there is nothing to show.
    """
    if session.mode != "agent":
        return None

    raw = session.metadata.get("agent_task_state")
    if not isinstance(raw, dict):
        return None

    if not bool(raw.get("active")):
        return None

    lines: list[str] = []
    for key in _RUNTIME_FIELD_KEYS:
        value = raw.get(key)
        if value is None or value == "":
            continue
        if key == "summary":
            text = str(value).strip()
            if not text:
                continue
            if len(text) > _SUMMARY_MAX_LEN:
                text = f"{text[: _SUMMARY_MAX_LEN - 3]}..."
            lines.append(f"{key}: {text}")
        else:
            lines.append(f"{key}: {value}")

    if not lines:
        return None

    return "\n".join(lines)
