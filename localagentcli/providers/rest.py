"""GenericRESTProvider — configurable REST endpoint provider."""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Iterator

import httpx

from localagentcli.models.backends.base import (
    GenerationResult,
    ModelMessage,
    StreamChunk,
)
from localagentcli.models.readiness import (
    configured_remote_capability_provenance,
    legacy_fallback_capability_provenance,
)
from localagentcli.providers.base import (
    ConnectionTestResult,
    RemoteModelInfo,
    RemoteProvider,
)

logger = logging.getLogger(__name__)

DEFAULT_REQUEST_MAPPING: dict[str, str] = {
    "messages_field": "messages",
    "model_field": "model",
    "stream_field": "stream",
}

DEFAULT_RESPONSE_MAPPING: dict[str, str] = {
    "content_field": "choices[0].message.content",
    "stream_content_field": "choices[0].delta.content",
    "reasoning_field": "",
    "tool_calls_field": "",
    "stream_reasoning_field": "",
    "stream_tool_calls_field": "",
}


class GenericRESTProvider(RemoteProvider):
    """Provider for arbitrary REST APIs with configurable field mappings."""

    def __init__(
        self,
        name: str,
        base_url: str,
        api_key: str,
        default_model: str,
        options: dict | None = None,
    ):
        super().__init__(name, base_url, api_key, default_model, options)
        self._request_mapping: dict[str, str] = self._options.get(
            "request_mapping", DEFAULT_REQUEST_MAPPING
        )
        self._response_mapping: dict[str, str] = self._options.get(
            "response_mapping", DEFAULT_RESPONSE_MAPPING
        )
        self._endpoint: str = self._options.get("endpoint", "/chat/completions")
        self._models_endpoint: str = self._options.get("models_endpoint", "/models")
        self._models_field: str = self._options.get("models_field", "data")
        self._model_id_field: str = self._options.get("model_id_field", "id")
        self._model_name_field: str = self._options.get("model_name_field", "id")

        timeout = self._options.get("timeout", 30)
        headers: dict[str, str] = {"Authorization": f"Bearer {self._api_key}"}
        custom_headers = self._options.get("custom_headers", {})
        if isinstance(custom_headers, dict):
            headers.update(custom_headers)
        self._client = httpx.Client(
            base_url=self._base_url,
            headers=headers,
            timeout=timeout,
        )

    def generate(self, messages: list[ModelMessage], **kwargs: object) -> GenerationResult:
        """Send request using configured mapping."""
        body = self._build_request_body(messages, stream=False, **kwargs)
        try:
            response = self._request_with_retries(
                lambda: self._client.post(self._endpoint, json=body)
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            return GenerationResult(text="", finish_reason="error", usage={"error": str(e)})
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            return GenerationResult(text="", finish_reason="error", usage={"error": str(e)})

        data = response.json()
        content_field = self._response_mapping.get("content_field", "choices[0].message.content")
        reasoning_field = self._response_mapping.get("reasoning_field", "")
        tool_calls_field = self._response_mapping.get("tool_calls_field", "")
        text = extract_field(data, content_field)
        reasoning = extract_field(data, reasoning_field) if reasoning_field else None
        tool_calls = extract_field(data, tool_calls_field) if tool_calls_field else []
        return GenerationResult(
            text=str(text) if text is not None else "",
            reasoning=str(reasoning) if isinstance(reasoning, str) else "",
            tool_calls=tool_calls if isinstance(tool_calls, list) else [],
            finish_reason="stop",
        )

    def stream_generate(
        self, messages: list[ModelMessage], **kwargs: object
    ) -> Iterator[StreamChunk]:
        """Stream using configured endpoint and response mapping."""
        body = self._build_request_body(messages, stream=True, **kwargs)
        stream_field = self._response_mapping.get(
            "stream_content_field", "choices[0].delta.content"
        )
        reasoning_field = self._response_mapping.get("stream_reasoning_field", "")
        tool_calls_field = self._response_mapping.get("stream_tool_calls_field", "")
        context = None
        try:
            context, resp = self._open_stream_with_retries(
                lambda: self._client.stream("POST", self._endpoint, json=body)
            )
            resp.raise_for_status()
            for line in resp.iter_lines():
                for chunk in self._parse_sse_line(
                    line,
                    stream_field,
                    reasoning_field,
                    tool_calls_field,
                ):
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
        """Send a minimal request to the configured endpoint."""
        start = time.monotonic()
        try:
            body = self._build_request_body([ModelMessage(role="user", content="Hi")], stream=False)
            response = self._request_with_retries(
                lambda: self._client.post(self._endpoint, json=body)
            )
            response.raise_for_status()
            latency = (time.monotonic() - start) * 1000
            return ConnectionTestResult(
                success=True,
                message="Connected successfully.",
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
        """Try to discover models from a configured endpoint, then fall back."""
        try:
            response = self._request_with_retries(lambda: self._client.get(self._models_endpoint))
            response.raise_for_status()
            payload = response.json()
            raw_models = payload
            if not isinstance(raw_models, list):
                extracted = extract_field(payload, self._models_field)
                raw_models = extracted if isinstance(extracted, list) else []

            models: list[RemoteModelInfo] = []
            for raw_model in raw_models:
                if isinstance(raw_model, str):
                    model_id = raw_model
                    model_name = raw_model
                elif isinstance(raw_model, dict):
                    model_id = str(extract_field(raw_model, self._model_id_field) or "").strip()
                    model_name = str(
                        extract_field(raw_model, self._model_name_field) or model_id
                    ).strip()
                else:
                    continue

                if not model_id:
                    continue
                capabilities = self.capabilities()
                models.append(
                    RemoteModelInfo(
                        id=model_id,
                        name=model_name or model_id,
                        capabilities=capabilities,
                        capability_provenance=configured_remote_capability_provenance(capabilities),
                        selection_state="api_discovered",
                    )
                )
            if models:
                return models
        except Exception:
            logger.debug("Failed to list models from %s", self._name)

        if self._default_model:
            capabilities = self.capabilities()
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
        return bool(self.capabilities().get("tool_use", False))

    def supports_reasoning(self) -> bool:
        return bool(self.capabilities().get("reasoning", False))

    def supports_streaming(self) -> bool:
        return True

    def capabilities(self) -> dict:
        return {
            "tool_use": bool(self._options.get("supports_tools", False)),
            "reasoning": bool(self._options.get("supports_reasoning", False)),
            "streaming": True,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_request_body(
        self,
        messages: list[ModelMessage],
        stream: bool = False,
        **kwargs: object,
    ) -> dict:
        """Build request body using configured field mappings."""
        messages_field = self._request_mapping.get("messages_field", "messages")
        model_field = self._request_mapping.get("model_field", "model")
        stream_field = self._request_mapping.get("stream_field", "stream")

        body: dict = {
            model_field: kwargs.get("model", self.active_model),
            messages_field: [{"role": m.role, "content": m.content} for m in messages],
            stream_field: stream,
        }
        self.set_active_model(str(body[model_field]))
        return body

    @staticmethod
    def _parse_sse_line(
        line: str,
        content_field: str,
        reasoning_field: str,
        tool_calls_field: str,
    ) -> list[StreamChunk]:
        """Parse a single SSE line using the configured response mapping."""
        if not line or not line.startswith("data: "):
            return []
        data_str = line[6:]
        if data_str.strip() == "[DONE]":
            return [StreamChunk(kind="done", is_done=True)]
        try:
            data = json.loads(data_str)
        except json.JSONDecodeError:
            return []
        chunks: list[StreamChunk] = []
        content = extract_field(data, content_field)
        if content:
            chunks.append(StreamChunk(text=str(content), kind="final_text"))
        if reasoning_field:
            reasoning = extract_field(data, reasoning_field)
            if isinstance(reasoning, str) and reasoning:
                chunks.append(
                    StreamChunk(
                        text=reasoning,
                        kind="reasoning",
                        importance="secondary",
                    )
                )
        if tool_calls_field:
            tool_calls = extract_field(data, tool_calls_field)
            if isinstance(tool_calls, list):
                chunks.extend(
                    StreamChunk(
                        kind="tool_call",
                        importance="secondary",
                        payload=tool_call,
                        tool_call_data=tool_call if isinstance(tool_call, dict) else None,
                    )
                    for tool_call in tool_calls
                    if isinstance(tool_call, dict)
                )
        return chunks


def extract_field(data: Any, path: str) -> Any:
    """Navigate a dotted path with optional array indices.

    Example: "choices[0].message.content" navigates
    data["choices"][0]["message"]["content"].
    """
    # Split on dots, but keep bracket indices attached to their segment
    segments = path.split(".")
    current = data
    for segment in segments:
        # Check for array index: "choices[0]" -> key="choices", index=0
        match = re.match(r"^(\w+)\[(\d+)\]$", segment)
        if match:
            key, idx = match.group(1), int(match.group(2))
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return None
            if isinstance(current, list) and idx < len(current):
                current = current[idx]
            else:
                return None
        else:
            if isinstance(current, dict) and segment in current:
                current = current[segment]
            else:
                return None
    return current
