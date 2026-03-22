"""Tests for RemoteProvider ABC, ConnectionTestResult, and RemoteModelInfo."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
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
    effective_model_request_timeout,
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

    async def agenerate(self, messages: list[ModelMessage], **kwargs: object) -> GenerationResult:
        return GenerationResult(text="stub response")

    async def astream_generate(
        self, messages: list[ModelMessage], **kwargs: object
    ) -> AsyncIterator[StreamChunk]:
        yield StreamChunk(text="chunk")
        yield StreamChunk(is_done=True)

    async def atest_connection(self) -> ConnectionTestResult:
        return ConnectionTestResult(success=True, message="OK")

    async def alist_models(self) -> list[RemoteModelInfo]:
        return [RemoteModelInfo(id="test-model", name="Test Model")]


# ---------------------------------------------------------------------------
# Timeout precedence
# ---------------------------------------------------------------------------


class TestEffectiveModelRequestTimeout:
    def test_provider_timeout_wins(self):
        assert effective_model_request_timeout({"timeout": 12.5}, 300) == 12.5

    def test_falls_back_to_global(self):
        assert effective_model_request_timeout({}, 90) == 90.0

    def test_default_when_unset(self):
        assert effective_model_request_timeout(None, None) == 300.0


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

    def test_request_timeout_value_from_kwargs(self):
        p = StubRemoteProvider(name="test", base_url="http://x", api_key="k", default_model="m")
        assert p._request_timeout_value({"request_timeout": 42.5}) == 42.5

    def test_track_async_stream_without_running_loop(self):
        p = StubRemoteProvider(name="test", base_url="http://x", api_key="k", default_model="m")
        resp = MagicMock()
        p._track_async_stream(resp)
        assert p._async_stream_response is resp
        assert p._async_stream_loop is None

    def test_retry_delay_uses_retry_after_header(self):
        p = StubRemoteProvider(name="test", base_url="http://x", api_key="k", default_model="m")
        req = httpx.Request("GET", "http://x")
        response = httpx.Response(503, request=req, headers={"Retry-After": "0.25"})
        assert p._retry_delay(1, response) == 0.25

    def test_retry_delay_invalid_retry_after_falls_back_to_backoff(self):
        p = StubRemoteProvider(name="test", base_url="http://x", api_key="k", default_model="m")
        req = httpx.Request("GET", "http://x")
        response = httpx.Response(503, request=req, headers={"Retry-After": "not-a-number"})
        assert p._retry_delay(3, response) == pytest.approx(0.6)

    def test_request_with_retries_recovers_after_transient_status(self):
        opts = {"max_retries": 2}
        p = StubRemoteProvider(
            name="test", base_url="http://x", api_key="k", default_model="m", options=opts
        )
        req = httpx.Request("GET", "http://x")
        calls = {"n": 0}

        def factory() -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(503, request=req)
            return httpx.Response(200, request=req)

        out = p._request_with_retries(factory)
        assert out.status_code == 200
        assert calls["n"] == 2

    def test_request_with_retries_recovers_after_connect_error(self):
        opts = {"max_retries": 1}
        p = StubRemoteProvider(
            name="test", base_url="http://x", api_key="k", default_model="m", options=opts
        )
        req = httpx.Request("GET", "http://x")
        calls = {"n": 0}

        def factory() -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                raise httpx.ConnectError("boom", request=req)
            return httpx.Response(200, request=req)

        out = p._request_with_retries(factory)
        assert out.status_code == 200
        assert calls["n"] == 2

    @pytest.mark.asyncio
    async def test_arequest_with_retries_recovers_after_transient_status(self):
        opts = {"max_retries": 2}
        p = StubRemoteProvider(
            name="test", base_url="http://x", api_key="k", default_model="m", options=opts
        )
        req = httpx.Request("GET", "http://x")
        calls = {"n": 0}

        async def factory() -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(503, request=req)
            return httpx.Response(200, request=req)

        out = await p._arequest_with_retries(factory)
        assert out.status_code == 200
        assert calls["n"] == 2

    @pytest.mark.asyncio
    async def test_close_async_client_skips_sync_close_when_loop_running(self):
        p = StubRemoteProvider(name="test", base_url="http://x", api_key="k", default_model="m")
        mock_ac = MagicMock()
        mock_ac.aclose = AsyncMock()
        p._async_client = mock_ac
        p._close_async_client_sync_best_effort()
        mock_ac.aclose.assert_not_called()
