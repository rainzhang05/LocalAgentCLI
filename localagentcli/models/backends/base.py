"""ModelBackend ABC — unified interface for all model backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator


@dataclass
class StreamChunk:
    """A single chunk of streaming model output."""

    text: str = ""
    is_reasoning: bool = False
    is_tool_call: bool = False
    tool_call_data: dict | None = None
    is_done: bool = False
    usage: dict | None = None


@dataclass
class GenerationResult:
    """Complete result of a non-streaming generation."""

    text: str
    reasoning: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    finish_reason: str = ""


@dataclass
class ModelMessage:
    """A message in the conversation for model API calls.

    This is a lightweight version for model calls, distinct from
    localagentcli.session.state.Message which has timestamp and is_summary.
    """

    role: str  # "user" | "assistant" | "system" | "tool"
    content: str
    metadata: dict = field(default_factory=dict)


class ModelBackend(ABC):
    """Abstract base class for all model backends (local and remote)."""

    @abstractmethod
    def load(self, model_path: Path, **kwargs: object) -> None:
        """Load the model into memory."""
        ...

    @abstractmethod
    def unload(self) -> None:
        """Unload the model and free memory."""
        ...

    @abstractmethod
    def generate(self, messages: list[ModelMessage], **kwargs: object) -> GenerationResult:
        """Generate a complete response."""
        ...

    @abstractmethod
    def stream_generate(
        self, messages: list[ModelMessage], **kwargs: object
    ) -> Iterator[StreamChunk]:
        """Generate a streaming response."""
        ...

    @abstractmethod
    def supports_tools(self) -> bool:
        """Whether this backend supports tool/function calling."""
        ...

    @abstractmethod
    def supports_reasoning(self) -> bool:
        """Whether this backend supports reasoning/thinking tokens."""
        ...

    @abstractmethod
    def supports_streaming(self) -> bool:
        """Whether this backend supports streaming output."""
        ...

    @abstractmethod
    def memory_usage(self) -> int:
        """Return current memory usage in bytes."""
        ...

    @abstractmethod
    def capabilities(self) -> dict:
        """Return a dict of all capability flags."""
        ...
