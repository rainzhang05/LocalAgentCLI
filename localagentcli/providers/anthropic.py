"""AnthropicProvider — Anthropic Messages API integration."""

from __future__ import annotations

import json
import logging
import time
from typing import Iterator

import httpx

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

logger = logging.getLogger(__name__)


class AnthropicProvider(RemoteProvider):
    """Provider for the Anthropic Messages API."""

    ANTHROPIC_VERSION = "2023-06-01"

    def __init__(
        self,
        name: str,
        base_url: str,
        api_key: str,
        default_model: str,
        options: dict | None = None,
    ):
        super().__init__(name, base_url, api_key, default_model, options)
        timeout = self._options.get("timeout", 30)
        self._client = httpx.Client(
            base_url=self._base_url,
            headers={
                "x-api-key": self._api_key,
                "anthropic-version": self.ANTHROPIC_VERSION,
                "content-type": "application/json",
            },
            timeout=timeout,
        )

    def generate(self, messages: list[ModelMessage], **kwargs: object) -> GenerationResult:
        """POST /v1/messages without streaming."""
        body = self._build_request_body(messages, stream=False, **kwargs)
        try:
            response = self._client.post("/v1/messages", json=body)
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            return GenerationResult(text="", finish_reason="error", usage={"error": str(e)})
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            return GenerationResult(text="", finish_reason="error", usage={"error": str(e)})

        data = response.json()
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        for block in data.get("content", []):
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "thinking":
                reasoning_parts.append(block.get("thinking", ""))
            elif block.get("type") == "tool_use":
                tool_calls = [
                    {
                        "id": block.get("id", ""),
                        "type": "function",
                        "function": {
                            "name": block.get("name", ""),
                            "arguments": json.dumps(block.get("input", {})),
                        },
                    }
                ]
                return GenerationResult(
                    text="".join(text_parts),
                    reasoning="".join(reasoning_parts),
                    tool_calls=tool_calls,
                    usage=data.get("usage", {}),
                    finish_reason=data.get("stop_reason", ""),
                )

        return GenerationResult(
            text="".join(text_parts),
            reasoning="".join(reasoning_parts),
            usage=data.get("usage", {}),
            finish_reason=data.get("stop_reason", ""),
        )

    def stream_generate(
        self, messages: list[ModelMessage], **kwargs: object
    ) -> Iterator[StreamChunk]:
        """POST /v1/messages with stream=True. Handle Anthropic SSE format."""
        body = self._build_request_body(messages, stream=True, **kwargs)
        try:
            with self._client.stream("POST", "/v1/messages", json=body) as resp:
                resp.raise_for_status()
                event_type = ""
                for line in resp.iter_lines():
                    if line.startswith("event: "):
                        event_type = line[7:].strip()
                        continue
                    if not line.startswith("data: "):
                        continue
                    chunk = self._parse_sse_event(event_type, line[6:])
                    if chunk is not None:
                        yield chunk
                        if chunk.is_done:
                            return
        except httpx.HTTPStatusError as e:
            yield StreamChunk(text=f"API error: {e.response.status_code}", is_done=True)
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            yield StreamChunk(text=f"Connection error: {e}", is_done=True)

    def test_connection(self) -> ConnectionTestResult:
        """Use /v1/models to verify connectivity and API-key scope."""
        start = time.monotonic()
        try:
            response = self._client.get("/v1/models")
            response.raise_for_status()
            latency = max((time.monotonic() - start) * 1000, 0.001)
            model_count = len(response.json().get("data", []))
            return ConnectionTestResult(
                success=True,
                message=f"Connected. {model_count} models available.",
                latency_ms=latency,
            )
        except httpx.HTTPStatusError as e:
            latency = max((time.monotonic() - start) * 1000, 0.001)
            if e.response.status_code == 401:
                msg = "Authentication failed. Check your API key."
            else:
                msg = f"HTTP {e.response.status_code}: {e.response.text[:200]}"
            return ConnectionTestResult(success=False, message=msg, latency_ms=latency)
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            latency = max((time.monotonic() - start) * 1000, 0.001)
            return ConnectionTestResult(
                success=False,
                message=f"Connection failed: {e}",
                latency_ms=latency,
            )

    def list_models(self) -> list[RemoteModelInfo]:
        """GET /v1/models and parse the response."""
        try:
            response = self._client.get("/v1/models")
            response.raise_for_status()
            data = response.json()
            models: list[RemoteModelInfo] = []
            for model_data in data.get("data", []):
                model_id = model_data.get("id", "")
                if not model_id:
                    continue
                models.append(
                    RemoteModelInfo(
                        id=model_id,
                        name=model_data.get("display_name") or model_data.get("name") or model_id,
                        capabilities=self._capabilities_for_model(model_id),
                    )
                )
            if models:
                return models
        except Exception:
            logger.debug("Failed to list models from %s", self._name)
        return [
            RemoteModelInfo(
                id=self._default_model,
                name=self._default_model,
                capabilities=self._capabilities_for_model(self._default_model),
            )
        ]

    def supports_tools(self) -> bool:
        return True

    def supports_reasoning(self) -> bool:
        return True

    def supports_streaming(self) -> bool:
        return True

    def capabilities(self) -> dict:
        return {"tool_use": True, "reasoning": True, "streaming": True}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_request_body(
        self,
        messages: list[ModelMessage],
        stream: bool = False,
        **kwargs: object,
    ) -> dict:
        """Build the request body for /v1/messages."""
        system_text, api_messages = self._format_messages(messages)
        body: dict = {
            "model": kwargs.get("model", self._default_model),
            "messages": api_messages,
            "stream": stream,
            "max_tokens": kwargs.get("max_tokens", 4096),
        }
        if system_text:
            body["system"] = system_text
        if "temperature" in kwargs:
            body["temperature"] = kwargs["temperature"]
        tool_definitions = kwargs.get("tools")
        if isinstance(tool_definitions, list) and tool_definitions:
            body["tools"] = [
                {
                    "name": definition["name"],
                    "description": definition["description"],
                    "input_schema": definition["parameters"],
                }
                for definition in tool_definitions
                if isinstance(definition, dict)
            ]
        if "tool_choice" in kwargs:
            body["tool_choice"] = kwargs["tool_choice"]
        return body

    @staticmethod
    def _format_messages(
        messages: list[ModelMessage],
    ) -> tuple[str, list[dict]]:
        """Separate system message and format user/assistant messages.

        Returns (system_text, api_messages).
        Anthropic requires alternating user/assistant roles.
        """
        system_parts: list[str] = []
        api_messages: list[dict] = []
        index = 0
        while index < len(messages):
            msg = messages[index]
            if msg.role == "system":
                system_parts.append(msg.content)
                index += 1
                continue

            if msg.role == "tool":
                blocks = []
                while index < len(messages) and messages[index].role == "tool":
                    tool_message = messages[index]
                    blocks.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_message.metadata.get("tool_call_id", ""),
                            "content": tool_message.content,
                        }
                    )
                    index += 1
                api_messages.append({"role": "user", "content": blocks})
                continue

            if msg.role == "assistant" and msg.metadata.get("tool_calls"):
                blocks = []
                if msg.content:
                    blocks.append({"type": "text", "text": msg.content})
                for tool_call in msg.metadata["tool_calls"]:
                    function = tool_call.get("function", {})
                    raw_arguments = function.get("arguments", "{}")
                    try:
                        parsed_arguments = (
                            raw_arguments
                            if isinstance(raw_arguments, dict)
                            else json.loads(raw_arguments)
                        )
                    except json.JSONDecodeError:
                        parsed_arguments = {}
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": tool_call.get("id", ""),
                            "name": function.get("name", ""),
                            "input": parsed_arguments,
                        }
                    )
                api_messages.append({"role": "assistant", "content": blocks})
                index += 1
                continue

            api_messages.append({"role": msg.role, "content": msg.content})
            index += 1

        return "\n\n".join(system_parts), api_messages

    @staticmethod
    def _parse_sse_event(event_type: str, data_str: str) -> StreamChunk | None:
        """Parse an Anthropic SSE event into a StreamChunk."""
        try:
            data = json.loads(data_str)
        except json.JSONDecodeError:
            return None

        if event_type == "content_block_delta":
            delta = data.get("delta", {})
            delta_type = delta.get("type", "")
            if delta_type == "text_delta":
                return StreamChunk(text=delta.get("text", ""))
            if delta_type == "thinking_delta":
                return StreamChunk(text=delta.get("thinking", ""), is_reasoning=True)

        if event_type == "message_stop":
            return StreamChunk(is_done=True)

        if event_type == "message_delta":
            usage = data.get("usage")
            stop_reason = data.get("delta", {}).get("stop_reason")
            if stop_reason:
                return StreamChunk(is_done=True, usage=usage)

        return None

    @staticmethod
    def _capabilities_for_model(model_id: str) -> dict:
        return {"tool_use": True, "reasoning": True, "streaming": True}
