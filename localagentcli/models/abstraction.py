"""ModelAbstractionLayer — unified interface hiding backend differences."""

from __future__ import annotations

from typing import Iterator

from localagentcli.models.backends.base import (
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
        yield from self._backend.stream_generate(messages, **kwargs)

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
