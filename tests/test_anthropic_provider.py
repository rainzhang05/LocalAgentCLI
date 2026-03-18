"""Tests for AnthropicProvider with mocked HTTP responses."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx

from localagentcli.models.backends.base import ModelMessage
from localagentcli.providers.anthropic import ANTHROPIC_MODELS, AnthropicProvider


def _make_provider(**kwargs: object) -> AnthropicProvider:
    defaults: dict = {
        "name": "anthropic",
        "base_url": "https://api.anthropic.com",
        "api_key": "sk-ant-test",
        "default_model": "claude-sonnet-4-20250514",
    }
    defaults.update(kwargs)
    return AnthropicProvider(**defaults)


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
# generate() tests
# ---------------------------------------------------------------------------


class TestAnthropicGenerate:
    def test_generate_success(self):
        provider = _make_provider()
        data = {
            "content": [{"type": "text", "text": "Hello!"}],
            "usage": {"input_tokens": 5, "output_tokens": 2},
            "stop_reason": "end_turn",
        }
        with patch.object(provider._client, "post", return_value=_mock_response(data)):
            result = provider.generate([ModelMessage(role="user", content="Hi")])
        assert result.text == "Hello!"
        assert result.finish_reason == "end_turn"

    def test_generate_with_reasoning(self):
        provider = _make_provider()
        data = {
            "content": [
                {"type": "thinking", "thinking": "Let me think..."},
                {"type": "text", "text": "Answer"},
            ],
            "usage": {},
            "stop_reason": "end_turn",
        }
        with patch.object(provider._client, "post", return_value=_mock_response(data)):
            result = provider.generate([ModelMessage(role="user", content="Hi")])
        assert result.text == "Answer"
        assert result.reasoning == "Let me think..."

    def test_generate_http_error(self):
        provider = _make_provider()
        with patch.object(provider._client, "post", return_value=_mock_response({}, 401)):
            result = provider.generate([ModelMessage(role="user", content="Hi")])
        assert result.finish_reason == "error"

    def test_generate_timeout(self):
        provider = _make_provider()
        with patch.object(provider._client, "post", side_effect=httpx.TimeoutException("timeout")):
            result = provider.generate([ModelMessage(role="user", content="Hi")])
        assert result.finish_reason == "error"


# ---------------------------------------------------------------------------
# stream_generate() tests
# ---------------------------------------------------------------------------


class TestAnthropicStreamGenerate:
    def test_stream_basic(self):
        provider = _make_provider()
        lines = [
            "event: message_start",
            'data: {"type":"message_start","message":{"id":"msg_01"}}',
            "event: content_block_delta",
            'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hello"}}',
            "event: content_block_delta",
            'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":" world"}}',
            "event: message_stop",
            'data: {"type":"message_stop"}',
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

    def test_stream_with_thinking(self):
        provider = _make_provider()
        think_data = json.dumps(
            {"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": "hmm"}}
        )
        text_data = json.dumps(
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "answer"}}
        )
        lines = [
            "event: content_block_delta",
            f"data: {think_data}",
            "event: content_block_delta",
            f"data: {text_data}",
            "event: message_stop",
            'data: {"type":"message_stop"}',
        ]
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.iter_lines.return_value = iter(lines)
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch.object(provider._client, "stream", return_value=mock_resp):
            chunks = list(provider.stream_generate([ModelMessage(role="user", content="Think")]))

        reasoning = [c for c in chunks if c.is_reasoning]
        assert len(reasoning) == 1
        assert reasoning[0].text == "hmm"

    def test_stream_http_error(self):
        provider = _make_provider()
        mock_resp = MagicMock()
        error_resp = MagicMock()
        error_resp.status_code = 500
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            message="server error", request=MagicMock(), response=error_resp
        )
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch.object(provider._client, "stream", return_value=mock_resp):
            chunks = list(provider.stream_generate([ModelMessage(role="user", content="Hi")]))
        assert chunks[-1].is_done is True
        assert "500" in chunks[-1].text

    def test_stream_message_delta_stop(self):
        provider = _make_provider()
        delta_data = json.dumps(
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn"},
                "usage": {"output_tokens": 10},
            }
        )
        lines = [
            "event: message_delta",
            f"data: {delta_data}",
        ]
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.iter_lines.return_value = iter(lines)
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch.object(provider._client, "stream", return_value=mock_resp):
            chunks = list(provider.stream_generate([ModelMessage(role="user", content="Hi")]))
        assert chunks[-1].is_done is True


# ---------------------------------------------------------------------------
# test_connection() tests
# ---------------------------------------------------------------------------


class TestAnthropicTestConnection:
    def test_success(self):
        provider = _make_provider()
        data = {
            "content": [{"type": "text", "text": "H"}],
            "stop_reason": "max_tokens",
        }
        with patch.object(provider._client, "post", return_value=_mock_response(data)):
            result = provider.test_connection()
        assert result.success is True
        assert result.latency_ms > 0

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
# list_models() tests
# ---------------------------------------------------------------------------


class TestAnthropicListModels:
    def test_returns_static_list(self):
        provider = _make_provider()
        models = provider.list_models()
        assert len(models) == len(ANTHROPIC_MODELS)
        ids = {m.id for m in models}
        assert "claude-sonnet-4-20250514" in ids

    def test_returns_copy(self):
        provider = _make_provider()
        m1 = provider.list_models()
        m2 = provider.list_models()
        assert m1 is not m2


# ---------------------------------------------------------------------------
# Capability tests
# ---------------------------------------------------------------------------


class TestAnthropicCapabilities:
    def test_supports_tools(self):
        assert _make_provider().supports_tools() is True

    def test_supports_reasoning(self):
        assert _make_provider().supports_reasoning() is True

    def test_supports_streaming(self):
        assert _make_provider().supports_streaming() is True

    def test_capabilities(self):
        assert _make_provider().capabilities() == {
            "tool_use": True,
            "reasoning": True,
            "streaming": True,
        }


# ---------------------------------------------------------------------------
# _format_messages() tests
# ---------------------------------------------------------------------------


class TestAnthropicFormatMessages:
    def test_separates_system(self):
        msgs = [
            ModelMessage(role="system", content="You are helpful."),
            ModelMessage(role="user", content="Hi"),
        ]
        system, api_msgs = AnthropicProvider._format_messages(msgs)
        assert system == "You are helpful."
        assert len(api_msgs) == 1
        assert api_msgs[0]["role"] == "user"

    def test_no_system(self):
        msgs = [ModelMessage(role="user", content="Hi")]
        system, api_msgs = AnthropicProvider._format_messages(msgs)
        assert system == ""
        assert len(api_msgs) == 1

    def test_preserves_alternation(self):
        msgs = [
            ModelMessage(role="user", content="Hi"),
            ModelMessage(role="assistant", content="Hello"),
            ModelMessage(role="user", content="How are you?"),
        ]
        _, api_msgs = AnthropicProvider._format_messages(msgs)
        roles = [m["role"] for m in api_msgs]
        assert roles == ["user", "assistant", "user"]


# ---------------------------------------------------------------------------
# _parse_sse_event() tests
# ---------------------------------------------------------------------------


class TestAnthropicParseSSE:
    def test_text_delta(self):
        data = json.dumps({"delta": {"type": "text_delta", "text": "Hi"}})
        chunk = AnthropicProvider._parse_sse_event("content_block_delta", data)
        assert chunk is not None
        assert chunk.text == "Hi"
        assert chunk.is_reasoning is False

    def test_thinking_delta(self):
        data = json.dumps({"delta": {"type": "thinking_delta", "thinking": "hmm"}})
        chunk = AnthropicProvider._parse_sse_event("content_block_delta", data)
        assert chunk is not None
        assert chunk.text == "hmm"
        assert chunk.is_reasoning is True

    def test_message_stop(self):
        chunk = AnthropicProvider._parse_sse_event("message_stop", "{}")
        assert chunk is not None
        assert chunk.is_done is True

    def test_message_delta_with_stop_reason(self):
        data = json.dumps({"delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 5}})
        chunk = AnthropicProvider._parse_sse_event("message_delta", data)
        assert chunk is not None
        assert chunk.is_done is True

    def test_unknown_event(self):
        chunk = AnthropicProvider._parse_sse_event("content_block_start", '{"type":"text"}')
        assert chunk is None

    def test_invalid_json(self):
        chunk = AnthropicProvider._parse_sse_event("content_block_delta", "not json")
        assert chunk is None
