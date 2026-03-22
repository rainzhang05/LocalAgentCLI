"""Async GenericREST provider smoke tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from localagentcli.models.backends.base import ModelMessage
from localagentcli.providers.rest import GenericRESTProvider


def _make_provider(**kwargs: object) -> GenericRESTProvider:
    defaults: dict = {
        "name": "custom",
        "base_url": "http://localhost:8000",
        "api_key": "k",
        "default_model": "m",
    }
    defaults.update(kwargs)
    return GenericRESTProvider(**defaults)


@pytest.mark.asyncio
async def test_agenerate_success():
    provider = _make_provider()
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"choices": [{"message": {"content": "OK"}}]}
    with patch.object(
        GenericRESTProvider,
        "_ensure_async_client",
        AsyncMock(return_value=MagicMock()),
    ):
        with patch.object(provider, "_arequest_with_retries", AsyncMock(return_value=mock_resp)):
            r = await provider.agenerate([ModelMessage(role="user", content="x")])
    assert r.text == "OK"


@pytest.mark.asyncio
async def test_agenerate_timeout():
    provider = _make_provider()
    with patch.object(
        GenericRESTProvider,
        "_ensure_async_client",
        AsyncMock(return_value=MagicMock()),
    ):
        with patch.object(
            provider, "_arequest_with_retries", AsyncMock(side_effect=httpx.ConnectError("x"))
        ):
            r = await provider.agenerate([ModelMessage(role="user", content="x")])
    assert r.finish_reason == "error"


@pytest.mark.asyncio
async def test_alist_models_fallback_default():
    provider = _make_provider()
    with patch.object(
        GenericRESTProvider,
        "_ensure_async_client",
        AsyncMock(return_value=MagicMock()),
    ):
        with patch.object(
            provider,
            "_arequest_with_retries",
            AsyncMock(side_effect=RuntimeError("fail")),
        ):
            models = await provider.alist_models()
    assert models
    assert models[0].id == "m"


@pytest.mark.asyncio
async def test_astream_timeout_chunks():
    provider = _make_provider()
    with patch.object(
        GenericRESTProvider,
        "_ensure_async_client",
        AsyncMock(return_value=MagicMock()),
    ):
        with patch.object(
            provider,
            "_aopen_stream_with_retries",
            AsyncMock(side_effect=httpx.TimeoutException("t")),
        ):
            chunks = [
                c async for c in provider.astream_generate([ModelMessage(role="user", content="x")])
            ]
    assert any(c.kind == "error" for c in chunks)


@pytest.mark.asyncio
async def test_atest_connection_failure():
    provider = _make_provider()
    with patch.object(
        GenericRESTProvider,
        "_ensure_async_client",
        AsyncMock(return_value=MagicMock()),
    ):
        with patch.object(
            provider, "_arequest_with_retries", AsyncMock(side_effect=httpx.TimeoutException("t"))
        ):
            r = await provider.atest_connection()
    assert r.success is False
