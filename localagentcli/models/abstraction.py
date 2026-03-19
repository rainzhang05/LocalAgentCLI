"""ModelAbstractionLayer — unified interface hiding backend differences."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from typing import Iterator

from localagentcli.models.backends.base import (
    EmbeddedStreamNormalizer,
    GenerationResult,
    ModelBackend,
    ModelMessage,
    StreamChunk,
    collect_generation_result,
)


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

    def supports_tools(self) -> bool:
        """Whether the backend supports tool/function calling."""
        return self._backend.supports_tools()

    def supports_reasoning(self) -> bool:
        """Whether the backend supports reasoning/thinking tokens."""
        return self._backend.supports_reasoning()

    def supports_streaming(self) -> bool:
        """Whether the backend supports streaming output."""
        return self._backend.supports_streaming()

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
