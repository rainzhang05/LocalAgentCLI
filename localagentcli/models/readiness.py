"""Shared target readiness and capability-confidence helpers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Mapping

CapabilityTier = Literal["verified", "inferred", "configured", "legacy_fallback", "unknown"]

_CAPABILITY_KEYS = ("tool_use", "reasoning", "streaming")
_AGENT_READY_TIERS = {"verified", "inferred", "configured"}


@dataclass(frozen=True)
class CapabilityAssessment:
    """One capability claim plus its confidence and rationale."""

    supported: bool
    tier: CapabilityTier
    reason: str


@dataclass(frozen=True)
class TargetReadiness:
    """Readiness summary for one local or remote target."""

    kind: Literal["local", "provider"]
    selection_state: str
    capabilities: dict[str, CapabilityAssessment]
    summary: str
    guidance: str = ""
    operator_posture: Literal["ready", "degraded", "blocked"] = "blocked"
    tradeoff: str = ""
    agent_recommendation: str = ""


def local_capability_provenance(*, reasoning_supported: bool) -> dict[str, dict[str, str]]:
    """Return provenance for a newly installed local model."""
    return {
        "tool_use": {
            "tier": "verified",
            "reason": "Local runtimes do not emit structured tool calls yet.",
        },
        "reasoning": {
            "tier": "inferred" if reasoning_supported else "unknown",
            "reason": (
                "Reasoning support is inferred from the model family and installation metadata."
                if reasoning_supported
                else "Reasoning output has not been verified for this local runtime."
            ),
        },
        "streaming": {
            "tier": "verified",
            "reason": "Local runtimes stream token output directly.",
        },
    }


def default_local_capability_provenance(
    capabilities: Mapping[str, object] | None,
) -> dict[str, dict[str, str]]:
    """Best-effort provenance for older registry entries without stored provenance."""
    caps = _normalize_capability_flags(capabilities)
    return {
        "tool_use": {
            "tier": "verified" if not caps["tool_use"] else "unknown",
            "reason": (
                "Local runtimes do not emit structured tool calls yet."
                if not caps["tool_use"]
                else "Tool-use support has not been verified for this local model entry."
            ),
        },
        "reasoning": {
            "tier": "inferred" if caps["reasoning"] else "unknown",
            "reason": (
                "Reasoning support is inferred from the model family and installation metadata."
                if caps["reasoning"]
                else "Reasoning output has not been verified for this local runtime."
            ),
        },
        "streaming": {
            "tier": "verified" if caps["streaming"] else "unknown",
            "reason": (
                "Local runtimes stream token output directly."
                if caps["streaming"]
                else "Streaming support has not been verified for this local runtime."
            ),
        },
    }


def inferred_remote_capability_provenance(
    capabilities: Mapping[str, object] | None,
    *,
    provider_label: str,
) -> dict[str, dict[str, str]]:
    """Return inferred provenance for API-discovered remote models."""
    return _uniform_capability_provenance(
        capabilities,
        tier="inferred",
        reason=(
            f"Capabilities are inferred from the {provider_label} model id and provider semantics."
        ),
    )


def configured_remote_capability_provenance(
    capabilities: Mapping[str, object] | None,
) -> dict[str, dict[str, str]]:
    """Return configured provenance for generic REST providers."""
    return _uniform_capability_provenance(
        capabilities,
        tier="configured",
        reason="Capabilities come from this provider's configured flags.",
    )


def legacy_fallback_capability_provenance(
    capabilities: Mapping[str, object] | None,
) -> dict[str, dict[str, str]]:
    """Return legacy-fallback provenance for provider default-model fallback."""
    return _uniform_capability_provenance(
        capabilities,
        tier="legacy_fallback",
        reason=(
            "Live model discovery was unavailable; using the stored provider default model as a "
            "legacy fallback."
        ),
    )


def unknown_capability_provenance(
    capabilities: Mapping[str, object] | None,
    *,
    reason: str,
) -> dict[str, dict[str, str]]:
    """Return unknown provenance when a target cannot be verified."""
    return _uniform_capability_provenance(
        capabilities,
        tier="unknown",
        reason=reason,
    )


def normalize_capability_provenance(
    provenance: Mapping[str, object] | None,
    capabilities: Mapping[str, object] | None,
    *,
    default_builder: Callable[[Mapping[str, object] | None], dict[str, dict[str, str]]]
    | None = None,
) -> dict[str, dict[str, str]]:
    """Normalize stored provenance and fill any missing capability entries."""
    caps = _normalize_capability_flags(capabilities)
    normalized: dict[str, dict[str, str]] = {}
    if isinstance(provenance, Mapping):
        for key in _CAPABILITY_KEYS:
            raw = provenance.get(key)
            if not isinstance(raw, Mapping):
                continue
            tier = str(raw.get("tier", "")).strip()
            if tier not in _valid_tiers():
                continue
            reason = str(raw.get("reason", "")).strip()
            normalized[key] = {
                "tier": tier,
                "reason": reason,
            }

    fallback = (
        default_builder(caps)
        if default_builder is not None
        else unknown_capability_provenance(
            caps,
            reason="Capability provenance was not recorded for this target.",
        )
    )
    for key in _CAPABILITY_KEYS:
        normalized.setdefault(key, fallback[key])
    return normalized


def build_capability_assessments(
    capabilities: Mapping[str, object] | None,
    capability_provenance: Mapping[str, object] | None,
    *,
    default_builder: Callable[[Mapping[str, object] | None], dict[str, dict[str, str]]]
    | None = None,
) -> dict[str, CapabilityAssessment]:
    """Combine booleans and provenance into full capability assessments."""
    caps = _normalize_capability_flags(capabilities)
    provenance = normalize_capability_provenance(
        capability_provenance,
        caps,
        default_builder=default_builder,
    )
    return {
        key: CapabilityAssessment(
            supported=caps[key],
            tier=provenance[key]["tier"],  # type: ignore[arg-type]
            reason=provenance[key]["reason"],
        )
        for key in _CAPABILITY_KEYS
    }


def build_target_readiness(
    *,
    kind: Literal["local", "provider"],
    selection_state: str,
    capabilities: Mapping[str, object] | None,
    capability_provenance: Mapping[str, object] | None,
    default_builder: Callable[[Mapping[str, object] | None], dict[str, dict[str, str]]]
    | None = None,
    summary: str | None = None,
    guidance: str = "",
) -> TargetReadiness:
    """Build a target readiness object from booleans plus provenance."""
    assessments = build_capability_assessments(
        capabilities,
        capability_provenance,
        default_builder=default_builder,
    )
    tool_use = assessments["tool_use"]
    posture, tradeoff, recommendation = _derive_operator_readiness(
        kind=kind,
        selection_state=selection_state,
        assessments=assessments,
    )
    computed_summary = summary or (
        f"Agent mode {'available' if is_agent_ready(assessments) else 'unavailable'}: "
        f"tool use {yes_no(tool_use.supported)} [{tool_use.tier}]."
    )
    return TargetReadiness(
        kind=kind,
        selection_state=selection_state,
        capabilities=assessments,
        summary=computed_summary,
        guidance=guidance,
        operator_posture=posture,
        tradeoff=tradeoff,
        agent_recommendation=recommendation,
    )


def is_agent_ready(
    readiness: TargetReadiness | Mapping[str, CapabilityAssessment],
) -> bool:
    """Return whether a target is trusted enough for agent mode tool use."""
    assessments = readiness.capabilities if isinstance(readiness, TargetReadiness) else readiness
    tool_use = assessments["tool_use"]
    return tool_use.supported and tool_use.tier in _AGENT_READY_TIERS


def format_capability_line(label: str, assessment: CapabilityAssessment) -> str:
    """Render one detailed capability line for inspection output."""
    return f"{label}: {yes_no(assessment.supported)} [{assessment.tier}] - {assessment.reason}"


def format_capability_brief(label: str, assessment: CapabilityAssessment) -> str:
    """Render one compact capability badge for menus and tables."""
    return f"{label}: {yes_no(assessment.supported)} [{assessment.tier}]"


def selection_state_label(selection_state: str) -> str:
    """Render a human-readable label for selection state."""
    if selection_state == "api_discovered":
        return "api discovered"
    if selection_state == "legacy_fallback":
        return "legacy fallback"
    if selection_state == "model_unselected":
        return "model unselected"
    return selection_state.replace("_", " ")


def readiness_posture_label(readiness: TargetReadiness) -> str:
    """Render a human-readable readiness posture."""
    return readiness.operator_posture.replace("_", " ")


def format_readiness_tradeoff(readiness: TargetReadiness) -> str:
    """Render one concise tradeoff line for operator-facing surfaces."""
    if readiness.tradeoff:
        return f"{readiness_posture_label(readiness)} - {readiness.tradeoff}"
    return readiness_posture_label(readiness)


def yes_no(value: bool) -> str:
    """Render booleans consistently in user-facing readiness text."""
    return "yes" if value else "no"


def _normalize_capability_flags(capabilities: Mapping[str, object] | None) -> dict[str, bool]:
    caps = dict(capabilities or {})
    return {
        "tool_use": bool(caps.get("tool_use", False)),
        "reasoning": bool(caps.get("reasoning", False)),
        "streaming": bool(caps.get("streaming", True)),
    }


def _uniform_capability_provenance(
    capabilities: Mapping[str, object] | None,
    *,
    tier: CapabilityTier,
    reason: str,
) -> dict[str, dict[str, str]]:
    caps = _normalize_capability_flags(capabilities)
    return {
        key: {
            "tier": tier,
            "reason": reason,
        }
        for key in caps
    }


def _valid_tiers() -> set[str]:
    return {"verified", "inferred", "configured", "legacy_fallback", "unknown"}


def _derive_operator_readiness(
    *,
    kind: Literal["local", "provider"],
    selection_state: str,
    assessments: Mapping[str, CapabilityAssessment],
) -> tuple[Literal["ready", "degraded", "blocked"], str, str]:
    tool_use = assessments["tool_use"]
    if is_agent_ready(assessments):
        return (
            "ready",
            "Agent mode can run tool steps with trusted tool-use readiness.",
            "Agent mode is available for this target.",
        )

    if selection_state in {"legacy_fallback", "unknown", "model_unselected"}:
        return (
            "degraded",
            "Chat mode remains available, but agent mode is blocked until model discovery and "
            "selection are refreshed.",
            "Refresh discovery and choose an API-discovered model before running agent tasks.",
        )

    if kind == "local":
        return (
            "blocked",
            "Local target remains usable for chat, but this model cannot run agent tool steps.",
            "Switch to a tool-capable local or provider target.",
        )

    return (
        "blocked",
        "Provider target remains usable for chat, but tool-use readiness is not trusted for "
        "agent mode.",
        f"Pick a provider model with trusted tool use (currently {tool_use.tier}).",
    )
