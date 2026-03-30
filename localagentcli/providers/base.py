"""RemoteProvider ABC — base class for all remote API providers."""

from __future__ import annotations

import asyncio
import time
from abc import abstractmethod
from collections.abc import AsyncIterator, Awaitable
from contextlib import AbstractAsyncContextManager, AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, TypeVar, cast

import httpx

from localagentcli.models.backends.base import (
    GenerationResult,
    ModelBackend,
    ModelMessage,
    StreamChunk,
)
from localagentcli.models.model_info import ModelInfo
from localagentcli.models.prompt_profile import ProviderPromptProfile

_ResponseT = TypeVar("_ResponseT", bound=httpx.Response)
_CONNECTION_POLICIES = {"reuse", "close_after_turn"}


def effective_model_request_timeout(
    provider_options: dict[str, Any] | None,
    global_model_response_seconds: float | int | None,
) -> float:
    """Resolve HTTP timeout: provider options.timeout overrides global model_response."""
    opts = provider_options or {}
    if opts.get("timeout") is not None:
        return float(opts["timeout"])
    if global_model_response_seconds is not None:
        return float(global_model_response_seconds)
    return 300.0


@dataclass
class ConnectionTestResult:
    """Result of a provider connectivity test."""

    success: bool
    message: str
    latency_ms: float = 0.0


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
        self._cancel_requested = False
        self._async_stream_response: httpx.Response | None = None
        self._async_stream_loop: asyncio.AbstractEventLoop | None = None

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

    def _request_timeout_value(self, kwargs: dict[str, object]) -> float:
        """HTTP client timeout from generation kwargs or provider options."""
        rt = kwargs.get("request_timeout")
        if rt is not None:
            return float(cast(Any, rt))
        return float(self._options.get("timeout", 30))

    def _stream_idle_timeout_value(self, kwargs: dict[str, object]) -> float:
        """Idle timeout between streamed lines; 0 disables idle-timeout enforcement."""
        value = kwargs.get("stream_idle_timeout")
        if value is not None:
            timeout = float(cast(Any, value))
            return timeout if timeout > 0 else 0.0
        configured = self._options.get("idle_stream_timeout")
        if configured is None:
            return 0.0
        timeout = float(configured)
        return timeout if timeout > 0 else 0.0

    def _connection_policy(self) -> str:
        """Provider async-client lifecycle policy."""
        policy = str(self._options.get("connection_policy", "reuse") or "reuse").strip()
        if policy not in _CONNECTION_POLICIES:
            return "reuse"
        return policy

    async def _maybe_close_async_client_after_turn(self) -> None:
        """Close async client after a turn when connection policy requires it."""
        if self._connection_policy() != "close_after_turn":
            return
        aclient = getattr(self, "_async_client", None)
        if aclient is None:
            return
        try:
            await aclient.aclose()
        except Exception:
            pass
        setattr(self, "_async_client", None)

    async def _aiter_lines_with_idle_timeout(
        self,
        response: httpx.Response,
        kwargs: dict[str, object],
    ) -> AsyncIterator[str]:
        """Yield streamed lines while enforcing optional idle timeout between lines."""
        idle_timeout = self._stream_idle_timeout_value(kwargs)
        if idle_timeout <= 0:
            async for line in response.aiter_lines():
                yield line
            return

        iterator = response.aiter_lines().__aiter__()
        while True:
            try:
                line = await asyncio.wait_for(iterator.__anext__(), timeout=idle_timeout)
            except StopAsyncIteration:
                break
            except TimeoutError as exc:
                raise TimeoutError(f"Stream idle timeout after {idle_timeout:.1f}s") from exc
            yield line

    def _track_async_stream(self, response: httpx.Response) -> None:
        self._async_stream_response = response
        try:
            self._async_stream_loop = asyncio.get_running_loop()
        except RuntimeError:
            self._async_stream_loop = None

    def _untrack_async_stream(self) -> None:
        self._async_stream_response = None
        self._async_stream_loop = None

    def _reset_cancel(self) -> None:
        self._cancel_requested = False

    def cancel(self) -> None:
        """Request cancellation of in-flight async streaming or generation."""
        self._cancel_requested = True
        resp = self._async_stream_response
        loop = self._async_stream_loop
        if resp is not None and loop is not None and loop.is_running():

            async def _close() -> None:
                try:
                    await resp.aclose()
                except Exception:
                    pass

            try:
                loop.call_soon_threadsafe(lambda: asyncio.ensure_future(_close(), loop=loop))
            except Exception:
                pass

    # ------------------------------------------------------------------
    # ModelBackend no-ops (remote providers have no local model)
    # ------------------------------------------------------------------

    def model_info(self) -> ModelInfo:
        """Return a generalized ModelInfo for the active remote model."""
        return ModelInfo(
            id=self.active_model,
            name=self.active_model,
            selection_state="active_remote_model",
            capabilities={},
        )

    def prompt_profile(self) -> ProviderPromptProfile:
        """Provider-aware prompt assembly preferences for this backend."""
        return ProviderPromptProfile(provider_kind="generic")

    def load(self, model_path: Path, **kwargs: object) -> None:
        """No-op for remote providers."""

    def unload(self) -> None:
        """No-op for remote providers."""

    def memory_usage(self) -> int:
        """Remote providers use no local memory."""
        return 0

    def close(self) -> None:
        """Close the underlying sync HTTP client when present."""
        self.cancel()
        client = getattr(self, "_client", None)
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
        self._close_async_client_sync_best_effort()

    def _close_async_client_sync_best_effort(self) -> None:
        """When no event loop is running, close AsyncClient synchronously."""
        ac = getattr(self, "_async_client", None)
        if ac is None:
            return
        try:
            asyncio.get_running_loop()
            return
        except RuntimeError:
            pass
        try:
            asyncio.run(ac.aclose())
        except Exception:
            pass
        setattr(self, "_async_client", None)

    async def aclose(self) -> None:
        """Close async HTTP resources."""
        self.cancel()
        aclient = getattr(self, "_async_client", None)
        if aclient is not None:
            try:
                await aclient.aclose()
            except Exception:
                pass
            setattr(self, "_async_client", None)

    # ------------------------------------------------------------------
    # Abstract methods unique to remote providers
    # ------------------------------------------------------------------

    @abstractmethod
    def test_connection(self) -> ConnectionTestResult:
        """Test connectivity to the provider."""
        ...

    @abstractmethod
    def list_models(self) -> list[ModelInfo]:
        """List available models from this provider."""
        ...

    @abstractmethod
    async def atest_connection(self) -> ConnectionTestResult:
        """Async connectivity test."""
        ...

    @abstractmethod
    async def alist_models(self) -> list[ModelInfo]:
        """Async model listing."""
        ...

    @abstractmethod
    async def agenerate(self, messages: list[ModelMessage], **kwargs: object) -> GenerationResult:
        """Async non-streaming generation."""
        ...

    @abstractmethod
    async def astream_generate(
        self, messages: list[ModelMessage], **kwargs: object
    ) -> AsyncIterator[StreamChunk]:
        """Async streaming generation."""
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

    async def _async_sleep_before_retry(
        self, attempt: int, response: httpx.Response | None = None
    ) -> None:
        delay = self._retry_delay(attempt, response)
        if delay > 0:
            await asyncio.sleep(delay)

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

    async def _arequest_with_retries(
        self,
        request_factory: Callable[[], Awaitable[httpx.Response]],
    ) -> httpx.Response:
        """Async non-streaming request with bounded retries."""
        last_error: Exception | None = None
        for attempt in range(1, self._max_retry_attempts() + 1):
            try:
                response = await request_factory()
            except (
                httpx.ConnectError,
                httpx.RemoteProtocolError,
                httpx.TimeoutException,
            ) as exc:
                last_error = exc
                if attempt >= self._max_retry_attempts():
                    raise
                await self._async_sleep_before_retry(attempt)
                continue

            if self._should_retry_response(response) and attempt < self._max_retry_attempts():
                await response.aclose()
                await self._async_sleep_before_retry(attempt, response)
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

    async def _aopen_stream_with_retries(
        self,
        stream_factory: Callable[[], AbstractAsyncContextManager[httpx.Response]],
    ) -> tuple[AbstractAsyncContextManager[httpx.Response], httpx.Response]:
        """Open an async streaming response with bounded retries."""
        last_error: Exception | None = None
        for attempt in range(1, self._max_retry_attempts() + 1):
            context = stream_factory()
            try:
                response = await context.__aenter__()
            except (
                httpx.ConnectError,
                httpx.RemoteProtocolError,
                httpx.TimeoutException,
            ) as exc:
                last_error = exc
                if attempt >= self._max_retry_attempts():
                    raise
                await self._async_sleep_before_retry(attempt)
                continue

            if self._should_retry_response(response) and attempt < self._max_retry_attempts():
                await context.__aexit__(None, None, None)
                await self._async_sleep_before_retry(attempt, response)
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
