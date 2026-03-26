"""Centralized feature flags and metadata."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List


class FeatureStage(Enum):
    """High-level lifecycle stage for a feature."""

    UNDER_DEVELOPMENT = "under_development"
    """Features that are still under development, not ready for external use."""

    EXPERIMENTAL = "experimental"
    """Experimental features made available to users or testing."""

    STABLE = "stable"
    """Stable features. The feature flag is kept for ad-hoc enabling/disabling."""

    DEPRECATED = "deprecated"
    """Deprecated feature that should not be used anymore."""

    REMOVED = "removed"
    """The feature flag is useless but kept for backward compatibility reason."""


class Feature(str, Enum):
    """Unique features toggled via configuration."""

    # We can add actual product features here as they are developed.
    # Currently, this serves as the registry boundary.
    DUMMY_STABLE = "dummy_stable"
    DUMMY_EXPERIMENTAL = "dummy_experimental"

    # Example feature for future roadmap work
    TOOL_MCP_OAUTH = "tool_mcp_oauth"
    MCP_TOOL_INVENTORY_REFRESH = "mcp_tool_inventory_refresh"


@dataclass(frozen=True)
class FeatureSpec:
    """Read-only definition for a feature."""

    id: Feature
    stage: FeatureStage
    default_enabled: bool
    description: str


# The single source of truth for all known features.
FEATURES: tuple[FeatureSpec, ...] = (
    FeatureSpec(
        id=Feature.DUMMY_STABLE,
        stage=FeatureStage.STABLE,
        default_enabled=True,
        description="A stable feature used for tests.",
    ),
    FeatureSpec(
        id=Feature.DUMMY_EXPERIMENTAL,
        stage=FeatureStage.EXPERIMENTAL,
        default_enabled=False,
        description="An experimental feature used for tests.",
    ),
    FeatureSpec(
        id=Feature.TOOL_MCP_OAUTH,
        stage=FeatureStage.UNDER_DEVELOPMENT,
        default_enabled=False,
        description="Support for MCP OAuth workflows.",
    ),
    FeatureSpec(
        id=Feature.MCP_TOOL_INVENTORY_REFRESH,
        stage=FeatureStage.UNDER_DEVELOPMENT,
        default_enabled=False,
        description="Refresh MCP-backed tool inventory between agent turns.",
    ),
)


class FeatureRegistry:
    """Manages the effective feature set based on configuration values."""

    def __init__(
        self,
        config_toggles: Dict[str, bool],
        custom_specs: tuple[FeatureSpec, ...] | None = None,
    ) -> None:
        """Initialize the feature registry.

        Args:
            config_toggles: The `[features]` table from the user configuration.
            custom_specs: Optional override for known features, used primarily for tests.
        """
        self._specs: Dict[str, FeatureSpec] = {}
        self._enabled: set[str] = set()

        specs = custom_specs if custom_specs is not None else FEATURES

        # Pre-compute defaults
        for spec in specs:
            self._specs[spec.id.value] = spec
            if spec.default_enabled:
                self._enabled.add(spec.id.value)

        # Apply configuration toggles
        for key, is_enabled in config_toggles.items():
            if key not in self._specs:
                # We could log a warning here if a logger is available
                continue

            spec = self._specs[key]
            if spec.stage == FeatureStage.REMOVED:
                # Removed features cannot be turned on, but maybe silence config warnings
                self._enabled.discard(key)
                continue

            if is_enabled:
                self._enabled.add(key)
            else:
                self._enabled.discard(key)

    def is_enabled(self, feature: Feature | str) -> bool:
        """Check if a feature is enabled.

        Args:
            feature: The Feature enum or string key.

        Returns:
            True if enabled, False otherwise.
        """
        key = feature.value if isinstance(feature, Feature) else feature
        return key in self._enabled

    def get_enabled_features(self) -> List[str]:
        """Return a sorted list of all enabled feature keys."""
        return sorted(list(self._enabled))
