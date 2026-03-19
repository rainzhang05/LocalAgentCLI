"""Tests for GenericRESTProvider with mocked HTTP responses."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx

from localagentcli.models.backends.base import ModelMessage
from localagentcli.providers.rest import GenericRESTProvider, extract_field


def _make_provider(**kwargs: object) -> GenericRESTProvider:
    defaults: dict = {
        "name": "custom",
        "base_url": "http://localhost:8000",
        "api_key": "test-key",
        "default_model": "local-model",
    }
    defaults.update(kwargs)
    return GenericRESTProvider(**defaults)


def _mock_response(data: dict, status_code: int = 200) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = data
    resp.raise_for_status.return_value = None
    resp.text = json.dumps(data)
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            message=f"HTTP {status_code}", request=MagicMock(), response=resp
        )
    return resp


# ---------------------------------------------------------------------------
# extract_field() tests
# ---------------------------------------------------------------------------


class TestExtractField:
    def test_simple_path(self):
        data = {"message": {"content": "Hello"}}
        assert extract_field(data, "message.content") == "Hello"

    def test_array_index(self):
        data = {"choices": [{"text": "A"}, {"text": "B"}]}
        assert extract_field(data, "choices[0].text") == "A"
        assert extract_field(data, "choices[1].text") == "B"

    def test_nested_array(self):
        data = {"choices": [{"delta": {"content": "Hi"}}]}
        assert extract_field(data, "choices[0].delta.content") == "Hi"

    def test_missing_key(self):
        data = {"a": 1}
        assert extract_field(data, "b") is None

    def test_missing_nested_key(self):
        data = {"a": {"b": 1}}
        assert extract_field(data, "a.c") is None

    def test_index_out_of_range(self):
        data = {"items": [1]}
        assert extract_field(data, "items[5]") is None

    def test_key_not_dict(self):
        data = {"a": "string"}
        assert extract_field(data, "a.b") is None

    def test_single_key(self):
        data = {"text": "hello"}
        assert extract_field(data, "text") == "hello"


# ---------------------------------------------------------------------------
# generate() tests
# ---------------------------------------------------------------------------


class TestRESTGenerate:
    def test_generate_success(self):
        provider = _make_provider()
        data = {"choices": [{"message": {"content": "Response"}}]}
        with patch.object(provider._client, "post", return_value=_mock_response(data)):
            result = provider.generate([ModelMessage(role="user", content="Hi")])
        assert result.text == "Response"

    def test_generate_with_custom_mapping(self):
        provider = _make_provider(
            options={
                "response_mapping": {"content_field": "output.text"},
            }
        )
        data = {"output": {"text": "Custom response"}}
        with patch.object(provider._client, "post", return_value=_mock_response(data)):
            result = provider.generate([ModelMessage(role="user", content="Hi")])
        assert result.text == "Custom response"

    def test_generate_http_error(self):
        provider = _make_provider()
        with patch.object(provider._client, "post", return_value=_mock_response({}, 500)):
            result = provider.generate([ModelMessage(role="user", content="Hi")])
        assert result.finish_reason == "error"

    def test_generate_missing_content(self):
        provider = _make_provider()
        data = {"empty": True}
        with patch.object(provider._client, "post", return_value=_mock_response(data)):
            result = provider.generate([ModelMessage(role="user", content="Hi")])
        assert result.text == ""


# ---------------------------------------------------------------------------
# stream_generate() tests
# ---------------------------------------------------------------------------


class TestRESTStreamGenerate:
    def test_stream_basic(self):
        provider = _make_provider()
        lines = [
            'data: {"choices":[{"delta":{"content":"Hello"}}]}',
            "data: [DONE]",
        ]
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.iter_lines.return_value = iter(lines)
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch.object(provider._client, "stream", return_value=mock_resp):
            chunks = list(provider.stream_generate([ModelMessage(role="user", content="Hi")]))
        assert any(c.text == "Hello" for c in chunks)
        assert chunks[-1].is_done is True

    def test_stream_http_error(self):
        provider = _make_provider()
        mock_resp = MagicMock()
        error_resp = MagicMock()
        error_resp.status_code = 401
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            message="auth", request=MagicMock(), response=error_resp
        )
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch.object(provider._client, "stream", return_value=mock_resp):
            chunks = list(provider.stream_generate([ModelMessage(role="user", content="Hi")]))
        assert chunks[-1].is_done is True


# ---------------------------------------------------------------------------
# test_connection() tests
# ---------------------------------------------------------------------------


class TestRESTTestConnection:
    def test_success(self):
        provider = _make_provider()
        data = {"choices": [{"message": {"content": "ok"}}]}
        with patch.object(provider._client, "post", return_value=_mock_response(data)):
            result = provider.test_connection()
        assert result.success is True

    def test_auth_failure(self):
        provider = _make_provider()
        with patch.object(provider._client, "post", return_value=_mock_response({}, 401)):
            result = provider.test_connection()
        assert result.success is False
        assert "Authentication" in result.message

    def test_connection_error(self):
        provider = _make_provider()
        with patch.object(provider._client, "post", side_effect=httpx.ConnectError("refused")):
            result = provider.test_connection()
        assert result.success is False


# ---------------------------------------------------------------------------
# Capability tests
# ---------------------------------------------------------------------------


class TestRESTCapabilities:
    def test_supports_tools(self):
        assert _make_provider().supports_tools() is False

    def test_supports_reasoning(self):
        assert _make_provider().supports_reasoning() is False

    def test_supports_streaming(self):
        assert _make_provider().supports_streaming() is True

    def test_capabilities(self):
        assert _make_provider().capabilities() == {
            "tool_use": False,
            "reasoning": False,
            "streaming": True,
        }


class TestRESTListModels:
    def test_returns_api_models(self):
        provider = _make_provider()
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"data": [{"id": "alpha"}, {"id": "beta"}]}
        with patch.object(provider._client, "get", return_value=response):
            models = provider.list_models()
        assert len(models) == 2
        assert models[0].id == "alpha"

    def test_returns_default_model_when_discovery_fails(self):
        provider = _make_provider()
        with patch.object(provider._client, "get", side_effect=Exception("fail")):
            models = provider.list_models()
        assert len(models) == 1
        assert models[0].id == "local-model"

    def test_returns_default_model(self):
        provider = _make_provider()
        models = provider.list_models()
        assert len(models) == 1
        assert models[0].id == "local-model"


class TestRESTCustomEndpoint:
    def test_custom_endpoint(self):
        provider = _make_provider(options={"endpoint": "/v1/generate"})
        assert provider._endpoint == "/v1/generate"
