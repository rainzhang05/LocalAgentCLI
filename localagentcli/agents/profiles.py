"""Shared generation-profile helpers for agent-mode model calls."""

from __future__ import annotations

from localagentcli.models.model_info import ModelInfo

_DEFAULT_TEMPERATURE = 0.7
_DEFAULT_TOP_P = 1.0
_DEFAULT_MAX_TOKENS = 4096


def build_generation_profile(
    phase: str,
    base_config: dict[str, object] | None,
    model_info: ModelInfo,
) -> dict[str, object]:
    """Derive a model-aware generation profile for a specific agent phase."""
    base = dict(base_config or {})
    temperature = _coerce_float(base.get("temperature"), _DEFAULT_TEMPERATURE)
    top_p = _coerce_float(base.get("top_p"), _DEFAULT_TOP_P)

    default_tokens = _coerce_int(model_info.default_max_tokens, _DEFAULT_MAX_TOKENS)
    if default_tokens <= 0:
        default_tokens = _DEFAULT_MAX_TOKENS

    max_tokens = _coerce_int(base.get("max_tokens"), default_tokens)
    if max_tokens <= 0:
        max_tokens = default_tokens
    reasoning_effort = _resolve_reasoning_effort(base.get("reasoning_effort"), model_info)

    if phase == "triage":
        profile: dict[str, object] = {
            "temperature": min(temperature, 0.1),
            "max_tokens": min(max_tokens, 512),
            "top_p": top_p,
        }
        if reasoning_effort is not None:
            profile["reasoning_effort"] = reasoning_effort
        return profile
    if phase == "planning":
        profile = {
            "temperature": min(temperature, 0.1),
            "max_tokens": min(max_tokens, 2048),
            "top_p": top_p,
        }
        if reasoning_effort is not None:
            profile["reasoning_effort"] = reasoning_effort
        return profile
    if phase == "step":
        profile = {
            "temperature": min(temperature, 0.2),
            "max_tokens": max_tokens,
            "top_p": top_p,
        }
        if reasoning_effort is not None:
            profile["reasoning_effort"] = reasoning_effort
        return profile
    profile = {
        "temperature": temperature,
        "max_tokens": max_tokens,
        "top_p": top_p,
    }
    if reasoning_effort is not None:
        profile["reasoning_effort"] = reasoning_effort
    return profile


def _coerce_float(value: object, default: float) -> float:
    if isinstance(value, bool):
        return float(default)
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return default
    return default


def _coerce_int(value: object, default: int) -> int:
    if isinstance(value, bool):
        return int(default)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _resolve_reasoning_effort(value: object, model_info: ModelInfo) -> str | None:
    if model_info.capabilities.get("reasoning") is False:
        return None

    selected = _normalize_reasoning_effort(value)
    if selected is None:
        return None

    supported = [
        normalized
        for level in model_info.supported_reasoning_levels
        if (normalized := _normalize_reasoning_effort(level)) is not None
    ]
    if not supported:
        return selected
    if selected in supported:
        return selected
    return supported[(len(supported) - 1) // 2]


def _normalize_reasoning_effort(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if normalized in {"low", "medium", "high"}:
        return normalized
    return None
