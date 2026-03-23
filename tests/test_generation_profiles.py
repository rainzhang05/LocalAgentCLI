"""Tests for shared agent generation-profile helpers."""

from __future__ import annotations

from localagentcli.agents.profiles import build_generation_profile
from localagentcli.models.model_info import ModelInfo


def _model(default_max_tokens: int = 4096) -> ModelInfo:
    return ModelInfo(id="test-model", default_max_tokens=default_max_tokens)


def _reasoning_model(levels: list[str]) -> ModelInfo:
    return ModelInfo(
        id="reasoning-model",
        default_max_tokens=4096,
        supported_reasoning_levels=levels,
        capabilities={"reasoning": True},
    )


def test_step_profile_uses_model_default_max_tokens_by_default() -> None:
    profile = build_generation_profile("step", {}, _model(default_max_tokens=9000))

    assert profile["temperature"] == 0.2
    assert profile["max_tokens"] == 9000
    assert profile["top_p"] == 1.0


def test_phase_profiles_apply_expected_caps() -> None:
    base = {"temperature": 0.9, "max_tokens": 9000, "top_p": 0.8}

    triage = build_generation_profile("triage", base, _model(default_max_tokens=12000))
    planning = build_generation_profile("planning", base, _model(default_max_tokens=12000))

    assert triage == {"temperature": 0.1, "max_tokens": 512, "top_p": 0.8}
    assert planning == {"temperature": 0.1, "max_tokens": 2048, "top_p": 0.8}


def test_profile_coerces_string_values_and_ignores_bool_for_max_tokens() -> None:
    profile = build_generation_profile(
        "step",
        {
            "temperature": "0.05",
            "max_tokens": True,
            "top_p": "0.9",
        },
        _model(default_max_tokens=1234),
    )

    assert profile["temperature"] == 0.05
    assert profile["max_tokens"] == 1234
    assert profile["top_p"] == 0.9


def test_profile_preserves_reasoning_effort_when_supported() -> None:
    profile = build_generation_profile(
        "step",
        {"reasoning_effort": "high"},
        _reasoning_model(["low", "medium", "high"]),
    )

    assert profile["reasoning_effort"] == "high"


def test_profile_falls_back_to_supported_reasoning_effort_when_unsupported() -> None:
    profile = build_generation_profile(
        "step",
        {"reasoning_effort": "high"},
        _reasoning_model(["low", "medium"]),
    )

    assert profile["reasoning_effort"] == "low"


def test_profile_omits_reasoning_effort_when_model_explicitly_non_reasoning() -> None:
    profile = build_generation_profile(
        "step",
        {"reasoning_effort": "high"},
        ModelInfo(
            id="non-reasoning",
            default_max_tokens=4096,
            capabilities={"reasoning": False},
        ),
    )

    assert "reasoning_effort" not in profile
