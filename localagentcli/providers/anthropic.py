"""AnthropicProvider — Anthropic Messages API integration."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Iterator

import httpx

from localagentcli.models.backends.base import (
    GenerationResult,
    ModelMessage,
    StreamChunk,
)
from localagentcli.models.readiness import (
    inferred_remote_capability_provenance,
    legacy_fallback_capability_provenance,
)
from localagentcli.providers.base import (
    ConnectionTestResult,
    RemoteModelInfo,
    RemoteProvider,
)

logger = logging.getLogger(__name__)


@dataclass
class _AnthropicBlockState:
    """State for a single streamed Anthropic content block."""

    block_type: str
    block_id: str = ""
    name: str = ""
    input_json: str = ""
    input_data: dict = field(default_factory=dict)

    def to_tool_call(self) -> dict:
        return {
            "id": self.block_id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": self.input_json or json.dumps(self.input_data, ensure_ascii=False),
            },
        }


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
        timeout = float(self._options.get("timeout", 30))
        self._client = httpx.Client(
            base_url=self._base_url,
            headers={
                "x-api-key": self._api_key,
                "anthropic-version": self.ANTHROPIC_VERSION,
                "content-type": "application/json",
            },
            timeout=timeout,
        )
        self._async_client: httpx.AsyncClient | None = None

    def _sync_timeout(self) -> float:
        return float(self._options.get("timeout", 30))

    async def _ensure_async_client(self, timeout: float) -> httpx.AsyncClient:
        if self._async_client is not None:
            return self._async_client
        self._async_client = httpx.AsyncClient(
            base_url=self._base_url,
            headers={
                "x-api-key": self._api_key,
                "anthropic-version": self.ANTHROPIC_VERSION,
                "content-type": "application/json",
            },
            timeout=timeout,
        )
        return self._async_client

    def generate(self, messages: list[ModelMessage], **kwargs: object) -> GenerationResult:
        """POST /v1/messages without streaming."""
        body = self._build_request_body(messages, stream=False, **kwargs)
        try:
            response = self._request_with_retries(
                lambda: self._client.post("/v1/messages", json=body)
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            return GenerationResult(text="", finish_reason="error", usage={"error": str(e)})
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            return GenerationResult(text="", finish_reason="error", usage={"error": str(e)})

        data = response.json()
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_calls: list[dict] = []
        for block in data.get("content", []):
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "thinking":
                reasoning_parts.append(block.get("thinking", ""))
            elif block.get("type") == "tool_use":
                tool_calls.append(
                    {
                        "id": block.get("id", ""),
                        "type": "function",
                        "function": {
                            "name": block.get("name", ""),
                            "arguments": json.dumps(block.get("input", {}), ensure_ascii=False),
                        },
                    }
                )

        return GenerationResult(
            text="".join(text_parts),
            reasoning="".join(reasoning_parts),
            tool_calls=tool_calls,
            usage=data.get("usage", {}),
            finish_reason=data.get("stop_reason", ""),
        )

    def stream_generate(
        self, messages: list[ModelMessage], **kwargs: object
    ) -> Iterator[StreamChunk]:
        """POST /v1/messages with stream=True. Handle Anthropic SSE format."""
        body = self._build_request_body(messages, stream=True, **kwargs)
        blocks: dict[int, _AnthropicBlockState] = {}
        context = None
        try:
            context, resp = self._open_stream_with_retries(
                lambda: self._client.stream("POST", "/v1/messages", json=body)
            )
            resp.raise_for_status()
            event_type = ""
            for line in resp.iter_lines():
                if line.startswith("event: "):
                    event_type = line[7:].strip()
                    continue
                if not line.startswith("data: "):
                    continue
                for chunk in self._parse_sse_event(event_type, line[6:], blocks):
                    yield chunk
                    if chunk.is_done:
                        return
        except httpx.HTTPStatusError as e:
            yield StreamChunk(
                text=f"API error: {e.response.status_code}",
                kind="error",
                importance="secondary",
            )
            yield StreamChunk(kind="done", is_done=True, payload={"finish_reason": "error"})
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            yield StreamChunk(
                text=f"Connection error: {e}",
                kind="error",
                importance="secondary",
            )
            yield StreamChunk(kind="done", is_done=True, payload={"finish_reason": "error"})
        finally:
            if context is not None:
                context.__exit__(None, None, None)

    def test_connection(self) -> ConnectionTestResult:
        """Use /v1/models to verify connectivity and API-key scope."""
        start = time.monotonic()
        try:
            response = self._request_with_retries(lambda: self._client.get("/v1/models"))
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
            response = self._request_with_retries(lambda: self._client.get("/v1/models"))
            response.raise_for_status()
            data = response.json()
            models: list[RemoteModelInfo] = []
            for model_data in data.get("data", []):
                model_id = model_data.get("id", "")
                if not model_id:
                    continue
                capabilities = self._capabilities_for_model(model_id)
                models.append(
                    RemoteModelInfo(
                        id=model_id,
                        name=model_data.get("display_name") or model_data.get("name") or model_id,
                        capabilities=capabilities,
                        capability_provenance=inferred_remote_capability_provenance(
                            capabilities,
                            provider_label="Anthropic",
                        ),
                        selection_state="api_discovered",
                    )
                )
            if models:
                return models
        except Exception:
            logger.debug("Failed to list models from %s", self._name)
        if self._default_model:
            capabilities = self._capabilities_for_model(self._default_model)
            return [
                RemoteModelInfo(
                    id=self._default_model,
                    name=self._default_model,
                    capabilities=capabilities,
                    capability_provenance=legacy_fallback_capability_provenance(capabilities),
                    selection_state="legacy_fallback",
                )
            ]
        return []

    async def agenerate(self, messages: list[ModelMessage], **kwargs: object) -> GenerationResult:
        self._reset_cancel()
        body = self._build_request_body(messages, stream=False, **kwargs)
        timeout = self._request_timeout_value(kwargs)
        client = await self._ensure_async_client(timeout)
        try:
            response = await self._arequest_with_retries(
                lambda: client.post("/v1/messages", json=body)
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            return GenerationResult(text="", finish_reason="error", usage={"error": str(e)})
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            return GenerationResult(text="", finish_reason="error", usage={"error": str(e)})
        finally:
            await self._maybe_close_async_client_after_turn()

        data = response.json()
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_calls: list[dict] = []
        for block in data.get("content", []):
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "thinking":
                reasoning_parts.append(block.get("thinking", ""))
            elif block.get("type") == "tool_use":
                tool_calls.append(
                    {
                        "id": block.get("id", ""),
                        "type": "function",
                        "function": {
                            "name": block.get("name", ""),
                            "arguments": json.dumps(block.get("input", {}), ensure_ascii=False),
                        },
                    }
                )

        return GenerationResult(
            text="".join(text_parts),
            reasoning="".join(reasoning_parts),
            tool_calls=tool_calls,
            usage=data.get("usage", {}),
            finish_reason=data.get("stop_reason", ""),
        )

    async def astream_generate(  # type: ignore[misc, override]
        self, messages: list[ModelMessage], **kwargs: object
    ) -> AsyncIterator[StreamChunk]:
        self._reset_cancel()
        body = self._build_request_body(messages, stream=True, **kwargs)
        blocks: dict[int, _AnthropicBlockState] = {}
        timeout = self._request_timeout_value(kwargs)
        client = await self._ensure_async_client(timeout)
        context = None
        try:

            def _stream_factory():
                return client.stream("POST", "/v1/messages", json=body)

            context, resp = await self._aopen_stream_with_retries(_stream_factory)
            resp.raise_for_status()
            self._track_async_stream(resp)
            try:
                event_type = ""
                async for line in self._aiter_lines_with_idle_timeout(resp, kwargs):
                    if self._cancel_requested:
                        yield StreamChunk(
                            text="Generation interrupted.",
                            kind="notification",
                            importance="secondary",
                        )
                        yield StreamChunk(
                            kind="done", is_done=True, payload={"finish_reason": "cancelled"}
                        )
                        return
                    if line.startswith("event: "):
                        event_type = line[7:].strip()
                        continue
                    if not line.startswith("data: "):
                        continue
                    for chunk in self._parse_sse_event(event_type, line[6:], blocks):
                        yield chunk
                        if chunk.is_done:
                            return
            finally:
                self._untrack_async_stream()
        except httpx.HTTPStatusError as e:
            yield StreamChunk(
                text=f"API error: {e.response.status_code}",
                kind="error",
                importance="secondary",
            )
            yield StreamChunk(kind="done", is_done=True, payload={"finish_reason": "error"})
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            yield StreamChunk(
                text=f"Connection error: {e}",
                kind="error",
                importance="secondary",
            )
            yield StreamChunk(kind="done", is_done=True, payload={"finish_reason": "error"})
        except TimeoutError as e:
            yield StreamChunk(
                text=f"Connection error: {e}",
                kind="error",
                importance="secondary",
            )
            yield StreamChunk(kind="done", is_done=True, payload={"finish_reason": "error"})
        except asyncio.CancelledError:
            yield StreamChunk(
                text="Generation interrupted.",
                kind="notification",
                importance="secondary",
            )
            yield StreamChunk(kind="done", is_done=True, payload={"finish_reason": "cancelled"})
            raise
        finally:
            if context is not None:
                await context.__aexit__(None, None, None)
            await self._maybe_close_async_client_after_turn()

    async def atest_connection(self) -> ConnectionTestResult:
        start = time.monotonic()
        timeout = self._sync_timeout()
        client = await self._ensure_async_client(timeout)
        try:
            response = await self._arequest_with_retries(lambda: client.get("/v1/models"))
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

    async def alist_models(self) -> list[RemoteModelInfo]:
        try:
            timeout = self._sync_timeout()
            client = await self._ensure_async_client(timeout)
            response = await self._arequest_with_retries(lambda: client.get("/v1/models"))
            response.raise_for_status()
            data = response.json()
            models: list[RemoteModelInfo] = []
            for model_data in data.get("data", []):
                model_id = model_data.get("id", "")
                if not model_id:
                    continue
                capabilities = self._capabilities_for_model(model_id)
                models.append(
                    RemoteModelInfo(
                        id=model_id,
                        name=model_data.get("display_name") or model_data.get("name") or model_id,
                        capabilities=capabilities,
                        capability_provenance=inferred_remote_capability_provenance(
                            capabilities,
                            provider_label="Anthropic",
                        ),
                        selection_state="api_discovered",
                    )
                )
            if models:
                return models
        except Exception:
            logger.debug("Failed to list models from %s", self._name)
        if self._default_model:
            capabilities = self._capabilities_for_model(self._default_model)
            return [
                RemoteModelInfo(
                    id=self._default_model,
                    name=self._default_model,
                    capabilities=capabilities,
                    capability_provenance=legacy_fallback_capability_provenance(capabilities),
                    selection_state="legacy_fallback",
                )
            ]
        return []

    def supports_tools(self) -> bool:
        return bool(self._capabilities_for_model(self.active_model).get("tool_use", False))

    def supports_reasoning(self) -> bool:
        return bool(self._capabilities_for_model(self.active_model).get("reasoning", False))

    def supports_streaming(self) -> bool:
        return True

    def capabilities(self) -> dict:
        return self._capabilities_for_model(self.active_model)

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
        model_name = str(kwargs.get("model", self.active_model) or self.active_model)
        self.set_active_model(model_name)
        system_text, api_messages = self._format_messages(messages)
        body: dict = {
            "model": model_name,
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
    def _parse_sse_event(
        event_type: str,
        data_str: str,
        blocks: dict[int, _AnthropicBlockState],
    ) -> list[StreamChunk]:
        """Parse an Anthropic SSE event into normalized stream chunks."""
        try:
            data = json.loads(data_str)
        except json.JSONDecodeError:
            return []

        chunks: list[StreamChunk] = []

        if event_type == "content_block_start":
            index = int(data.get("index", 0))
            block = data.get("content_block", {})
            block_type = str(block.get("type", ""))
            if block_type:
                blocks[index] = _AnthropicBlockState(
                    block_type=block_type,
                    block_id=str(block.get("id", "")),
                    name=str(block.get("name", "")),
                    input_data=block.get("input", {})
                    if isinstance(block.get("input", {}), dict)
                    else {},
                )
                if block_type == "tool_use":
                    chunks.append(
                        StreamChunk(
                            text=f"Model prepared tool call: {block.get('name', '')}",
                            kind="notification",
                            importance="secondary",
                            transient=True,
                            payload={"tool_name": block.get("name", "")},
                        )
                    )
            return chunks

        if event_type == "content_block_delta":
            index = int(data.get("index", 0))
            state = blocks.get(index)
            delta = data.get("delta", {})
            delta_type = delta.get("type", "")
            if delta_type == "text_delta":
                chunks.append(StreamChunk(text=delta.get("text", ""), kind="final_text"))
            elif delta_type == "thinking_delta":
                chunks.append(
                    StreamChunk(
                        text=delta.get("thinking", ""),
                        kind="reasoning",
                        importance="secondary",
                    )
                )
            elif delta_type == "input_json_delta" and state is not None:
                state.input_json += str(delta.get("partial_json", ""))
            return chunks

        if event_type == "content_block_stop":
            index = int(data.get("index", 0))
            state = blocks.pop(index, None)
            if state is not None and state.block_type == "tool_use":
                tool_call = state.to_tool_call()
                chunks.append(
                    StreamChunk(
                        kind="tool_call",
                        importance="secondary",
                        payload=tool_call,
                        tool_call_data=tool_call,
                    )
                )
            return chunks

        if event_type == "message_stop":
            return [StreamChunk(kind="done", is_done=True, payload={"finish_reason": "stop"})]

        if event_type == "message_delta":
            usage = data.get("usage")
            stop_reason = data.get("delta", {}).get("stop_reason")
            if stop_reason:
                chunks.append(
                    StreamChunk(
                        kind="done",
                        is_done=True,
                        usage=usage if isinstance(usage, dict) else None,
                        payload={"finish_reason": stop_reason},
                    )
                )
            return chunks

        return []

    @staticmethod
    def _capabilities_for_model(model_id: str) -> dict:
        return {"tool_use": True, "reasoning": True, "streaming": True}
