"""Async Anthropic provider smoke tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from localagentcli.models.backends.base import ModelMessage
from localagentcli.providers.anthropic import AnthropicProvider


def _make_provider(**kwargs: object) -> AnthropicProvider:
    defaults: dict = {
        "name": "anthropic",
        "base_url": "https://api.anthropic.com",
        "api_key": "sk-ant",
        "default_model": "claude-3-5-haiku-latest",
    }
    defaults.update(kwargs)
    return AnthropicProvider(**defaults)


@pytest.mark.asyncio
async def test_agenerate_success():
    provider = _make_provider()
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "content": [{"type": "text", "text": "Hi"}],
        "usage": {},
        "stop_reason": "end_turn",
    }
    with patch.object(
        AnthropicProvider,
        "_ensure_async_client",
        AsyncMock(return_value=MagicMock()),
    ):
        with patch.object(provider, "_arequest_with_retries", AsyncMock(return_value=mock_resp)):
            r = await provider.agenerate([ModelMessage(role="user", content="x")])
    assert r.text == "Hi"


@pytest.mark.asyncio
async def test_agenerate_http_error():
    provider = _make_provider()
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 403
    mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "403", request=MagicMock(), response=mock_resp
    )
    with patch.object(
        AnthropicProvider,
        "_ensure_async_client",
        AsyncMock(return_value=MagicMock()),
    ):
        with patch.object(provider, "_arequest_with_retries", AsyncMock(return_value=mock_resp)):
            r = await provider.agenerate([ModelMessage(role="user", content="x")])
    assert r.finish_reason == "error"


@pytest.mark.asyncio
async def test_alist_models_ok():
    provider = _make_provider()
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"data": [{"id": "claude-3-opus"}]}
    with patch.object(
        AnthropicProvider,
        "_ensure_async_client",
        AsyncMock(return_value=MagicMock()),
    ):
        with patch.object(provider, "_arequest_with_retries", AsyncMock(return_value=mock_resp)):
            models = await provider.alist_models()
    assert models
    assert models[0].id == "claude-3-opus"


@pytest.mark.asyncio
async def test_astream_http_error_chunks():
    provider = _make_provider()
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 502
    mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "502", request=MagicMock(), response=mock_resp
    )
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_resp)
    ctx.__aexit__ = AsyncMock(return_value=None)
    with patch.object(
        AnthropicProvider,
        "_ensure_async_client",
        AsyncMock(return_value=MagicMock()),
    ):
        with patch.object(
            provider, "_aopen_stream_with_retries", AsyncMock(return_value=(ctx, mock_resp))
        ):
            chunks = [
                c async for c in provider.astream_generate([ModelMessage(role="user", content="x")])
            ]
    assert any(c.kind == "error" for c in chunks)


@pytest.mark.asyncio
async def test_atest_connection_ok():
    provider = _make_provider()
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"data": {"models": []}}
    with patch.object(
        AnthropicProvider,
        "_ensure_async_client",
        AsyncMock(return_value=MagicMock()),
    ):
        with patch.object(provider, "_arequest_with_retries", AsyncMock(return_value=mock_resp)):
            r = await provider.atest_connection()
    assert r.success is True
