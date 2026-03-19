"""Tests for OpenAIProvider with mocked HTTP responses."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx

from localagentcli.models.backends.base import ModelMessage
from localagentcli.providers.openai import OpenAIProvider


def _make_provider(**kwargs: object) -> OpenAIProvider:
    defaults: dict = {
        "name": "openai",
        "base_url": "https://api.openai.com/v1",
        "api_key": "sk-test",
        "default_model": "gpt-4o",
    }
    defaults.update(kwargs)
    return OpenAIProvider(**defaults)


def _mock_response(data: dict, status_code: int = 200) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = data
    resp.raise_for_status.return_value = None
    resp.text = json.dumps(data)
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            message=f"HTTP {status_code}",
            request=MagicMock(),
            response=resp,
        )
    return resp


# ---------------------------------------------------------------------------
# generate() tests
# ---------------------------------------------------------------------------


class TestOpenAIGenerate:
    def test_generate_success(self):
        provider = _make_provider()
        response_data = {
            "choices": [{"message": {"content": "Hello!"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2},
        }
        with patch.object(provider._client, "post", return_value=_mock_response(response_data)):
            result = provider.generate([ModelMessage(role="user", content="Hi")])
        assert result.text == "Hello!"
        assert result.finish_reason == "stop"
        assert result.usage["prompt_tokens"] == 5

    def test_generate_http_error(self):
        provider = _make_provider()
        with patch.object(
            provider._client, "post", return_value=_mock_response({}, status_code=401)
        ):
            result = provider.generate([ModelMessage(role="user", content="Hi")])
        assert result.finish_reason == "error"

    def test_generate_timeout(self):
        provider = _make_provider()
        with patch.object(provider._client, "post", side_effect=httpx.TimeoutException("timeout")):
            result = provider.generate([ModelMessage(role="user", content="Hi")])
        assert result.finish_reason == "error"

    def test_generate_connection_error(self):
        provider = _make_provider()
        with patch.object(provider._client, "post", side_effect=httpx.ConnectError("refused")):
            result = provider.generate([ModelMessage(role="user", content="Hi")])
        assert result.finish_reason == "error"


# ---------------------------------------------------------------------------
# stream_generate() tests
# ---------------------------------------------------------------------------


class TestOpenAIStreamGenerate:
    def test_stream_basic(self):
        provider = _make_provider()
        lines = [
            'data: {"choices":[{"delta":{"content":"Hello"},"finish_reason":null}]}',
            'data: {"choices":[{"delta":{"content":" world"},"finish_reason":null}]}',
            "data: [DONE]",
        ]
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.iter_lines.return_value = iter(lines)
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch.object(provider._client, "stream", return_value=mock_resp):
            chunks = list(provider.stream_generate([ModelMessage(role="user", content="Hi")]))

        texts = [c.text for c in chunks if c.text and not c.is_done]
        assert texts == ["Hello", " world"]
        assert chunks[-1].is_done is True

    def test_stream_skips_empty_lines(self):
        provider = _make_provider()
        lines = [
            "",
            'data: {"choices":[{"delta":{"content":"text"},"finish_reason":null}]}',
            "",
            "data: [DONE]",
        ]
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.iter_lines.return_value = iter(lines)
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch.object(provider._client, "stream", return_value=mock_resp):
            chunks = list(provider.stream_generate([ModelMessage(role="user", content="Hi")]))

        text_chunks = [c for c in chunks if c.text and not c.is_done]
        assert len(text_chunks) == 1

    def test_stream_http_error(self):
        provider = _make_provider()
        mock_resp = MagicMock()
        error_resp = MagicMock()
        error_resp.status_code = 429
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            message="rate limited", request=MagicMock(), response=error_resp
        )
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch.object(provider._client, "stream", return_value=mock_resp):
            chunks = list(provider.stream_generate([ModelMessage(role="user", content="Hi")]))
        assert chunks[-1].is_done is True
        assert "429" in chunks[-1].text

    def test_stream_timeout(self):
        provider = _make_provider()
        with patch.object(
            provider._client, "stream", side_effect=httpx.TimeoutException("timeout")
        ):
            chunks = list(provider.stream_generate([ModelMessage(role="user", content="Hi")]))
        assert chunks[-1].is_done is True
        assert "Connection error" in chunks[-1].text


# ---------------------------------------------------------------------------
# test_connection() tests
# ---------------------------------------------------------------------------


class TestOpenAITestConnection:
    def test_success(self):
        provider = _make_provider()
        data = {"data": [{"id": "gpt-4o"}, {"id": "gpt-3.5-turbo"}]}
        with patch.object(provider._client, "get", return_value=_mock_response(data)):
            result = provider.test_connection()
        assert result.success is True
        assert "2 models" in result.message
        assert result.latency_ms > 0

    def test_auth_failure(self):
        provider = _make_provider()
        with patch.object(provider._client, "get", return_value=_mock_response({}, 401)):
            result = provider.test_connection()
        assert result.success is False
        assert "Authentication" in result.message

    def test_server_error(self):
        provider = _make_provider()
        with patch.object(provider._client, "get", return_value=_mock_response({}, 500)):
            result = provider.test_connection()
        assert result.success is False
        assert "500" in result.message

    def test_connection_error(self):
        provider = _make_provider()
        with patch.object(provider._client, "get", side_effect=httpx.ConnectError("refused")):
            result = provider.test_connection()
        assert result.success is False
        assert "Connection failed" in result.message


# ---------------------------------------------------------------------------
# list_models() tests
# ---------------------------------------------------------------------------


class TestOpenAIListModels:
    def test_list_models(self):
        provider = _make_provider()
        data = {"data": [{"id": "gpt-4o"}, {"id": "gpt-3.5-turbo"}]}
        with patch.object(provider._client, "get", return_value=_mock_response(data)):
            models = provider.list_models()
        assert len(models) == 2
        assert models[0].id == "gpt-4o"
        assert models[0].capabilities["tool_use"] is True

    def test_list_models_error(self):
        provider = _make_provider()
        with patch.object(provider._client, "get", side_effect=Exception("fail")):
            models = provider.list_models()
        assert len(models) == 1
        assert models[0].id == provider.default_model


# ---------------------------------------------------------------------------
# Capability tests
# ---------------------------------------------------------------------------


class TestOpenAICapabilities:
    def test_supports_tools(self):
        assert _make_provider().supports_tools() is True

    def test_supports_reasoning(self):
        assert _make_provider().supports_reasoning() is False

    def test_supports_streaming(self):
        assert _make_provider().supports_streaming() is True

    def test_capabilities(self):
        caps = _make_provider().capabilities()
        assert caps == {"tool_use": True, "reasoning": False, "streaming": True}


# ---------------------------------------------------------------------------
# _format_messages() tests
# ---------------------------------------------------------------------------


class TestOpenAIFormatMessages:
    def test_format_messages(self):
        msgs = [
            ModelMessage(role="system", content="You are helpful."),
            ModelMessage(role="user", content="Hi"),
        ]
        result = OpenAIProvider._format_messages(msgs)
        assert result == [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hi"},
        ]

    def test_format_messages_with_tool_calls_and_tool_result(self):
        msgs = [
            ModelMessage(
                role="assistant",
                content="",
                metadata={
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "file_read", "arguments": "{}"},
                        }
                    ]
                },
            ),
            ModelMessage(
                role="tool",
                content='{"status":"success"}',
                metadata={"tool_call_id": "call_1"},
            ),
        ]

        result = OpenAIProvider._format_messages(msgs)

        assert result[0]["tool_calls"][0]["function"]["name"] == "file_read"
        assert result[1] == {
            "role": "tool",
            "content": '{"status":"success"}',
            "tool_call_id": "call_1",
        }


class TestOpenAIBuildRequestBody:
    def test_includes_tools(self):
        provider = _make_provider()
        body = provider._build_request_body(
            [ModelMessage(role="user", content="Hi")],
            tools=[
                {
                    "name": "file_read",
                    "description": "Read a file",
                    "parameters": {"type": "object", "properties": {}},
                }
            ],
            tool_choice="auto",
        )

        assert body["tools"][0]["type"] == "function"
        assert body["tools"][0]["function"]["name"] == "file_read"
        assert body["tool_choice"] == "auto"


# ---------------------------------------------------------------------------
# _parse_sse_line() tests
# ---------------------------------------------------------------------------


class TestOpenAIParseSSE:
    def test_parse_text_chunk(self):
        line = 'data: {"choices":[{"delta":{"content":"Hi"},"finish_reason":null}]}'
        chunk = OpenAIProvider._parse_sse_line(line)
        assert chunk is not None
        assert chunk.text == "Hi"

    def test_parse_done(self):
        chunk = OpenAIProvider._parse_sse_line("data: [DONE]")
        assert chunk is not None
        assert chunk.is_done is True

    def test_parse_empty_line(self):
        assert OpenAIProvider._parse_sse_line("") is None

    def test_parse_non_data_line(self):
        assert OpenAIProvider._parse_sse_line("event: message") is None

    def test_parse_invalid_json(self):
        assert OpenAIProvider._parse_sse_line("data: {invalid}") is None

    def test_parse_finish_reason(self):
        line = 'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}'
        chunk = OpenAIProvider._parse_sse_line(line)
        assert chunk is not None
        assert chunk.is_done is True
