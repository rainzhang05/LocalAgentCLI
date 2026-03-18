"""Tests for ProviderRegistry — CRUD operations and provider instantiation."""

from __future__ import annotations

from pathlib import Path

import pytest

from localagentcli.config.manager import ConfigManager
from localagentcli.providers.keys import KeyManager
from localagentcli.providers.registry import ProviderEntry, ProviderRegistry


@pytest.fixture
def secrets_dir(tmp_path: Path) -> Path:
    d = tmp_path / "secrets"
    d.mkdir()
    return d


@pytest.fixture
def key_manager(secrets_dir: Path) -> KeyManager:
    km = KeyManager(secrets_dir)
    km._keyring_available = False
    return km


@pytest.fixture
def registry(config: ConfigManager, key_manager: KeyManager) -> ProviderRegistry:
    return ProviderRegistry(config, key_manager)


def _make_entry(
    name: str = "openai",
    ptype: str = "openai",
    base_url: str = "https://api.openai.com/v1",
    default_model: str = "gpt-4o",
) -> ProviderEntry:
    return ProviderEntry(name=name, type=ptype, base_url=base_url, default_model=default_model)


# ---------------------------------------------------------------------------
# ProviderEntry tests
# ---------------------------------------------------------------------------


class TestProviderEntry:
    def test_defaults(self):
        e = ProviderEntry(name="test", type="openai", base_url="http://x", default_model="m")
        assert e.options == {}
        assert e.status == "configured"
        assert e.added_at == ""

    def test_to_dict(self):
        e = _make_entry()
        d = e.to_dict()
        assert d["type"] == "openai"
        assert d["base_url"] == "https://api.openai.com/v1"
        assert d["default_model"] == "gpt-4o"
        assert "name" not in d  # name is the key, not in the value

    def test_from_dict(self):
        d = {
            "type": "anthropic",
            "base_url": "https://api.anthropic.com",
            "default_model": "claude-sonnet-4-20250514",
            "status": "tested",
        }
        e = ProviderEntry.from_dict("my-anthropic", d)
        assert e.name == "my-anthropic"
        assert e.type == "anthropic"
        assert e.status == "tested"

    def test_from_dict_defaults(self):
        e = ProviderEntry.from_dict("test", {})
        assert e.type == "openai"
        assert e.base_url == ""
        assert e.status == "configured"


# ---------------------------------------------------------------------------
# ProviderRegistry CRUD tests
# ---------------------------------------------------------------------------


class TestProviderRegistryAdd:
    def test_add_provider(self, registry: ProviderRegistry, key_manager: KeyManager):
        entry = _make_entry()
        registry.add(entry, "sk-test-123")
        assert key_manager.has_key("openai") is True

    def test_add_sets_timestamp(self, registry: ProviderRegistry):
        entry = _make_entry()
        registry.add(entry, "sk-key")
        assert entry.added_at != ""

    def test_add_invalid_type(self, registry: ProviderRegistry):
        entry = _make_entry(ptype="invalid")
        with pytest.raises(ValueError, match="Invalid provider type"):
            registry.add(entry, "key")

    def test_add_persists_to_config(self, registry: ProviderRegistry, config: ConfigManager):
        entry = _make_entry()
        registry.add(entry, "sk-key")
        providers = config.get("providers", {})
        assert "openai" in providers
        assert providers["openai"]["type"] == "openai"


class TestProviderRegistryRemove:
    def test_remove_provider(self, registry: ProviderRegistry, key_manager: KeyManager):
        entry = _make_entry()
        registry.add(entry, "sk-key")
        registry.remove("openai")
        assert key_manager.has_key("openai") is False

    def test_remove_nonexistent(self, registry: ProviderRegistry):
        with pytest.raises(KeyError, match="not found"):
            registry.remove("nonexistent")

    def test_remove_clears_config(self, registry: ProviderRegistry, config: ConfigManager):
        entry = _make_entry()
        registry.add(entry, "sk-key")
        registry.remove("openai")
        providers = config.get("providers", {})
        assert "openai" not in providers


class TestProviderRegistryGet:
    def test_get_existing(self, registry: ProviderRegistry):
        entry = _make_entry()
        registry.add(entry, "sk-key")
        result = registry.get("openai")
        assert result is not None
        assert result.name == "openai"
        assert result.type == "openai"

    def test_get_nonexistent(self, registry: ProviderRegistry):
        assert registry.get("nonexistent") is None


class TestProviderRegistryList:
    def test_list_empty(self, registry: ProviderRegistry):
        assert registry.list_providers() == []

    def test_list_multiple(self, registry: ProviderRegistry):
        registry.add(_make_entry("openai"), "key1")
        registry.add(
            _make_entry(
                "anthropic",
                ptype="anthropic",
                base_url="https://api.anthropic.com",
                default_model="claude-sonnet-4-20250514",
            ),
            "key2",
        )
        entries = registry.list_providers()
        assert len(entries) == 2
        names = {e.name for e in entries}
        assert names == {"openai", "anthropic"}


class TestProviderRegistryStatus:
    def test_update_status(self, registry: ProviderRegistry):
        registry.add(_make_entry(), "key")
        registry.update_status("openai", "tested")
        entry = registry.get("openai")
        assert entry is not None
        assert entry.status == "tested"

    def test_update_status_nonexistent(self, registry: ProviderRegistry):
        with pytest.raises(KeyError, match="not found"):
            registry.update_status("nonexistent", "tested")


class TestProviderRegistryActive:
    def test_get_active_name_default(self, registry: ProviderRegistry):
        assert registry.get_active_name() == ""

    def test_set_active(self, registry: ProviderRegistry, config: ConfigManager):
        registry.add(_make_entry(), "key")
        registry.set_active("openai")
        assert config.get("provider.active_provider") == "openai"

    def test_set_active_nonexistent(self, registry: ProviderRegistry):
        with pytest.raises(KeyError, match="not found"):
            registry.set_active("nonexistent")

    def test_set_active_empty_clears(self, registry: ProviderRegistry, config: ConfigManager):
        registry.add(_make_entry(), "key")
        registry.set_active("openai")
        registry.set_active("")
        assert config.get("provider.active_provider") == ""


class TestProviderRegistryCreateProvider:
    def test_create_openai_provider(self, registry: ProviderRegistry):
        registry.add(_make_entry(), "sk-test")
        provider = registry.create_provider("openai")
        assert provider.name == "openai"
        assert provider.default_model == "gpt-4o"

    def test_create_anthropic_provider(self, registry: ProviderRegistry):
        entry = _make_entry(
            "anthropic",
            ptype="anthropic",
            base_url="https://api.anthropic.com",
            default_model="claude-sonnet-4-20250514",
        )
        registry.add(entry, "sk-ant")
        provider = registry.create_provider("anthropic")
        assert provider.name == "anthropic"

    def test_create_rest_provider(self, registry: ProviderRegistry):
        entry = _make_entry(
            "custom",
            ptype="rest",
            base_url="http://localhost:8000",
            default_model="local",
        )
        registry.add(entry, "key")
        provider = registry.create_provider("custom")
        assert provider.name == "custom"

    def test_create_nonexistent(self, registry: ProviderRegistry):
        with pytest.raises(KeyError, match="not found"):
            registry.create_provider("nonexistent")

    def test_create_no_api_key(self, registry: ProviderRegistry, key_manager: KeyManager):
        registry.add(_make_entry(), "sk-key")
        key_manager.delete_key("openai")
        with pytest.raises(ValueError, match="No API key"):
            registry.create_provider("openai")
