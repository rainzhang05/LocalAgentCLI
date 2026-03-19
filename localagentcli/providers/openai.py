"""OpenAIProvider — OpenAI Chat Completions API integration."""

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
        timeout = self._options.get("timeout", 30)
        self._client = httpx.Client(
            base_url=self._base_url,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )

    def generate(self, messages: list[ModelMessage], **kwargs: object) -> GenerationResult:
        """POST /chat/completions without streaming."""
        body = self._build_request_body(messages, stream=False, **kwargs)
        try:
            response = self._client.post("/chat/completions", json=body)
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
            text=message.get("content", ""),
            tool_calls=message.get("tool_calls", []),
            usage=usage,
            finish_reason=choice.get("finish_reason", ""),
        )

    def stream_generate(
        self, messages: list[ModelMessage], **kwargs: object
    ) -> Iterator[StreamChunk]:
        """POST /chat/completions with stream=True. Parse SSE."""
        body = self._build_request_body(messages, stream=True, **kwargs)
        try:
            with self._client.stream("POST", "/chat/completions", json=body) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    chunk = self._parse_sse_line(line)
                    if chunk is not None:
                        yield chunk
                        if chunk.is_done:
                            return
        except httpx.HTTPStatusError as e:
            yield StreamChunk(text=f"API error: {e.response.status_code}", is_done=True)
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            yield StreamChunk(text=f"Connection error: {e}", is_done=True)

    def test_connection(self) -> ConnectionTestResult:
        """GET /models to verify API key and connectivity."""
        start = time.monotonic()
        try:
            response = self._client.get("/models")
            response.raise_for_status()
            latency = (time.monotonic() - start) * 1000
            data = response.json()
            model_count = len(data.get("data", []))
            return ConnectionTestResult(
                success=True,
                message=f"Connected. {model_count} models available.",
                latency_ms=latency,
            )
        except httpx.HTTPStatusError as e:
            latency = (time.monotonic() - start) * 1000
            if e.response.status_code == 401:
                msg = "Authentication failed. Check your API key."
            else:
                msg = f"HTTP {e.response.status_code}: {e.response.text[:200]}"
            return ConnectionTestResult(success=False, message=msg, latency_ms=latency)
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            latency = (time.monotonic() - start) * 1000
            return ConnectionTestResult(
                success=False,
                message=f"Connection failed: {e}",
                latency_ms=latency,
            )

    def list_models(self) -> list[RemoteModelInfo]:
        """GET /models and parse the response."""
        try:
            response = self._client.get("/models")
            response.raise_for_status()
            data = response.json()
            return [
                RemoteModelInfo(
                    id=m["id"],
                    name=m.get("id", ""),
                    capabilities={
                        "tool_use": True,
                        "reasoning": False,
                        "streaming": True,
                    },
                )
                for m in data.get("data", [])
            ]
        except Exception:
            logger.debug("Failed to list models from %s", self._name)
            return []

    def supports_tools(self) -> bool:
        return True

    def supports_reasoning(self) -> bool:
        return False

    def supports_streaming(self) -> bool:
        return True

    def capabilities(self) -> dict:
        return {"tool_use": True, "reasoning": False, "streaming": True}

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
        body: dict = {
            "model": kwargs.get("model", self._default_model),
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
        return body

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
    def _parse_sse_line(line: str) -> StreamChunk | None:
        """Parse a single SSE line into a StreamChunk, or None to skip."""
        if not line or not line.startswith("data: "):
            return None
        data_str = line[6:]
        if data_str.strip() == "[DONE]":
            return StreamChunk(is_done=True)
        try:
            data = json.loads(data_str)
        except json.JSONDecodeError:
            return None
        choices = data.get("choices", [])
        if not choices:
            return None
        delta = choices[0].get("delta", {})
        content = delta.get("content", "")
        if content:
            return StreamChunk(text=content)
        # Check for finish reason on the final chunk
        if choices[0].get("finish_reason"):
            usage = data.get("usage")
            return StreamChunk(is_done=True, usage=usage)
        return None
