"""Session-level provider usage normalization and budgeting helpers."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from localagentcli.models.backends.base import StreamChunk
from localagentcli.session.state import Session

USAGE_BUDGET_KEY = "usage_budget"

_PROMPT_KEYS = (
    "prompt_tokens",
    "input_tokens",
    "prompt_token_count",
    "input_token_count",
    "promptTokens",
    "inputTokens",
)
_COMPLETION_KEYS = (
    "completion_tokens",
    "output_tokens",
    "completion_token_count",
    "output_token_count",
    "completionTokens",
    "outputTokens",
)
_TOTAL_KEYS = (
    "total_tokens",
    "total_token_count",
    "totalTokens",
)


def normalize_usage_counts(usage: object) -> dict[str, int]:
    """Normalize provider usage payloads into prompt/completion/total tokens."""
    if not isinstance(usage, dict):
        return {}

    prompt = _first_non_negative_int(usage, _PROMPT_KEYS)
    completion = _first_non_negative_int(usage, _COMPLETION_KEYS)
    total = _first_non_negative_int(usage, _TOTAL_KEYS)

    if total is None and (prompt is not None or completion is not None):
        total = (prompt or 0) + (completion or 0)

    normalized: dict[str, int] = {}
    if prompt is not None:
        normalized["prompt_tokens"] = prompt
    if completion is not None:
        normalized["completion_tokens"] = completion
    if total is not None:
        normalized["total_tokens"] = total
    return normalized


def usage_from_stream_chunks(chunks: list[StreamChunk]) -> dict[str, int]:
    """Extract normalized usage counts from streamed chunks."""
    merged_usage: dict[str, Any] = {}
    for chunk in chunks:
        if isinstance(chunk.usage, dict):
            merged_usage.update(chunk.usage)
    return normalize_usage_counts(merged_usage)


def latest_usage_counts(metadata: dict[str, object]) -> dict[str, int]:
    """Return latest normalized usage counts from session metadata."""
    budget = metadata.get(USAGE_BUDGET_KEY)
    if not isinstance(budget, dict):
        return {}
    latest = budget.get("latest")
    if not isinstance(latest, dict):
        return {}
    return normalize_usage_counts(latest)


def usage_budget_snapshot(metadata: dict[str, object]) -> dict[str, int]:
    """Return compaction-facing usage snapshot keys from session metadata."""
    latest = latest_usage_counts(metadata)
    snapshot: dict[str, int] = {}
    if "prompt_tokens" in latest:
        snapshot["latest_prompt_tokens"] = latest["prompt_tokens"]
    if "completion_tokens" in latest:
        snapshot["latest_completion_tokens"] = latest["completion_tokens"]
    if "total_tokens" in latest:
        snapshot["latest_total_tokens"] = latest["total_tokens"]
    return snapshot


def update_session_usage_budget(
    session: Session,
    usage: object,
    *,
    source: str,
) -> dict[str, int] | None:
    """Update session usage-budget metadata from one model call's usage payload."""
    normalized = normalize_usage_counts(usage)
    if not normalized:
        return None

    raw_budget = session.metadata.get(USAGE_BUDGET_KEY)
    budget = dict(raw_budget) if isinstance(raw_budget, dict) else {}

    raw_cumulative = budget.get("cumulative")
    cumulative = (
        normalize_usage_counts(raw_cumulative)
        if isinstance(raw_cumulative, dict)
        else {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    )
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        cumulative[key] = cumulative.get(key, 0) + normalized.get(key, 0)

    turns_with_usage = _coerce_non_negative_int(budget.get("turns_with_usage")) or 0
    latest: dict[str, object] = dict(normalized)
    latest["source"] = source
    latest["updated_at"] = datetime.now().isoformat()

    budget["latest"] = latest
    budget["cumulative"] = cumulative
    budget["turns_with_usage"] = turns_with_usage + 1
    session.metadata[USAGE_BUDGET_KEY] = budget
    return normalized


def _first_non_negative_int(values: dict[str, Any], keys: tuple[str, ...]) -> int | None:
    for key in keys:
        parsed = _coerce_non_negative_int(values.get(key))
        if parsed is not None:
            return parsed
    return None


def _coerce_non_negative_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return max(value, 0)
    if isinstance(value, float):
        if value != value:  # NaN guard
            return None
        return max(int(value), 0)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            parsed = int(float(stripped))
        except ValueError:
            return None
        return max(parsed, 0)
    return None
