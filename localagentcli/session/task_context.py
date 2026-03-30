"""Format session agent task metadata for model-visible runtime context."""

from __future__ import annotations

import json
from hashlib import sha1

from localagentcli.session.environment_context import get_environment_context_xml
from localagentcli.session.instructions import build_system_instructions
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
    "last_error_type",
    "approval_mode",
    "rollback_count",
    "usage_prompt_tokens",
    "usage_completion_tokens",
    "usage_total_tokens",
    "summary",
    "updated_at",
)

_TASK_STATE_SNAPSHOT_KEYS = (
    "active",
    "route",
    "phase",
    "step_index",
    "step_description",
    "pending_tool",
    "wait_reason",
    "retry_count",
    "last_error",
    "last_error_type",
    "approval_mode",
    "rollback_count",
    "usage_prompt_tokens",
    "usage_completion_tokens",
    "usage_total_tokens",
    "summary",
)

_RELEVANT_CONFIG_OVERRIDE_KEYS = (
    "generation.reasoning_effort",
    "safety.approval_mode",
    "safety.sandbox_mode",
)


def format_agent_task_runtime_section(session: Session) -> str | None:
    """Return a plain-text snapshot of ``metadata['agent_task_state']`` for prompts.

    Used during agent loop steps so the model sees current phase, step, approvals,
    etc. Returns ``None`` when not in agent mode, when state is missing, when the
    task is not active, or when there is nothing to show.
    """
    if session.mode != "agent":
        return None

    raw = _extract_agent_task_state(session.metadata.get("agent_task_state"), require_active=True)
    if raw is None:
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


def build_turn_context_snapshot(session: Session) -> dict[str, object]:
    """Build a normalized, model-relevant runtime snapshot for diffing.

    The snapshot intentionally omits highly volatile per-turn timestamps so that
    diffs remain signal-rich for model prompts and operator debugging.
    """
    instructions = build_system_instructions(session)
    env_xml = get_environment_context_xml(session.workspace)
    task_state = _extract_agent_task_state(
        session.metadata.get("agent_task_state"),
        require_active=False,
    )
    normalized_task_state = _normalize_task_state_for_snapshot(task_state)
    long_horizon = session.metadata.get("long_horizon_memory")
    long_horizon_count = len(long_horizon) if isinstance(long_horizon, list) else 0

    return {
        "session": {
            "mode": session.mode,
            "workspace": session.workspace,
            "model": session.model,
            "provider": session.provider,
        },
        "instructions": {
            "count": len(instructions),
            "fingerprint": _fingerprint(instructions),
        },
        "environment": {
            "fingerprint": _fingerprint(env_xml),
        },
        "task_state": normalized_task_state,
        "config_overrides": {
            key: session.config_overrides[key]
            for key in _RELEVANT_CONFIG_OVERRIDE_KEYS
            if key in session.config_overrides
        },
        "memory": {
            "long_horizon_count": long_horizon_count,
        },
    }


def _extract_agent_task_state(
    raw: object,
    *,
    require_active: bool,
) -> dict[str, object] | None:
    if not isinstance(raw, dict):
        return None
    if require_active and not bool(raw.get("active")):
        return None
    return dict(raw)


def _normalize_task_state_for_snapshot(state: dict[str, object] | None) -> dict[str, object]:
    if state is None:
        return {}

    normalized: dict[str, object] = {}
    for key in _TASK_STATE_SNAPSHOT_KEYS:
        value = state.get(key)
        if value is None or value == "":
            continue
        if key in {
            "retry_count",
            "rollback_count",
            "usage_prompt_tokens",
            "usage_completion_tokens",
            "usage_total_tokens",
        }:
            if isinstance(value, bool):
                continue
            if isinstance(value, int):
                normalized[key] = value
                continue
            if isinstance(value, float):
                normalized[key] = int(value)
                continue
            if isinstance(value, str):
                stripped = value.strip()
                if not stripped:
                    continue
                try:
                    normalized[key] = int(float(stripped))
                except ValueError:
                    continue
                continue
            continue
        if key == "summary":
            text = str(value).strip()
            if not text:
                continue
            if len(text) > _SUMMARY_MAX_LEN:
                text = f"{text[: _SUMMARY_MAX_LEN - 3]}..."
            normalized[key] = text
            continue
        normalized[key] = value
    return normalized


def _fingerprint(value: object) -> str:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return sha1(serialized.encode("utf-8")).hexdigest()
