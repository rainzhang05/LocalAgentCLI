"""Tests for shared agent generation-profile helpers."""

from __future__ import annotations

from localagentcli.agents.profiles import build_generation_profile
from localagentcli.models.model_info import ModelInfo


def _model(default_max_tokens: int = 4096) -> ModelInfo:
    return ModelInfo(id="test-model", default_max_tokens=default_max_tokens)


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
