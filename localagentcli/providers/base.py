"""RemoteProvider ABC — base class for all remote API providers."""

from __future__ import annotations

import time
from abc import abstractmethod
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, TypeVar

import httpx

from localagentcli.models.backends.base import ModelBackend

_ResponseT = TypeVar("_ResponseT", bound=httpx.Response)


@dataclass
class ConnectionTestResult:
    """Result of a provider connectivity test."""

    success: bool
    message: str
    latency_ms: float = 0.0


@dataclass
class RemoteModelInfo:
    """Metadata about a model available from a remote provider."""

    id: str
    name: str
    capabilities: dict = field(default_factory=dict)
    capability_provenance: dict = field(default_factory=dict)
    selection_state: str = "api_discovered"


class RemoteProvider(ModelBackend):
    """Abstract base class for remote API providers.

    Extends ModelBackend with provider-specific methods (test_connection,
    list_models). Remote providers have no local model to load, so load(),
    unload(), and memory_usage() are concrete no-ops.
    """

    def __init__(
        self,
        name: str,
        base_url: str,
        api_key: str,
        default_model: str,
        options: dict | None = None,
    ):
        self._name = name
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._default_model = default_model
        self._active_model = default_model
        self._options = options or {}

    @property
    def name(self) -> str:
        """Provider name."""
        return self._name

    @property
    def base_url(self) -> str:
        """API base URL."""
        return self._base_url

    @property
    def default_model(self) -> str:
        """Default model identifier."""
        return self._default_model

    @property
    def active_model(self) -> str:
        """Return the model id currently bound to this provider instance."""
        return self._active_model or self._default_model

    def set_active_model(self, model_name: str | None) -> None:
        """Bind the provider instance to a specific remote model id."""
        self._active_model = (model_name or self._default_model).strip() or self._default_model

    # ------------------------------------------------------------------
    # ModelBackend no-ops (remote providers have no local model)
    # ------------------------------------------------------------------

    def load(self, model_path: Path, **kwargs: object) -> None:
        """No-op for remote providers."""

    def unload(self) -> None:
        """No-op for remote providers."""

    def memory_usage(self) -> int:
        """Remote providers use no local memory."""
        return 0

    def cancel(self) -> None:
        """No-op by default for remote providers."""

    def close(self) -> None:
        """Close the underlying HTTP client when present."""
        client = getattr(self, "_client", None)
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Abstract methods unique to remote providers
    # ------------------------------------------------------------------

    @abstractmethod
    def test_connection(self) -> ConnectionTestResult:
        """Test connectivity to the provider."""
        ...

    @abstractmethod
    def list_models(self) -> list[RemoteModelInfo]:
        """List available models from this provider."""
        ...

    # ------------------------------------------------------------------
    # Shared retry helpers
    # ------------------------------------------------------------------

    def _max_retry_attempts(self) -> int:
        retries = int(self._options.get("max_retries", 2))
        return max(1, retries + 1)

    def _should_retry_response(self, response: httpx.Response) -> bool:
        return response.status_code in {408, 409, 425, 429, 500, 502, 503, 504}

    def _retry_delay(self, attempt: int, response: httpx.Response | None = None) -> float:
        if response is not None:
            headers = getattr(response, "headers", {}) or {}
            retry_after = str(headers.get("Retry-After", "")).strip()
            if retry_after:
                try:
                    return max(0.0, min(float(retry_after), 5.0))
                except ValueError:
                    pass
        return min(0.2 * attempt, 1.0)

    def _sleep_before_retry(self, attempt: int, response: httpx.Response | None = None) -> None:
        delay = self._retry_delay(attempt, response)
        if delay > 0:
            time.sleep(delay)

    def _request_with_retries(
        self,
        request_factory: Callable[[], _ResponseT],
    ) -> _ResponseT:
        """Run a non-streaming request with bounded retries."""
        last_error: Exception | None = None
        for attempt in range(1, self._max_retry_attempts() + 1):
            try:
                response = request_factory()
            except (
                httpx.ConnectError,
                httpx.RemoteProtocolError,
                httpx.TimeoutException,
            ) as exc:
                last_error = exc
                if attempt >= self._max_retry_attempts():
                    raise
                self._sleep_before_retry(attempt)
                continue

            if self._should_retry_response(response) and attempt < self._max_retry_attempts():
                response.close()
                self._sleep_before_retry(attempt, response)
                continue
            return response

        if last_error is not None:
            raise last_error
        raise RuntimeError("Retry wrapper exhausted without a response")

    def _open_stream_with_retries(
        self,
        stream_factory: Callable[[], AbstractContextManager[httpx.Response]],
    ) -> tuple[AbstractContextManager[httpx.Response], httpx.Response]:
        """Open a streaming response with bounded retries before any data is read."""
        last_error: Exception | None = None
        for attempt in range(1, self._max_retry_attempts() + 1):
            context = stream_factory()
            try:
                response = context.__enter__()
            except (
                httpx.ConnectError,
                httpx.RemoteProtocolError,
                httpx.TimeoutException,
            ) as exc:
                last_error = exc
                if attempt >= self._max_retry_attempts():
                    raise
                self._sleep_before_retry(attempt)
                continue

            if self._should_retry_response(response) and attempt < self._max_retry_attempts():
                context.__exit__(None, None, None)
                self._sleep_before_retry(attempt, response)
                continue
            return context, response

        if last_error is not None:
            raise last_error
        raise RuntimeError("Retry wrapper exhausted without a streaming response")

    # ------------------------------------------------------------------
    # Inherited abstract methods from ModelBackend (must be implemented
    # by concrete subclasses): generate, stream_generate, supports_tools,
    # supports_reasoning, supports_streaming, capabilities
    # ------------------------------------------------------------------
