"""Tests for shared readiness and provenance helpers."""

from __future__ import annotations

from localagentcli.models.readiness import (
    build_capability_assessments,
    build_target_readiness,
    default_local_capability_provenance,
    format_capability_line,
    format_readiness_tradeoff,
    is_agent_ready,
    local_capability_provenance,
    normalize_capability_provenance,
    readiness_posture_label,
    selection_state_label,
)


class TestLocalCapabilityProvenance:
    def test_reasoning_supported_marks_inferred(self):
        provenance = local_capability_provenance(reasoning_supported=True)

        assert provenance["tool_use"]["tier"] == "verified"
        assert provenance["reasoning"]["tier"] == "inferred"
        assert provenance["streaming"]["tier"] == "verified"

    def test_reasoning_unsupported_marks_unknown(self):
        provenance = local_capability_provenance(reasoning_supported=False)

        assert provenance["reasoning"]["tier"] == "unknown"
        assert "not been verified" in provenance["reasoning"]["reason"]


class TestNormalizeCapabilityProvenance:
    def test_fills_missing_entries_from_default_builder(self):
        capabilities = {"tool_use": False, "reasoning": True, "streaming": True}
        normalized = normalize_capability_provenance(
            {"tool_use": {"tier": "verified", "reason": "Known false."}},
            capabilities,
            default_builder=default_local_capability_provenance,
        )

        assert normalized["tool_use"]["tier"] == "verified"
        assert normalized["reasoning"]["tier"] == "inferred"
        assert normalized["streaming"]["tier"] == "verified"

    def test_skips_entries_with_invalid_tier(self):
        capabilities = {"tool_use": True, "reasoning": False, "streaming": True}
        normalized = normalize_capability_provenance(
            {"tool_use": {"tier": "not_a_valid_tier", "reason": "ignored"}},
            capabilities,
            default_builder=default_local_capability_provenance,
        )
        assert normalized["tool_use"]["tier"] != "not_a_valid_tier"


class TestBuildTargetReadiness:
    def test_agent_ready_requires_supported_tool_use_and_trusted_tier(self):
        readiness = build_target_readiness(
            kind="provider",
            selection_state="api_discovered",
            capabilities={"tool_use": True, "reasoning": False, "streaming": True},
            capability_provenance={
                "tool_use": {"tier": "inferred", "reason": "Provider semantics."},
                "reasoning": {"tier": "inferred", "reason": "Provider semantics."},
                "streaming": {"tier": "inferred", "reason": "Provider semantics."},
            },
        )

        assert is_agent_ready(readiness) is True
        assert readiness.summary == "Agent mode available: tool use yes [inferred]."
        assert readiness.operator_posture == "ready"
        assert "trusted tool-use readiness" in readiness.tradeoff

    def test_legacy_fallback_is_not_agent_ready(self):
        assessments = build_capability_assessments(
            {"tool_use": True, "reasoning": False, "streaming": True},
            {
                "tool_use": {"tier": "legacy_fallback", "reason": "Fallback only."},
                "reasoning": {"tier": "legacy_fallback", "reason": "Fallback only."},
                "streaming": {"tier": "legacy_fallback", "reason": "Fallback only."},
            },
        )

        assert is_agent_ready(assessments) is False


class TestFormattingHelpers:
    def test_format_capability_line(self):
        readiness = build_target_readiness(
            kind="local",
            selection_state="local",
            capabilities={"tool_use": False, "reasoning": False, "streaming": True},
            capability_provenance=None,
            default_builder=default_local_capability_provenance,
        )

        assert (
            format_capability_line("Tool use", readiness.capabilities["tool_use"])
            == "Tool use: no [verified] - Local runtimes do not emit structured tool calls yet."
        )

    def test_selection_state_label(self):
        assert selection_state_label("api_discovered") == "api discovered"
        assert selection_state_label("legacy_fallback") == "legacy fallback"
        assert selection_state_label("model_unselected") == "model unselected"

    def test_readiness_tradeoff_formatting(self):
        readiness = build_target_readiness(
            kind="provider",
            selection_state="legacy_fallback",
            capabilities={"tool_use": True, "reasoning": False, "streaming": True},
            capability_provenance={
                "tool_use": {"tier": "legacy_fallback", "reason": "Fallback only."},
                "reasoning": {"tier": "legacy_fallback", "reason": "Fallback only."},
                "streaming": {"tier": "legacy_fallback", "reason": "Fallback only."},
            },
        )

        assert readiness_posture_label(readiness) == "degraded"
        assert "degraded -" in format_readiness_tradeoff(readiness)
