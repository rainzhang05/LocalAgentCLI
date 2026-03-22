"""Async OpenAI provider paths (agenerate / error handling)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from localagentcli.models.backends.base import ModelMessage
from localagentcli.providers.openai import OpenAIProvider


def _make_provider(**kwargs: object) -> OpenAIProvider:
    defaults: dict = {
        "name": "openai",
        "base_url": "https://api.openai.com/v1",
        "api_key": "sk",
        "default_model": "gpt-4o",
    }
    defaults.update(kwargs)
    return OpenAIProvider(**defaults)


@pytest.mark.asyncio
async def test_agenerate_success():
    provider = _make_provider()
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": "Hello"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1},
    }

    with patch.object(
        OpenAIProvider,
        "_ensure_async_client",
        AsyncMock(return_value=MagicMock()),
    ):
        with patch.object(provider, "_arequest_with_retries", AsyncMock(return_value=mock_resp)):
            result = await provider.agenerate([ModelMessage(role="user", content="Hi")])

    assert result.text == "Hello"
    assert result.finish_reason == "stop"


@pytest.mark.asyncio
async def test_agenerate_http_error():
    provider = _make_provider()
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 401
    mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "401", request=MagicMock(), response=mock_resp
    )

    with patch.object(
        OpenAIProvider,
        "_ensure_async_client",
        AsyncMock(return_value=MagicMock()),
    ):
        with patch.object(provider, "_arequest_with_retries", AsyncMock(return_value=mock_resp)):
            result = await provider.agenerate([ModelMessage(role="user", content="Hi")])

    assert result.finish_reason == "error"


@pytest.mark.asyncio
async def test_agenerate_timeout():
    provider = _make_provider()

    with patch.object(
        OpenAIProvider,
        "_ensure_async_client",
        AsyncMock(return_value=MagicMock()),
    ):
        with patch.object(
            provider, "_arequest_with_retries", AsyncMock(side_effect=httpx.TimeoutException("t"))
        ):
            result = await provider.agenerate([ModelMessage(role="user", content="Hi")])

    assert result.finish_reason == "error"


@pytest.mark.asyncio
async def test_alist_models_uses_async_client_json():
    provider = _make_provider()
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"data": [{"id": "gpt-4o", "object": "model"}]}

    with patch.object(
        OpenAIProvider,
        "_ensure_async_client",
        AsyncMock(return_value=MagicMock()),
    ):
        with patch.object(provider, "_arequest_with_retries", AsyncMock(return_value=mock_resp)):
            models = await provider.alist_models()

    assert models
    assert models[0].id == "gpt-4o"


@pytest.mark.asyncio
async def test_astream_generate_http_error_yields_error_chunks():
    provider = _make_provider()
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 500
    mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "500", request=MagicMock(), response=mock_resp
    )
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_resp)
    ctx.__aexit__ = AsyncMock(return_value=None)

    with patch.object(
        OpenAIProvider,
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
    assert chunks[-1].is_done


@pytest.mark.asyncio
async def test_astream_generate_idle_timeout_yields_error_chunks():
    provider = _make_provider()
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.raise_for_status = MagicMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_resp)
    ctx.__aexit__ = AsyncMock(return_value=None)

    with patch.object(
        OpenAIProvider,
        "_ensure_async_client",
        AsyncMock(return_value=MagicMock()),
    ):
        with patch.object(
            provider, "_aopen_stream_with_retries", AsyncMock(return_value=(ctx, mock_resp))
        ):
            with patch.object(
                provider,
                "_aiter_lines_with_idle_timeout",
                side_effect=TimeoutError("idle timeout"),
            ):
                chunks = [
                    c
                    async for c in provider.astream_generate(
                        [ModelMessage(role="user", content="x")]
                    )
                ]

    assert any(c.kind == "error" for c in chunks)
    assert chunks[-1].is_done


@pytest.mark.asyncio
async def test_atest_connection_ok():
    provider = _make_provider()
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"data": []}

    with patch.object(
        OpenAIProvider,
        "_ensure_async_client",
        AsyncMock(return_value=MagicMock()),
    ):
        with patch.object(provider, "_arequest_with_retries", AsyncMock(return_value=mock_resp)):
            r = await provider.atest_connection()

    assert r.success is True
