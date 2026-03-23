"""ModelAbstractionLayer — unified interface hiding backend differences."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator, Iterator
from contextlib import redirect_stderr, redirect_stdout
from typing import Any, cast

from localagentcli.models.backends.base import (
    EmbeddedStreamNormalizer,
    GenerationResult,
    ModelBackend,
    ModelMessage,
    StreamChunk,
    acollect_generation_result,
    collect_generation_result,
)
from localagentcli.models.model_info import ModelInfo
from localagentcli.providers.base import RemoteProvider


class ModelAbstractionLayer:
    """Wraps a ModelBackend, providing a stable public API."""

    def __init__(self, backend: ModelBackend):
        self._backend = backend

    @property
    def backend(self) -> ModelBackend:
        """The underlying backend instance."""
        return self._backend

    def generate(self, messages: list[ModelMessage], **kwargs: object) -> GenerationResult:
        """Generate a complete response from the normalized streaming pipeline."""
        return collect_generation_result(self.stream_generate(messages, **kwargs))

    def stream_generate(
        self, messages: list[ModelMessage], **kwargs: object
    ) -> Iterator[StreamChunk]:
        """Generate a streaming response."""
        capture = _CapturedOutput()
        normalizer = EmbeddedStreamNormalizer()

        with redirect_stdout(capture), redirect_stderr(capture):
            for raw_chunk in self._backend.stream_generate(messages, **kwargs):
                yield from self._drain_captured_output(capture)
                if raw_chunk.is_done:
                    yield from normalizer.flush()
                    yield raw_chunk
                    continue
                yield from normalizer.feed(raw_chunk)

        yield from self._drain_captured_output(capture, final=True)
        yield from normalizer.flush()

    async def agenerate(self, messages: list[ModelMessage], **kwargs: object) -> GenerationResult:
        """Async complete response from the normalized streaming pipeline."""
        return await acollect_generation_result(self.astream_generate(messages, **kwargs))

    async def astream_generate(
        self, messages: list[ModelMessage], **kwargs: object
    ) -> AsyncIterator[StreamChunk]:
        """Async streaming response (native for remote, thread-bridged for local)."""
        if isinstance(self._backend, RemoteProvider):
            async for chunk in self._astream_remote(messages, **kwargs):
                yield chunk
        else:
            async for chunk in self._astream_local_threaded(messages, **kwargs):
                yield chunk

    async def _astream_remote(
        self, messages: list[ModelMessage], **kwargs: object
    ) -> AsyncIterator[StreamChunk]:
        capture = _CapturedOutput()
        normalizer = EmbeddedStreamNormalizer()
        remote = self._backend
        assert isinstance(remote, RemoteProvider)
        with redirect_stdout(capture), redirect_stderr(capture):
            remote_stream = cast(
                AsyncIterator[StreamChunk],
                remote.astream_generate(messages, **kwargs),
            )
            async for raw_chunk in remote_stream:
                for line in self._drain_captured_output(capture):
                    yield line
                if raw_chunk.is_done:
                    for part in normalizer.flush():
                        yield part
                    yield raw_chunk
                    continue
                for part in normalizer.feed(raw_chunk):
                    yield part
        for line in self._drain_captured_output(capture, final=True):
            yield line
        for part in normalizer.flush():
            yield part

    async def _astream_local_threaded(
        self, messages: list[ModelMessage], **kwargs: object
    ) -> AsyncIterator[StreamChunk]:
        """Run sync local stream_generate in a worker thread; never block the event loop."""
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue(maxsize=512)
        error_holder: list[BaseException] = []

        def worker() -> None:
            capture = _CapturedOutput()
            try:
                with redirect_stdout(capture), redirect_stderr(capture):
                    for raw_chunk in self._backend.stream_generate(messages, **kwargs):
                        for line in capture.drain():
                            fut = asyncio.run_coroutine_threadsafe(
                                queue.put(("notify", line)), loop
                            )
                            fut.result(timeout=3600)
                        fut = asyncio.run_coroutine_threadsafe(
                            queue.put(("chunk", raw_chunk)), loop
                        )
                        fut.result(timeout=3600)
                    for line in capture.drain(final=True):
                        fut = asyncio.run_coroutine_threadsafe(queue.put(("notify", line)), loop)
                        fut.result(timeout=3600)
            except BaseException as exc:
                error_holder.append(exc)
            finally:
                asyncio.run_coroutine_threadsafe(queue.put(("end", None)), loop).result(timeout=60)

        thread = threading.Thread(target=worker, name="localagentcli-model-stream", daemon=True)
        thread.start()
        normalizer = EmbeddedStreamNormalizer()
        try:
            while True:
                kind, payload = await queue.get()
                if kind == "end":
                    if error_holder:
                        raise error_holder[0]
                    break
                if kind == "notify":
                    yield StreamChunk(text=str(payload), kind="notification", importance="primary")
                    continue
                if kind == "chunk":
                    raw_chunk = payload
                    assert isinstance(raw_chunk, StreamChunk)
                    if raw_chunk.is_done:
                        for part in normalizer.flush():
                            yield part
                        yield raw_chunk
                        continue
                    for part in normalizer.feed(raw_chunk):
                        yield part
            for part in normalizer.flush():
                yield part
        finally:
            self._backend.cancel()
            thread.join(timeout=30.0)

    def supports_tools(self) -> bool:
        """Whether the backend supports tool/function calling."""
        return self._backend.supports_tools()

    def supports_reasoning(self) -> bool:
        """Whether the backend supports reasoning/thinking tokens."""
        return self._backend.supports_reasoning()

    def supports_streaming(self) -> bool:
        """Whether the backend supports streaming output."""
        return self._backend.supports_streaming()

    def model_info(self) -> ModelInfo:
        """Normalized metadata about model capabilities and limits."""
        return self._backend.model_info()

    def cancel(self) -> None:
        """Cancel the active generation, if supported by the backend."""
        self._backend.cancel()

    def _drain_captured_output(
        self,
        capture: "_CapturedOutput",
        *,
        final: bool = False,
    ) -> Iterator[StreamChunk]:
        """Convert captured backend stdout/stderr lines into notification chunks."""
        for line in capture.drain(final=final):
            yield StreamChunk(text=line, kind="notification", importance="primary")


class _CapturedOutput:
    """Capture backend stdout/stderr so it can be rendered separately."""

    def __init__(self) -> None:
        self._partial = ""
        self._lines: list[str] = []

    def write(self, text: str) -> int:
        self._partial += text
        while "\n" in self._partial:
            line, self._partial = self._partial.split("\n", 1)
            cleaned = line.strip()
            if cleaned:
                self._lines.append(cleaned)
        return len(text)

    def flush(self) -> None:
        return None

    def isatty(self) -> bool:
        return False

    def drain(self, *, final: bool = False) -> list[str]:
        """Drain any captured complete lines."""
        if final:
            cleaned = self._partial.strip()
            if cleaned:
                self._lines.append(cleaned)
            self._partial = ""
        lines = list(self._lines)
        self._lines.clear()
        return lines
