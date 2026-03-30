"""Provider-aware prompt assembly profile types."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderPromptProfile:
    """Prompt-shaping preferences for an active model/provider target."""

    provider_kind: str = "generic"
    structured_system_blocks: bool = False
    stable_system_cache_control_type: str | None = None


DEFAULT_PROMPT_PROFILE = ProviderPromptProfile()
