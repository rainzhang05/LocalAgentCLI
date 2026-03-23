"""Tests for adaptive truncation helpers."""

from __future__ import annotations

from localagentcli.agents.truncation import (
    adaptive_output_char_budget,
    truncate_for_model_output,
    truncate_middle,
)
from localagentcli.models.model_info import ModelInfo


def test_adaptive_output_char_budget_scales_with_model_defaults() -> None:
    small = adaptive_output_char_budget(ModelInfo(id="small", default_max_tokens=512))
    large = adaptive_output_char_budget(ModelInfo(id="large", default_max_tokens=8192))

    assert small < large
    assert small >= 1200


def test_truncate_middle_preserves_prefix_and_suffix() -> None:
    text = "A" * 300 + "B" * 300
    truncated = truncate_middle(text, 120)

    assert truncated.was_truncated is True
    assert truncated.original_chars == 600
    assert truncated.retained_chars <= 120
    assert truncated.text.startswith("A")
    assert truncated.text.endswith("B")


def test_truncate_for_model_output_keeps_short_text() -> None:
    model_info = ModelInfo(id="test", default_max_tokens=4096)
    result = truncate_for_model_output("short output", model_info)

    assert result.was_truncated is False
    assert result.text == "short output"
