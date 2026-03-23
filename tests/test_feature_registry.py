"""Tests for localagentcli.features.registry."""

from __future__ import annotations

from localagentcli.features.registry import (
    Feature,
    FeatureRegistry,
    FeatureSpec,
    FeatureStage,
)


def test_registry_uses_defaults():
    specs = (
        FeatureSpec(
            id=Feature.DUMMY_STABLE,
            stage=FeatureStage.STABLE,
            default_enabled=True,
            description="Stable description",
        ),
        FeatureSpec(
            id=Feature.DUMMY_EXPERIMENTAL,
            stage=FeatureStage.EXPERIMENTAL,
            default_enabled=False,
            description="Experimental description",
        ),
    )
    registry = FeatureRegistry({}, custom_specs=specs)

    assert registry.is_enabled(Feature.DUMMY_STABLE) is True
    assert registry.is_enabled("dummy_stable") is True
    assert registry.is_enabled(Feature.DUMMY_EXPERIMENTAL) is False
    assert registry.is_enabled("dummy_experimental") is False


def test_registry_applies_config_overrides():
    specs = (
        FeatureSpec(
            id=Feature.DUMMY_STABLE,
            stage=FeatureStage.STABLE,
            default_enabled=True,
            description="",
        ),
        FeatureSpec(
            id=Feature.DUMMY_EXPERIMENTAL,
            stage=FeatureStage.EXPERIMENTAL,
            default_enabled=False,
            description="",
        ),
    )
    toggles = {
        "dummy_stable": False,
        "dummy_experimental": True,
        "unknown_feature": True,
    }
    registry = FeatureRegistry(toggles, custom_specs=specs)

    assert registry.is_enabled(Feature.DUMMY_STABLE) is False
    assert registry.is_enabled(Feature.DUMMY_EXPERIMENTAL) is True

    # Unknown features are ignored smoothly
    assert registry.is_enabled("unknown_feature") is False


def test_registry_ignores_removed_features():
    specs = (
        FeatureSpec(
            # Using enum string directly since we can't easily add to Enum dynamically
            id=Feature.DUMMY_STABLE,
            stage=FeatureStage.REMOVED,
            default_enabled=True,
            description="",
        ),
    )
    # Even if default is true or config says true, a REMOVED feature is not enabled.
    registry = FeatureRegistry({"dummy_stable": True}, custom_specs=specs)
    assert registry.is_enabled(Feature.DUMMY_STABLE) is False


def test_get_enabled_features():
    specs = (
        FeatureSpec(
            id=Feature.DUMMY_STABLE,
            stage=FeatureStage.STABLE,
            default_enabled=True,
            description="",
        ),
        FeatureSpec(
            id=Feature.DUMMY_EXPERIMENTAL,
            stage=FeatureStage.EXPERIMENTAL,
            default_enabled=False,
            description="",
        ),
    )
    registry = FeatureRegistry({"dummy_experimental": True}, custom_specs=specs)
    enabled = registry.get_enabled_features()

    assert len(enabled) == 2
    assert "dummy_stable" in enabled
    assert "dummy_experimental" in enabled
