"""Tests for RemoteProvider ABC, ConnectionTestResult, and RemoteModelInfo."""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest

from localagentcli.models.backends.base import (
    GenerationResult,
    ModelMessage,
    StreamChunk,
)
from localagentcli.providers.base import (
    ConnectionTestResult,
    RemoteModelInfo,
    RemoteProvider,
)

# ---------------------------------------------------------------------------
# Concrete subclass for testing
# ---------------------------------------------------------------------------


class StubRemoteProvider(RemoteProvider):
    """Minimal concrete implementation for testing the ABC."""

    def generate(self, messages: list[ModelMessage], **kwargs: object) -> GenerationResult:
        return GenerationResult(text="stub response")

    def stream_generate(
        self, messages: list[ModelMessage], **kwargs: object
    ) -> Iterator[StreamChunk]:
        yield StreamChunk(text="chunk")
        yield StreamChunk(is_done=True)

    def supports_tools(self) -> bool:
        return True

    def supports_reasoning(self) -> bool:
        return False

    def supports_streaming(self) -> bool:
        return True

    def capabilities(self) -> dict:
        return {"tool_use": True, "reasoning": False, "streaming": True}

    def test_connection(self) -> ConnectionTestResult:
        return ConnectionTestResult(success=True, message="OK")

    def list_models(self) -> list[RemoteModelInfo]:
        return [RemoteModelInfo(id="test-model", name="Test Model")]


# ---------------------------------------------------------------------------
# ConnectionTestResult tests
# ---------------------------------------------------------------------------


class TestConnectionTestResult:
    def test_success(self):
        r = ConnectionTestResult(success=True, message="Connected")
        assert r.success is True
        assert r.message == "Connected"
        assert r.latency_ms == 0.0

    def test_failure_with_latency(self):
        r = ConnectionTestResult(success=False, message="Timeout", latency_ms=1500.5)
        assert r.success is False
        assert r.latency_ms == 1500.5


# ---------------------------------------------------------------------------
# RemoteModelInfo tests
# ---------------------------------------------------------------------------


class TestRemoteModelInfo:
    def test_basic(self):
        m = RemoteModelInfo(id="gpt-4o", name="GPT-4o")
        assert m.id == "gpt-4o"
        assert m.name == "GPT-4o"
        assert m.capabilities == {}

    def test_with_capabilities(self):
        caps = {"tool_use": True, "reasoning": False, "streaming": True}
        m = RemoteModelInfo(id="gpt-4o", name="GPT-4o", capabilities=caps)
        assert m.capabilities == caps

    def test_capabilities_independent(self):
        m1 = RemoteModelInfo(id="a", name="a")
        m2 = RemoteModelInfo(id="b", name="b")
        m1.capabilities["x"] = True
        assert "x" not in m2.capabilities


# ---------------------------------------------------------------------------
# RemoteProvider ABC tests
# ---------------------------------------------------------------------------


class TestRemoteProviderABC:
    def test_cannot_instantiate_abc(self):
        with pytest.raises(TypeError):
            RemoteProvider(  # type: ignore[abstract]
                name="test", base_url="http://localhost", api_key="key", default_model="m"
            )

    def test_concrete_instantiates(self):
        p = StubRemoteProvider(
            name="test", base_url="http://localhost:8000/", api_key="sk-key", default_model="m1"
        )
        assert isinstance(p, RemoteProvider)

    def test_name_property(self):
        p = StubRemoteProvider(
            name="my-provider", base_url="http://x", api_key="k", default_model="m"
        )
        assert p.name == "my-provider"

    def test_base_url_strips_trailing_slash(self):
        p = StubRemoteProvider(
            name="test", base_url="http://api.example.com/v1/", api_key="k", default_model="m"
        )
        assert p.base_url == "http://api.example.com/v1"

    def test_default_model_property(self):
        p = StubRemoteProvider(
            name="test", base_url="http://x", api_key="k", default_model="gpt-4o"
        )
        assert p.default_model == "gpt-4o"

    def test_load_is_noop(self):
        p = StubRemoteProvider(name="test", base_url="http://x", api_key="k", default_model="m")
        p.load(Path("/tmp/model"))  # should not raise

    def test_unload_is_noop(self):
        p = StubRemoteProvider(name="test", base_url="http://x", api_key="k", default_model="m")
        p.unload()  # should not raise

    def test_memory_usage_is_zero(self):
        p = StubRemoteProvider(name="test", base_url="http://x", api_key="k", default_model="m")
        assert p.memory_usage() == 0

    def test_options_default_empty(self):
        p = StubRemoteProvider(name="test", base_url="http://x", api_key="k", default_model="m")
        assert p._options == {}

    def test_options_stored(self):
        opts = {"timeout": 60, "custom": True}
        p = StubRemoteProvider(
            name="test", base_url="http://x", api_key="k", default_model="m", options=opts
        )
        assert p._options == opts

    def test_generate_delegates(self):
        p = StubRemoteProvider(name="test", base_url="http://x", api_key="k", default_model="m")
        result = p.generate([ModelMessage(role="user", content="hi")])
        assert result.text == "stub response"

    def test_stream_generate_delegates(self):
        p = StubRemoteProvider(name="test", base_url="http://x", api_key="k", default_model="m")
        chunks = list(p.stream_generate([ModelMessage(role="user", content="hi")]))
        assert len(chunks) == 2
        assert chunks[0].text == "chunk"
        assert chunks[1].is_done is True

    def test_test_connection(self):
        p = StubRemoteProvider(name="test", base_url="http://x", api_key="k", default_model="m")
        result = p.test_connection()
        assert result.success is True

    def test_list_models(self):
        p = StubRemoteProvider(name="test", base_url="http://x", api_key="k", default_model="m")
        models = p.list_models()
        assert len(models) == 1
        assert models[0].id == "test-model"
