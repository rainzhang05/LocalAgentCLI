"""Tests for OpenAIProvider with mocked HTTP responses."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx

from localagentcli.models.backends.base import ModelMessage
from localagentcli.providers.openai import OpenAIProvider, _OpenAIToolCallAccumulator


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
        assert "429" in chunks[0].text
        assert chunks[0].kind == "error"

    def test_stream_timeout(self):
        provider = _make_provider()
        with patch.object(
            provider._client, "stream", side_effect=httpx.TimeoutException("timeout")
        ):
            chunks = list(provider.stream_generate([ModelMessage(role="user", content="Hi")]))
        assert chunks[-1].is_done is True
        assert "Connection error" in chunks[0].text
        assert chunks[0].kind == "error"


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
        assert models[0].selection_state == "api_discovered"
        assert models[0].capability_provenance["tool_use"]["tier"] == "inferred"
        assert models[0].capability_provenance["reasoning"]["tier"] == "inferred"

    def test_list_models_error(self):
        provider = _make_provider()
        with patch.object(provider._client, "get", side_effect=Exception("fail")):
            models = provider.list_models()
        assert len(models) == 1
        assert models[0].id == provider.default_model
        assert models[0].selection_state == "legacy_fallback"
        assert models[0].capability_provenance["tool_use"]["tier"] == "legacy_fallback"


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

    def test_reasoning_depends_on_active_model(self):
        provider = _make_provider(default_model="gpt-5")

        assert provider.supports_reasoning() is True

    def test_model_info_exposes_reasoning_levels_for_reasoning_models(self):
        provider = _make_provider(default_model="gpt-5")

        info = provider.model_info()

        assert info.capabilities["reasoning"] is True
        assert info.supported_reasoning_levels == ["low", "medium", "high"]

    def test_prompt_profile_is_generic(self):
        profile = _make_provider().prompt_profile()

        assert profile.provider_kind == "generic"
        assert profile.structured_system_blocks is False


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

    def test_prompt_cache_fields_can_be_passed_through_options(self):
        provider = _make_provider(
            options={"prompt_cache": "true", "prompt_cache_key": "workspace-1"}
        )
        body = provider._build_request_body([ModelMessage(role="user", content="Hi")])

        assert body["prompt_cache"] is True
        assert body["prompt_cache_key"] == "workspace-1"

    def test_prompt_cache_kwargs_override_options(self):
        provider = _make_provider(options={"prompt_cache": False, "prompt_cache_key": "old-key"})
        body = provider._build_request_body(
            [ModelMessage(role="user", content="Hi")],
            prompt_cache=True,
            prompt_cache_key="new-key",
        )

        assert body["prompt_cache"] is True
        assert body["prompt_cache_key"] == "new-key"

    def test_reasoning_effort_is_passed_for_reasoning_models(self):
        provider = _make_provider(default_model="gpt-5")
        body = provider._build_request_body(
            [ModelMessage(role="user", content="Hi")],
            reasoning_effort="high",
        )

        assert body["reasoning_effort"] == "high"

    def test_reasoning_effort_is_not_passed_for_non_reasoning_models(self):
        provider = _make_provider(default_model="gpt-4o")
        body = provider._build_request_body(
            [ModelMessage(role="user", content="Hi")],
            reasoning_effort="high",
        )

        assert "reasoning_effort" not in body


# ---------------------------------------------------------------------------
# _parse_sse_line() tests
# ---------------------------------------------------------------------------


class TestOpenAIParseSSE:
    def test_parse_text_chunk(self):
        line = 'data: {"choices":[{"delta":{"content":"Hi"},"finish_reason":null}]}'
        chunks = OpenAIProvider._parse_sse_line(line, _OpenAIToolCallAccumulator())
        assert chunks[0].text == "Hi"

    def test_parse_done(self):
        chunks = OpenAIProvider._parse_sse_line("data: [DONE]", _OpenAIToolCallAccumulator())
        assert chunks[-1].is_done is True

    def test_parse_empty_line(self):
        assert OpenAIProvider._parse_sse_line("", _OpenAIToolCallAccumulator()) == []

    def test_parse_non_data_line(self):
        assert OpenAIProvider._parse_sse_line("event: message", _OpenAIToolCallAccumulator()) == []

    def test_parse_invalid_json(self):
        assert (
            OpenAIProvider._parse_sse_line(
                "data: {invalid}",
                _OpenAIToolCallAccumulator(),
            )
            == []
        )

    def test_parse_finish_reason(self):
        line = 'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}'
        chunks = OpenAIProvider._parse_sse_line(line, _OpenAIToolCallAccumulator())
        assert chunks[-1].is_done is True
        assert chunks[-1].payload == {"finish_reason": "stop"}

    def test_parse_tool_call_delta(self):
        accumulator = _OpenAIToolCallAccumulator()
        chunks = OpenAIProvider._parse_sse_line(
            (
                'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
                '"id":"call_1","function":{"name":"file_read",'
                '"arguments":"{\\"path\\":"}}]},"finish_reason":null}]}'
            ),
            accumulator,
        )

        assert chunks[0].kind == "notification"
        completed = OpenAIProvider._parse_sse_line(
            (
                'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
                '"function":{"arguments":"\\"notes.txt\\"}"}}]},'
                '"finish_reason":"tool_calls"}]}'
            ),
            accumulator,
        )
        assert completed[0].kind == "tool_call"
        assert completed[0].tool_call_data["function"]["name"] == "file_read"

    def test_parse_error_payload_line_emits_error_and_done(self):
        chunks = OpenAIProvider._parse_sse_line(
            'data: {"error":{"message":"rate limit exceeded"}}',
            _OpenAIToolCallAccumulator(),
        )

        assert chunks[0].kind == "error"
        assert "rate limit exceeded" in chunks[0].text
        assert chunks[-1].is_done is True
        assert chunks[-1].payload == {"finish_reason": "error"}
