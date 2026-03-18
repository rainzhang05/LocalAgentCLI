"""Tests for localagentcli.config.manager."""

from __future__ import annotations

import pytest

from localagentcli.config.manager import ConfigManager


class TestConfigManagerLoad:
    """Tests for config loading."""

    def test_creates_default_on_first_load(self, storage):
        cm = ConfigManager(storage.config_path)
        cm.load()
        assert storage.config_path.exists()

    def test_loads_existing_config(self, config):
        assert config.get("general.default_mode") == "agent"

    def test_merges_missing_defaults(self, storage):
        # Write a partial config
        storage.config_path.write_text('[general]\ndefault_mode = "chat"\n')
        cm = ConfigManager(storage.config_path)
        cm.load()
        assert cm.get("general.default_mode") == "chat"
        # Missing keys should get defaults
        assert cm.get("generation.temperature") == 0.7


class TestConfigManagerGet:
    """Tests for config getting."""

    def test_get_existing_key(self, config):
        assert config.get("general.default_mode") == "agent"

    def test_get_nested_key(self, config):
        assert config.get("generation.temperature") == 0.7

    def test_get_missing_key_returns_default(self, config):
        assert config.get("nonexistent.key") is None
        assert config.get("nonexistent.key", "fallback") == "fallback"

    def test_get_partial_path(self, config):
        # Getting a section returns the dict
        result = config.get("general")
        assert isinstance(result, dict)
        assert "default_mode" in result


class TestConfigManagerSet:
    """Tests for config setting."""

    def test_set_valid_value(self, config):
        config.set("general.default_mode", "chat")
        assert config.get("general.default_mode") == "chat"

    def test_set_persists_to_disk(self, config, storage):
        config.set("general.default_mode", "chat")
        # Reload from disk
        cm2 = ConfigManager(storage.config_path)
        cm2.load()
        assert cm2.get("general.default_mode") == "chat"

    def test_set_coerces_string_to_float(self, config):
        config.set("generation.temperature", "1.5")
        assert config.get("generation.temperature") == 1.5

    def test_set_coerces_string_to_int(self, config):
        config.set("generation.max_tokens", "8192")
        assert config.get("generation.max_tokens") == 8192

    def test_set_invalid_value_raises(self, config):
        with pytest.raises(ValueError, match="Invalid value"):
            config.set("general.default_mode", "invalid_mode")

    def test_set_unknown_key_raises(self, config):
        with pytest.raises(ValueError, match="Unknown config key"):
            config.set("nonexistent.key", "value")

    def test_set_out_of_range_raises(self, config):
        with pytest.raises(ValueError):
            config.set("generation.temperature", "5.0")


class TestConfigManagerGetAll:
    """Tests for full config retrieval."""

    def test_returns_dict(self, config):
        result = config.get_all()
        assert isinstance(result, dict)
        assert "general" in result

    def test_returns_deep_copy(self, config):
        result = config.get_all()
        result["general"]["default_mode"] = "changed"
        assert config.get("general.default_mode") == "agent"


class TestConfigManagerReset:
    """Tests for config reset."""

    def test_reset_restores_defaults(self, config):
        config.set("general.default_mode", "chat")
        config.reset_to_defaults()
        assert config.get("general.default_mode") == "agent"
