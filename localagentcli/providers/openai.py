"""OpenAIProvider — OpenAI Chat Completions API integration."""

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
from localagentcli.models.model_info import ModelInfo
from localagentcli.models.readiness import (
    inferred_remote_capability_provenance,
    legacy_fallback_capability_provenance,
)
from localagentcli.providers.base import (
    ConnectionTestResult,
    RemoteProvider,
)

logger = logging.getLogger(__name__)


@dataclass
class _OpenAIToolCallAccumulator:
    """Accumulate streamed OpenAI tool-call deltas into complete tool calls."""

    calls: dict[int, dict] = field(default_factory=dict)
    announced: set[int] = field(default_factory=set)

    def ingest(self, raw_tool_calls: list[dict]) -> list[StreamChunk]:
        notifications: list[StreamChunk] = []
        for raw_call in raw_tool_calls:
            index = int(raw_call.get("index", len(self.calls)))
            call = self.calls.setdefault(
                index,
                {
                    "id": "",
                    "type": "function",
                    "function": {"name": "", "arguments": ""},
                },
            )
            if raw_call.get("id"):
                call["id"] = raw_call["id"]
            function = raw_call.get("function", {})
            if function.get("name"):
                call["function"]["name"] = function["name"]
            if function.get("arguments"):
                call["function"]["arguments"] += function["arguments"]

            if call["function"]["name"] and index not in self.announced:
                self.announced.add(index)
                notifications.append(
                    StreamChunk(
                        text=f"Model prepared tool call: {call['function']['name']}",
                        kind="notification",
                        importance="secondary",
                        transient=True,
                        payload={"tool_name": call["function"]["name"]},
                    )
                )
        return notifications

    def finalized(self) -> list[dict]:
        return [self.calls[index] for index in sorted(self.calls)]


class OpenAIProvider(RemoteProvider):
    """Provider for OpenAI-compatible Chat Completions APIs.

    Supports OpenAI, Azure OpenAI, Together AI, Fireworks, vLLM,
    Ollama (OpenAI mode), and any service implementing the OpenAI spec.
    """

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
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
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
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )
        return self._async_client

    def generate(self, messages: list[ModelMessage], **kwargs: object) -> GenerationResult:
        """POST /chat/completions without streaming."""
        body = self._build_request_body(messages, stream=False, **kwargs)
        try:
            response = self._request_with_retries(
                lambda: self._client.post("/chat/completions", json=body)
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            return GenerationResult(text="", finish_reason="error", usage={"error": str(e)})
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            return GenerationResult(text="", finish_reason="error", usage={"error": str(e)})

        data = response.json()
        choice = data.get("choices", [{}])[0]
        message = choice.get("message", {})
        usage = data.get("usage", {})

        return GenerationResult(
            text=self._extract_message_text(message),
            reasoning=self._extract_message_reasoning(message),
            tool_calls=message.get("tool_calls", []),
            usage=usage,
            finish_reason=choice.get("finish_reason", ""),
        )

    def stream_generate(
        self, messages: list[ModelMessage], **kwargs: object
    ) -> Iterator[StreamChunk]:
        """POST /chat/completions with stream=True. Parse SSE."""
        body = self._build_request_body(messages, stream=True, **kwargs)
        accumulator = _OpenAIToolCallAccumulator()
        context = None
        try:
            context, resp = self._open_stream_with_retries(
                lambda: self._client.stream("POST", "/chat/completions", json=body)
            )
            resp.raise_for_status()
            for line in resp.iter_lines():
                for chunk in self._parse_sse_line(line, accumulator):
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
        """GET /models to verify API key and connectivity."""
        start = time.monotonic()
        try:
            response = self._request_with_retries(lambda: self._client.get("/models"))
            response.raise_for_status()
            latency = max((time.monotonic() - start) * 1000, 0.001)
            data = response.json()
            model_count = len(data.get("data", []))
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

    def list_models(self) -> list[ModelInfo]:
        """GET /models and parse the response."""
        try:
            response = self._request_with_retries(lambda: self._client.get("/models"))
            response.raise_for_status()
            data = response.json()
            models: list[ModelInfo] = []
            for model_data in data.get("data", []):
                model_id = model_data.get("id", "")
                if not model_id:
                    continue
                capabilities = self._capabilities_for_model(model_id)
                models.append(
                    ModelInfo(
                        id=model_id,
                        name=model_data.get("id", ""),
                        capabilities=capabilities,
                        supported_reasoning_levels=(
                            ["low", "medium", "high"]
                            if capabilities.get("reasoning", False)
                            else []
                        ),
                        capability_provenance=inferred_remote_capability_provenance(
                            capabilities,
                            provider_label="OpenAI-compatible",
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
                ModelInfo(
                    id=self._default_model,
                    name=self._default_model,
                    capabilities=capabilities,
                    supported_reasoning_levels=(
                        ["low", "medium", "high"] if capabilities.get("reasoning", False) else []
                    ),
                    capability_provenance=legacy_fallback_capability_provenance(capabilities),
                    selection_state="legacy_fallback",
                )
            ]
        return []

    async def agenerate(self, messages: list[ModelMessage], **kwargs: object) -> GenerationResult:
        """POST /chat/completions without streaming (async)."""
        self._reset_cancel()
        body = self._build_request_body(messages, stream=False, **kwargs)
        timeout = self._request_timeout_value(kwargs)
        client = await self._ensure_async_client(timeout)
        try:
            response = await self._arequest_with_retries(
                lambda: client.post("/chat/completions", json=body)
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            return GenerationResult(text="", finish_reason="error", usage={"error": str(e)})
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            return GenerationResult(text="", finish_reason="error", usage={"error": str(e)})
        finally:
            await self._maybe_close_async_client_after_turn()

        data = response.json()
        choice = data.get("choices", [{}])[0]
        message = choice.get("message", {})
        usage = data.get("usage", {})

        return GenerationResult(
            text=self._extract_message_text(message),
            reasoning=self._extract_message_reasoning(message),
            tool_calls=message.get("tool_calls", []),
            usage=usage,
            finish_reason=choice.get("finish_reason", ""),
        )

    async def astream_generate(  # type: ignore[misc, override]
        self, messages: list[ModelMessage], **kwargs: object
    ) -> AsyncIterator[StreamChunk]:
        """POST /chat/completions with stream=True (async SSE)."""
        self._reset_cancel()
        body = self._build_request_body(messages, stream=True, **kwargs)
        accumulator = _OpenAIToolCallAccumulator()
        timeout = self._request_timeout_value(kwargs)
        client = await self._ensure_async_client(timeout)
        context = None
        try:

            def _stream_factory():
                return client.stream("POST", "/chat/completions", json=body)

            context, resp = await self._aopen_stream_with_retries(_stream_factory)
            resp.raise_for_status()
            self._track_async_stream(resp)
            try:
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
                    for chunk in self._parse_sse_line(line, accumulator):
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
        """GET /models (async)."""
        start = time.monotonic()
        timeout = self._sync_timeout()
        client = await self._ensure_async_client(timeout)
        try:
            response = await self._arequest_with_retries(lambda: client.get("/models"))
            response.raise_for_status()
            latency = max((time.monotonic() - start) * 1000, 0.001)
            data = response.json()
            model_count = len(data.get("data", []))
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

    async def alist_models(self) -> list[ModelInfo]:
        """GET /models (async)."""
        try:
            timeout = self._sync_timeout()
            client = await self._ensure_async_client(timeout)
            response = await self._arequest_with_retries(lambda: client.get("/models"))
            response.raise_for_status()
            data = response.json()
            models: list[ModelInfo] = []
            for model_data in data.get("data", []):
                model_id = model_data.get("id", "")
                if not model_id:
                    continue
                capabilities = self._capabilities_for_model(model_id)
                models.append(
                    ModelInfo(
                        id=model_id,
                        name=model_data.get("id", ""),
                        capabilities=capabilities,
                        supported_reasoning_levels=(
                            ["low", "medium", "high"]
                            if capabilities.get("reasoning", False)
                            else []
                        ),
                        capability_provenance=inferred_remote_capability_provenance(
                            capabilities,
                            provider_label="OpenAI-compatible",
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
                ModelInfo(
                    id=self._default_model,
                    name=self._default_model,
                    capabilities=capabilities,
                    supported_reasoning_levels=(
                        ["low", "medium", "high"] if capabilities.get("reasoning", False) else []
                    ),
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

    def model_info(self) -> ModelInfo:
        capabilities = self._capabilities_for_model(self.active_model)
        return ModelInfo(
            id=self.active_model,
            name=self.active_model,
            capabilities=capabilities,
            supported_reasoning_levels=(
                ["low", "medium", "high"] if capabilities.get("reasoning", False) else []
            ),
            selection_state="active_remote_model",
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_request_body(
        self,
        messages: list[ModelMessage],
        stream: bool = False,
        **kwargs: object,
    ) -> dict:
        """Build the request body for /chat/completions."""
        model_name = str(kwargs.get("model", self.active_model) or self.active_model)
        self.set_active_model(model_name)
        body: dict = {
            "model": model_name,
            "messages": self._format_messages(messages),
            "stream": stream,
        }
        if "temperature" in kwargs:
            body["temperature"] = kwargs["temperature"]
        if "max_tokens" in kwargs:
            body["max_tokens"] = kwargs["max_tokens"]
        tool_definitions = kwargs.get("tools")
        if isinstance(tool_definitions, list) and tool_definitions:
            body["tools"] = [
                {
                    "type": "function",
                    "function": definition,
                }
                for definition in tool_definitions
                if isinstance(definition, dict)
            ]
        if "tool_choice" in kwargs:
            body["tool_choice"] = kwargs["tool_choice"]
        reasoning_effort = self._resolve_reasoning_effort(kwargs, model_name)
        if reasoning_effort is not None:
            body["reasoning_effort"] = reasoning_effort
        prompt_cache = kwargs.get("prompt_cache", self._options.get("prompt_cache"))
        if prompt_cache is not None:
            body["prompt_cache"] = self._coerce_prompt_cache(prompt_cache)
        prompt_cache_key = kwargs.get("prompt_cache_key", self._options.get("prompt_cache_key"))
        if isinstance(prompt_cache_key, str) and prompt_cache_key.strip():
            body["prompt_cache_key"] = prompt_cache_key.strip()
        return body

    def _resolve_reasoning_effort(
        self,
        kwargs: dict[str, object],
        model_name: str,
    ) -> str | None:
        if not self._capabilities_for_model(model_name).get("reasoning", False):
            return None
        raw_value = kwargs.get("reasoning_effort", self._options.get("reasoning_effort"))
        if not isinstance(raw_value, str):
            return None
        normalized = raw_value.strip().lower()
        if normalized in {"low", "medium", "high"}:
            return normalized
        return None

    @staticmethod
    def _coerce_prompt_cache(value: object) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        if isinstance(value, int | float):
            return bool(value)
        return False

    @staticmethod
    def _format_messages(messages: list[ModelMessage]) -> list[dict]:
        """Convert ModelMessage list to OpenAI message format."""
        formatted: list[dict] = []
        for message in messages:
            if message.role == "assistant" and message.metadata.get("tool_calls"):
                formatted.append(
                    {
                        "role": "assistant",
                        "content": message.content or None,
                        "tool_calls": message.metadata["tool_calls"],
                    }
                )
                continue
            if message.role == "tool":
                formatted.append(
                    {
                        "role": "tool",
                        "content": message.content,
                        "tool_call_id": message.metadata.get("tool_call_id", ""),
                    }
                )
                continue
            formatted.append({"role": message.role, "content": message.content})
        return formatted

    @staticmethod
    def _parse_sse_line(
        line: str,
        accumulator: _OpenAIToolCallAccumulator,
    ) -> list[StreamChunk]:
        """Parse a single SSE line into normalized stream chunks."""
        if not line or not line.startswith("data: "):
            return []
        data_str = line[6:]
        if data_str.strip() == "[DONE]":
            final_chunks = [
                StreamChunk(
                    kind="tool_call",
                    importance="secondary",
                    payload=tool_call,
                    tool_call_data=tool_call,
                )
                for tool_call in accumulator.finalized()
            ]
            final_chunks.append(StreamChunk(kind="done", is_done=True))
            return final_chunks
        try:
            data = json.loads(data_str)
        except json.JSONDecodeError:
            return []
        choices = data.get("choices", [])
        if not choices:
            return []
        delta = choices[0].get("delta", {})
        chunks: list[StreamChunk] = []

        content = delta.get("content", "")
        if isinstance(content, str) and content:
            chunks.append(StreamChunk(text=content, kind="final_text"))

        for reasoning in OpenAIProvider._extract_delta_reasoning(delta):
            chunks.append(
                StreamChunk(
                    text=reasoning,
                    kind="reasoning",
                    importance="secondary",
                )
            )

        refusal = delta.get("refusal", "")
        if isinstance(refusal, str) and refusal:
            chunks.append(
                StreamChunk(
                    text=refusal,
                    kind="notification",
                    importance="secondary",
                    payload={"source": "refusal"},
                )
            )

        tool_calls = delta.get("tool_calls", [])
        if isinstance(tool_calls, list) and tool_calls:
            chunks.extend(accumulator.ingest(tool_calls))

        finish_reason = choices[0].get("finish_reason", "")
        if finish_reason:
            chunks.extend(
                StreamChunk(
                    kind="tool_call",
                    importance="secondary",
                    payload=tool_call,
                    tool_call_data=tool_call,
                )
                for tool_call in accumulator.finalized()
            )
            usage = data.get("usage")
            chunks.append(
                StreamChunk(
                    kind="done",
                    is_done=True,
                    usage=usage if isinstance(usage, dict) else None,
                    payload={"finish_reason": finish_reason},
                )
            )
        return chunks

    @staticmethod
    def _extract_delta_reasoning(delta: dict) -> list[str]:
        values: list[str] = []
        for key in ("reasoning", "reasoning_content", "thinking"):
            raw = delta.get(key)
            if isinstance(raw, str) and raw:
                values.append(raw)
            elif isinstance(raw, list):
                for item in raw:
                    if isinstance(item, str) and item:
                        values.append(item)
                    elif isinstance(item, dict):
                        text = item.get("text") or item.get("content")
                        if isinstance(text, str) and text:
                            values.append(text)
        return values

    @staticmethod
    def _extract_message_text(message: dict) -> str:
        content = message.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    text = item.get("text") or item.get("content")
                    if isinstance(text, str):
                        parts.append(text)
            return "".join(parts)
        return ""

    @classmethod
    def _extract_message_reasoning(cls, message: dict) -> str:
        parts: list[str] = []
        for key in ("reasoning", "reasoning_content", "thinking"):
            raw = message.get(key)
            if isinstance(raw, str) and raw:
                parts.append(raw)
            elif isinstance(raw, list):
                for item in raw:
                    if isinstance(item, str):
                        parts.append(item)
                    elif isinstance(item, dict):
                        text = item.get("text") or item.get("content")
                        if isinstance(text, str):
                            parts.append(text)
        return "".join(parts)

    @staticmethod
    def _capabilities_for_model(model_id: str) -> dict:
        lowered = model_id.lower()
        return {
            "tool_use": not any(
                token in lowered
                for token in (
                    "embedding",
                    "whisper",
                    "tts",
                    "transcribe",
                    "moderation",
                    "image",
                    "rerank",
                )
            ),
            "reasoning": lowered.startswith(("o1", "o3", "o4")) or lowered.startswith("gpt-5"),
            "streaming": True,
        }
