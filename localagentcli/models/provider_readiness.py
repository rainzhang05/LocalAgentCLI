"""Provider model discovery helpers shared by commands and runtime."""

from __future__ import annotations

from localagentcli.models.readiness import (
    TargetReadiness,
    build_target_readiness,
    unknown_capability_provenance,
)
from localagentcli.providers.base import RemoteProvider


def resolve_remote_model_readiness(
    provider: RemoteProvider,
    model_name: str,
) -> TargetReadiness:
    """Resolve readiness for one selected provider model (sync)."""
    selected = (model_name or "").strip()
    provider.set_active_model(selected or None)
    if not selected:
        capabilities = provider.capabilities()
        return build_target_readiness(
            kind="provider",
            selection_state="model_unselected",
            capabilities=capabilities,
            capability_provenance=unknown_capability_provenance(
                capabilities,
                reason="No provider model is selected.",
            ),
            summary="No provider model selected.",
            guidance="Use /set or /set default to choose one.",
        )

    for model in provider.list_models():
        if model.id == selected or model.name == selected:
            guidance = (
                "Run /providers test to refresh discovery, then use /set to choose an "
                "API-discovered model."
                if model.selection_state == "legacy_fallback"
                else "Use /set to choose another model if this target does not fit the task."
            )
            return build_target_readiness(
                kind="provider",
                selection_state=model.selection_state,
                capabilities=model.capabilities,
                capability_provenance=model.capability_provenance,
                guidance=guidance,
            )

    capabilities = provider.capabilities()
    return build_target_readiness(
        kind="provider",
        selection_state="unknown",
        capabilities=capabilities,
        capability_provenance=unknown_capability_provenance(
            capabilities,
            reason=f"The selected provider model '{selected}' was not returned by live discovery.",
        ),
        summary=f"Model '{selected}' was not returned by provider discovery.",
        guidance=(
            "Run /providers test to refresh discovery, then use /set to choose an "
            "API-discovered model."
        ),
    )


async def aresolve_remote_model_readiness(
    provider: RemoteProvider,
    model_name: str,
) -> TargetReadiness:
    """Resolve readiness for one selected provider model (async discovery)."""
    selected = (model_name or "").strip()
    provider.set_active_model(selected or None)
    if not selected:
        capabilities = provider.capabilities()
        return build_target_readiness(
            kind="provider",
            selection_state="model_unselected",
            capabilities=capabilities,
            capability_provenance=unknown_capability_provenance(
                capabilities,
                reason="No provider model is selected.",
            ),
            summary="No provider model selected.",
            guidance="Use /set or /set default to choose one.",
        )

    models = await provider.alist_models()
    for model in models:
        if model.id == selected or model.name == selected:
            guidance = (
                "Run /providers test to refresh discovery, then use /set to choose an "
                "API-discovered model."
                if model.selection_state == "legacy_fallback"
                else "Use /set to choose another model if this target does not fit the task."
            )
            return build_target_readiness(
                kind="provider",
                selection_state=model.selection_state,
                capabilities=model.capabilities,
                capability_provenance=model.capability_provenance,
                guidance=guidance,
            )

    capabilities = provider.capabilities()
    return build_target_readiness(
        kind="provider",
        selection_state="unknown",
        capabilities=capabilities,
        capability_provenance=unknown_capability_provenance(
            capabilities,
            reason=f"The selected provider model '{selected}' was not returned by live discovery.",
        ),
        summary=f"Model '{selected}' was not returned by provider discovery.",
        guidance=(
            "Run /providers test to refresh discovery, then use /set to choose an "
            "API-discovered model."
        ),
    )
