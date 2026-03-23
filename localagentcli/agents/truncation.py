"""Model-aware output truncation helpers for agent/runtime payloads."""

from __future__ import annotations

from dataclasses import dataclass

from localagentcli.models.model_info import ModelInfo

_DEFAULT_MAX_TOKENS = 4096
_APPROX_CHARS_PER_TOKEN = 4
_MIN_OUTPUT_CHARS = 1200
_MAX_OUTPUT_CHARS = 24000


@dataclass(frozen=True)
class TruncatedText:
    """Result of applying an adaptive truncation policy to text."""

    text: str
    was_truncated: bool
    original_chars: int
    retained_chars: int


def adaptive_output_char_budget(model_info: ModelInfo) -> int:
    """Derive a truncation budget from model metadata.

    The budget scales with `default_max_tokens` and is clamped to a safe range
    so tiny models still preserve meaningful context while large models can keep
    richer tool output.
    """
    default_max_tokens = _coerce_positive_int(model_info.default_max_tokens, _DEFAULT_MAX_TOKENS)
    context_scale = _coerce_context_scale(model_info.effective_context_window_percent)
    raw_budget = int(default_max_tokens * _APPROX_CHARS_PER_TOKEN * context_scale * 0.75)
    return max(_MIN_OUTPUT_CHARS, min(raw_budget, _MAX_OUTPUT_CHARS))


def truncate_for_model_output(text: str, model_info: ModelInfo) -> TruncatedText:
    """Apply model-aware middle truncation and emit truncation metadata."""
    budget = adaptive_output_char_budget(model_info)
    return truncate_middle(text, budget)


def truncate_middle(text: str, max_chars: int) -> TruncatedText:
    """Truncate the middle of text while preserving prefix and suffix context."""
    original_chars = len(text)
    if max_chars <= 0:
        return TruncatedText(
            text="",
            was_truncated=original_chars > 0,
            original_chars=original_chars,
            retained_chars=0,
        )
    if original_chars <= max_chars:
        return TruncatedText(
            text=text,
            was_truncated=False,
            original_chars=original_chars,
            retained_chars=original_chars,
        )

    marker = f"\n...[{original_chars - max_chars} chars truncated]...\n"
    if max_chars <= len(marker):
        clipped = marker[:max_chars]
        return TruncatedText(
            text=clipped,
            was_truncated=True,
            original_chars=original_chars,
            retained_chars=len(clipped),
        )

    payload_chars = max_chars - len(marker)
    left_chars = payload_chars // 2
    right_chars = payload_chars - left_chars
    truncated = f"{text[:left_chars]}{marker}{text[-right_chars:]}"
    return TruncatedText(
        text=truncated,
        was_truncated=True,
        original_chars=original_chars,
        retained_chars=len(truncated),
    )


def _coerce_positive_int(value: object, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value if value > 0 else default
    if isinstance(value, float):
        coerced = int(value)
        return coerced if coerced > 0 else default
    if isinstance(value, str):
        try:
            parsed = int(value)
        except ValueError:
            return default
        return parsed if parsed > 0 else default
    return default


def _coerce_context_scale(value: object) -> float:
    if isinstance(value, bool):
        return 0.8
    if isinstance(value, int | float):
        numeric = float(value)
    elif isinstance(value, str):
        try:
            numeric = float(value)
        except ValueError:
            return 0.8
    else:
        return 0.8

    if numeric <= 0:
        return 0.8
    if numeric > 1:
        numeric = numeric / 100.0
    return max(0.25, min(numeric, 1.0))
