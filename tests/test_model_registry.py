"""Tests for ModelRegistry — CRUD operations, versioning, and search."""

from __future__ import annotations

from pathlib import Path

import pytest

from localagentcli.models.registry import ModelEntry, ModelRegistry, _version_number


@pytest.fixture
def registry_path(tmp_path: Path) -> Path:
    return tmp_path / "registry.json"


@pytest.fixture
def registry(registry_path: Path) -> ModelRegistry:
    return ModelRegistry(registry_path)


def _make_entry(
    name: str = "codellama-7b",
    version: str = "v1",
    fmt: str = "gguf",
    path: str = "/models/codellama-7b/v1",
    size_bytes: int = 4_000_000_000,
) -> ModelEntry:
    return ModelEntry(
        name=name,
        version=version,
        format=fmt,
        path=path,
        size_bytes=size_bytes,
        metadata={"source": "huggingface", "repo": "TheBloke/CodeLlama-7B-GGUF"},
    )


# ---------------------------------------------------------------------------
# ModelEntry tests
# ---------------------------------------------------------------------------


class TestModelEntry:
    def test_defaults(self):
        e = ModelEntry(name="test", version="v1", format="gguf", path="/p")
        assert e.size_bytes == 0
        assert e.capabilities["streaming"] is True
        assert e.capabilities["tool_use"] is False
        assert e.metadata == {}
        assert e.capability_provenance == {}

    def test_to_dict(self):
        e = _make_entry()
        e.capability_provenance = {
            "tool_use": {"tier": "verified", "reason": "Known false."},
            "reasoning": {"tier": "unknown", "reason": "Not verified."},
            "streaming": {"tier": "verified", "reason": "Known true."},
        }
        d = e.to_dict()
        assert d["name"] == "codellama-7b"
        assert d["version"] == "v1"
        assert d["format"] == "gguf"
        assert d["size_bytes"] == 4_000_000_000
        assert d["metadata"]["source"] == "huggingface"
        assert d["capability_provenance"]["tool_use"]["tier"] == "verified"

    def test_from_dict(self):
        d = {
            "name": "mistral-7b",
            "version": "v2",
            "format": "mlx",
            "path": "/models/mistral/v2",
            "size_bytes": 5_000_000_000,
            "capabilities": {"tool_use": True, "reasoning": False, "streaming": True},
            "metadata": {"source": "url"},
        }
        e = ModelEntry.from_dict(d)
        assert e.name == "mistral-7b"
        assert e.version == "v2"
        assert e.format == "mlx"
        assert e.capabilities["tool_use"] is True
        assert e.capability_provenance["tool_use"]["tier"] == "unknown"
        assert e.capability_provenance["reasoning"]["tier"] == "unknown"
        assert e.capability_provenance["streaming"]["tier"] == "verified"

    def test_from_dict_defaults(self):
        e = ModelEntry.from_dict({})
        assert e.name == ""
        assert e.version == "v1"
        assert e.format == ""
        assert e.size_bytes == 0
        assert e.capability_provenance["tool_use"]["tier"] == "verified"
        assert e.capability_provenance["reasoning"]["tier"] == "unknown"
        assert e.capability_provenance["streaming"]["tier"] == "verified"

    def test_roundtrip(self):
        original = _make_entry()
        original.capability_provenance = {
            "tool_use": {"tier": "verified", "reason": "Known false."},
            "reasoning": {"tier": "inferred", "reason": "Fingerprint matched."},
            "streaming": {"tier": "verified", "reason": "Known true."},
        }
        restored = ModelEntry.from_dict(original.to_dict())
        assert restored.name == original.name
        assert restored.version == original.version
        assert restored.format == original.format
        assert restored.size_bytes == original.size_bytes
        assert restored.metadata == original.metadata
        assert restored.capability_provenance == original.capability_provenance


# ---------------------------------------------------------------------------
# _version_number helper
# ---------------------------------------------------------------------------


class TestVersionNumber:
    def test_normal(self):
        assert _version_number("v1") == 1
        assert _version_number("v10") == 10

    def test_invalid(self):
        assert _version_number("") == 0
        assert _version_number("abc") == 0

    def test_none(self):
        assert _version_number(None) == 0  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# ModelRegistry CRUD tests
# ---------------------------------------------------------------------------


class TestModelRegistryRegister:
    def test_register(self, registry: ModelRegistry):
        entry = _make_entry()
        registry.register(entry)
        models = registry.list_models()
        assert len(models) == 1
        assert models[0].name == "codellama-7b"

    def test_register_duplicate_raises(self, registry: ModelRegistry):
        entry = _make_entry()
        registry.register(entry)
        with pytest.raises(ValueError, match="already registered"):
            registry.register(entry)

    def test_register_persists(self, registry_path: Path):
        reg1 = ModelRegistry(registry_path)
        reg1.register(_make_entry())
        reg2 = ModelRegistry(registry_path)
        assert len(reg2.list_models()) == 1

    def test_register_multiple(self, registry: ModelRegistry):
        registry.register(_make_entry("model-a"))
        registry.register(_make_entry("model-b", fmt="mlx"))
        assert len(registry.list_models()) == 2


class TestModelRegistryUnregister:
    def test_unregister_by_name(self, registry: ModelRegistry):
        registry.register(_make_entry())
        registry.unregister("codellama-7b")
        assert registry.list_models() == []

    def test_unregister_by_version(self, registry: ModelRegistry):
        registry.register(_make_entry(version="v1"))
        registry.register(_make_entry(version="v2", path="/models/codellama-7b/v2"))
        registry.unregister("codellama-7b", "v1")
        remaining = registry.list_models()
        assert len(remaining) == 1
        assert remaining[0].version == "v2"

    def test_unregister_all_versions(self, registry: ModelRegistry):
        registry.register(_make_entry(version="v1"))
        registry.register(_make_entry(version="v2", path="/models/codellama-7b/v2"))
        registry.unregister("codellama-7b")
        assert registry.list_models() == []

    def test_unregister_nonexistent_name(self, registry: ModelRegistry):
        with pytest.raises(KeyError, match="not found"):
            registry.unregister("nonexistent")

    def test_unregister_nonexistent_version(self, registry: ModelRegistry):
        registry.register(_make_entry())
        with pytest.raises(KeyError, match="not found"):
            registry.unregister("codellama-7b", "v99")


class TestModelRegistryGet:
    def test_get_existing(self, registry: ModelRegistry):
        registry.register(_make_entry())
        result = registry.get_model("codellama-7b")
        assert result is not None
        assert result.name == "codellama-7b"

    def test_get_nonexistent(self, registry: ModelRegistry):
        assert registry.get_model("nonexistent") is None

    def test_get_latest_version(self, registry: ModelRegistry):
        registry.register(_make_entry(version="v1"))
        registry.register(_make_entry(version="v2", path="/models/codellama-7b/v2"))
        result = registry.get_model("codellama-7b")
        assert result is not None
        assert result.version == "v2"

    def test_get_specific_version(self, registry: ModelRegistry):
        registry.register(_make_entry(version="v1"))
        registry.register(_make_entry(version="v2", path="/models/codellama-7b/v2"))
        result = registry.get_model("codellama-7b", "v1")
        assert result is not None
        assert result.version == "v1"

    def test_get_nonexistent_version(self, registry: ModelRegistry):
        registry.register(_make_entry())
        assert registry.get_model("codellama-7b", "v99") is None


class TestModelRegistryList:
    def test_list_empty(self, registry: ModelRegistry):
        assert registry.list_models() == []

    def test_list_multiple(self, registry: ModelRegistry):
        registry.register(_make_entry("model-a"))
        registry.register(_make_entry("model-b"))
        entries = registry.list_models()
        assert len(entries) == 2
        names = {e.name for e in entries}
        assert names == {"model-a", "model-b"}


class TestModelRegistryUpdate:
    def test_update(self, registry: ModelRegistry):
        registry.register(_make_entry())
        registry.update("codellama-7b", {"size_bytes": 9999})
        model = registry.get_model("codellama-7b")
        assert model is not None
        assert model.size_bytes == 9999

    def test_update_nonexistent(self, registry: ModelRegistry):
        with pytest.raises(KeyError, match="not found"):
            registry.update("nonexistent", {"size_bytes": 0})

    def test_update_latest_version(self, registry: ModelRegistry):
        registry.register(_make_entry(version="v1"))
        registry.register(_make_entry(version="v2", path="/models/codellama-7b/v2"))
        registry.update("codellama-7b", {"size_bytes": 1234})
        v2 = registry.get_model("codellama-7b", "v2")
        v1 = registry.get_model("codellama-7b", "v1")
        assert v2 is not None
        assert v2.size_bytes == 1234
        assert v1 is not None
        assert v1.size_bytes == 4_000_000_000  # unchanged

    def test_update_version(self, registry: ModelRegistry):
        registry.register(_make_entry(version="v1"))
        registry.register(_make_entry(version="v2", path="/models/codellama-7b/v2"))

        registry.update_version("codellama-7b", "v1", {"format": "mlx"})

        v1 = registry.get_model("codellama-7b", "v1")
        v2 = registry.get_model("codellama-7b", "v2")
        assert v1 is not None
        assert v1.format == "mlx"
        assert v2 is not None
        assert v2.format == "gguf"

    def test_update_version_missing_raises_keyerror(self, registry: ModelRegistry):
        registry.register(_make_entry(version="v1"))
        with pytest.raises(KeyError, match="not found"):
            registry.update_version("codellama-7b", "v9", {"format": "mlx"})


class TestModelRegistrySearch:
    def test_search_by_name(self, registry: ModelRegistry):
        registry.register(_make_entry("codellama-7b"))
        e2 = ModelEntry(
            name="mistral-7b",
            version="v1",
            format="gguf",
            path="/models/mistral/v1",
            metadata={"source": "huggingface", "repo": "mistral"},
        )
        registry.register(e2)
        results = registry.search("codellama")
        assert len(results) == 1
        assert results[0].name == "codellama-7b"

    def test_search_by_format(self, registry: ModelRegistry):
        registry.register(_make_entry("model-a", fmt="gguf"))
        registry.register(_make_entry("model-b", fmt="mlx"))
        results = registry.search("mlx")
        assert len(results) == 1
        assert results[0].name == "model-b"

    def test_search_by_metadata(self, registry: ModelRegistry):
        registry.register(_make_entry())
        results = registry.search("TheBloke")
        assert len(results) == 1

    def test_search_case_insensitive(self, registry: ModelRegistry):
        registry.register(_make_entry("CodeLlama"))
        results = registry.search("codellama")
        assert len(results) == 1

    def test_search_no_results(self, registry: ModelRegistry):
        registry.register(_make_entry())
        assert registry.search("nonexistent") == []


class TestModelRegistryVersioning:
    def test_next_version_first(self, registry: ModelRegistry):
        assert registry.next_version("codellama-7b") == "v1"

    def test_next_version_increment(self, registry: ModelRegistry):
        registry.register(_make_entry(version="v1"))
        assert registry.next_version("codellama-7b") == "v2"

    def test_next_version_with_gap(self, registry: ModelRegistry):
        registry.register(_make_entry(version="v1"))
        registry.register(_make_entry(version="v3", path="/p/v3"))
        assert registry.next_version("codellama-7b") == "v4"

    def test_next_version_different_model(self, registry: ModelRegistry):
        registry.register(_make_entry("other-model"))
        assert registry.next_version("codellama-7b") == "v1"


class TestModelRegistryEdgeCases:
    def test_empty_file(self, registry_path: Path):
        registry_path.write_text("")
        reg = ModelRegistry(registry_path)
        assert reg.list_models() == []

    def test_corrupt_json(self, registry_path: Path):
        registry_path.write_text("{not valid json")
        reg = ModelRegistry(registry_path)
        assert reg.list_models() == []

    def test_non_list_json(self, registry_path: Path):
        registry_path.write_text('{"key": "value"}')
        reg = ModelRegistry(registry_path)
        assert reg.list_models() == []

    def test_missing_file(self, registry: ModelRegistry):
        assert registry.list_models() == []
