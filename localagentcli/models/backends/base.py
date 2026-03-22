"""ModelBackend ABC — unified interface for all model backends."""

from __future__ import annotations

import importlib
import re
import subprocess
import sys
from abc import ABC, abstractmethod
from collections.abc import AsyncIterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Iterator, Literal

BACKEND_DEPENDENCIES: dict[str, list[str]] = {
    "mlx": ["mlx", "mlx_lm"],
    "gguf": ["llama_cpp"],
    "safetensors": ["torch", "transformers", "safetensors"],
}

BACKEND_REQUIREMENTS: dict[str, list[str]] = {
    "mlx": ["mlx>=0.5", "mlx-lm>=0.5"],
    "gguf": ["llama-cpp-python>=0.2"],
    "safetensors": ["torch>=2.0", "transformers>=4.35", "safetensors>=0.4"],
}

BACKEND_EXTRAS: dict[str, str] = {
    "mlx": "mlx",
    "gguf": "gguf",
    "safetensors": "torch",
}

BACKEND_LABELS: dict[str, str] = {
    "mlx": "MLX",
    "gguf": "GGUF",
    "safetensors": "Safetensors",
}

_CONTROL_TOKEN_PATTERN = re.compile(r"<\|([a-zA-Z_]+)\|>")


@dataclass
class StreamChunk:
    """A single chunk of streaming model output."""

    text: str = ""
    kind: Literal["final_text", "reasoning", "tool_call", "notification", "error", "done"] = (
        "final_text"
    )
    importance: Literal["primary", "secondary"] = "primary"
    transient: bool = False
    payload: dict | None = None
    is_reasoning: bool = False
    is_tool_call: bool = False
    tool_call_data: dict | None = None
    is_done: bool = False
    usage: dict | None = None

    def __post_init__(self) -> None:
        """Keep legacy boolean flags aligned with the normalized chunk kind."""
        if self.is_reasoning:
            self.kind = "reasoning"
        elif self.is_tool_call:
            self.kind = "tool_call"
        elif self.is_done:
            self.kind = "done"

        if self.kind == "reasoning":
            self.is_reasoning = True
            if self.importance == "primary":
                self.importance = "secondary"
        elif self.kind == "tool_call":
            self.is_tool_call = True
            if self.importance == "primary":
                self.importance = "secondary"
            if self.tool_call_data is None and isinstance(self.payload, dict):
                self.tool_call_data = self.payload
        elif self.kind == "done":
            self.is_done = True

        if self.payload is None and self.tool_call_data is not None:
            self.payload = self.tool_call_data

    def to_dict(self) -> dict[str, object]:
        """Serialize the chunk for session metadata and tests."""
        return {
            "text": self.text,
            "kind": self.kind,
            "importance": self.importance,
            "transient": self.transient,
            "payload": self.payload,
            "is_reasoning": self.is_reasoning,
            "is_tool_call": self.is_tool_call,
            "tool_call_data": self.tool_call_data,
            "is_done": self.is_done,
            "usage": self.usage,
        }


@dataclass
class GenerationResult:
    """Complete result of a non-streaming generation."""

    text: str
    reasoning: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    finish_reason: str = ""
    chunks: list[StreamChunk] = field(default_factory=list)


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

    def cancel(self) -> None:
        """Cancel an in-flight generation if the backend supports it."""
        return None

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


class EmbeddedStreamNormalizer:
    """Normalize in-band channel markup emitted inside plain text streams."""

    def __init__(self) -> None:
        self._buffer = ""
        self._active_channel: str | None = None
        self._in_message = False

    def feed(self, chunk: StreamChunk) -> list[StreamChunk]:
        """Normalize one streaming chunk."""
        if chunk.kind != "final_text" or not chunk.text:
            return [chunk]
        self._buffer += chunk.text
        return self._drain(final=False)

    def flush(self) -> list[StreamChunk]:
        """Flush any buffered text at stream end."""
        return self._drain(final=True)

    def _drain(self, *, final: bool) -> list[StreamChunk]:
        chunks: list[StreamChunk] = []

        while True:
            match = _CONTROL_TOKEN_PATTERN.search(self._buffer)
            if match is None:
                text, self._buffer = self._split_safe_text(final=final)
                chunks.extend(self._emit_text(text))
                break

            if match.start() > 0:
                prefix = self._buffer[: match.start()]
                self._buffer = self._buffer[match.start() :]
                chunks.extend(self._emit_text(prefix))
                continue

            token = match.group(1).lower()
            remainder = self._buffer[match.end() :]
            if token in {"channel", "start"}:
                next_match = _CONTROL_TOKEN_PATTERN.search(remainder)
                if next_match is None:
                    if not final:
                        break
                    value = remainder
                    self._buffer = ""
                else:
                    value = remainder[: next_match.start()]
                    self._buffer = remainder[next_match.start() :]
                cleaned = value.strip()
                if token == "channel":
                    self._active_channel = cleaned.lower() or None
                continue

            self._buffer = remainder
            if token == "message":
                self._in_message = True
                continue
            if token == "end":
                self._active_channel = None
                self._in_message = False
                continue

        return chunks

    def _split_safe_text(self, *, final: bool) -> tuple[str, str]:
        """Split out safe plain text while preserving partial control tokens."""
        if final:
            return self._buffer, ""
        partial_start = self._buffer.rfind("<|")
        if partial_start == -1:
            return self._buffer, ""
        partial = self._buffer[partial_start:]
        if "|>" in partial:
            return self._buffer, ""
        return self._buffer[:partial_start], partial

    def _emit_text(self, text: str) -> list[StreamChunk]:
        """Convert buffered text into normalized chunks."""
        if not text:
            return []
        kind, importance = self._chunk_style()
        return [StreamChunk(text=text, kind=kind, importance=importance)]

    def _chunk_style(
        self,
    ) -> tuple[
        Literal["final_text", "reasoning", "tool_call", "notification", "error", "done"],
        Literal["primary", "secondary"],
    ]:
        if not (self._in_message and self._active_channel):
            return "final_text", "primary"

        channel = self._active_channel
        if channel in {"analysis", "reasoning", "thinking"}:
            return "reasoning", "secondary"
        if channel == "final":
            return "final_text", "primary"
        if channel in {"commentary", "notification", "notifier"}:
            return "notification", "primary"
        if channel in {"tool", "tool_call", "function_call"}:
            return "tool_call", "secondary"
        return "notification", "secondary"


def collect_generation_result(chunks: Iterable[StreamChunk]) -> GenerationResult:
    """Collect normalized stream chunks into a GenerationResult."""
    ordered_chunks: list[StreamChunk] = []
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_calls: list[dict] = []
    usage: dict = {}
    finish_reason = ""
    error_messages: list[str] = []

    for chunk in chunks:
        ordered_chunks.append(chunk)

        if chunk.kind == "final_text" and chunk.text:
            text_parts.append(chunk.text)
            continue
        if chunk.kind == "reasoning" and chunk.text:
            reasoning_parts.append(chunk.text)
            continue
        if chunk.kind == "tool_call":
            payload = chunk.tool_call_data or chunk.payload
            if isinstance(payload, dict):
                tool_calls.append(payload)
            continue
        if chunk.kind == "error" and chunk.text:
            error_messages.append(chunk.text)
            continue
        if chunk.kind == "done":
            if isinstance(chunk.usage, dict):
                usage.update(chunk.usage)
            payload = chunk.payload or {}
            if isinstance(payload, dict):
                finish_reason = str(payload.get("finish_reason", finish_reason) or finish_reason)

    if error_messages and "error" not in usage:
        usage["error"] = "\n".join(error_messages)
    if usage.get("error") and not finish_reason:
        finish_reason = "error"

    return GenerationResult(
        text="".join(text_parts),
        reasoning="".join(reasoning_parts),
        tool_calls=tool_calls,
        usage=usage,
        finish_reason=finish_reason,
        chunks=ordered_chunks,
    )


async def acollect_generation_result(chunks: AsyncIterable[StreamChunk]) -> GenerationResult:
    """Collect chunks from an async stream into a GenerationResult."""
    buffered: list[StreamChunk] = []
    async for chunk in chunks:
        buffered.append(chunk)
    return collect_generation_result(iter(buffered))


def backend_label(backend: str) -> str:
    """Return a user-facing label for a backend key."""
    return BACKEND_LABELS.get(backend, backend)


def backend_extra_name(backend: str) -> str:
    """Return the optional dependency extra name for a backend."""
    try:
        return BACKEND_EXTRAS[backend]
    except KeyError as exc:
        raise ValueError(f"Unknown backend: {backend}") from exc


def backend_install_hint(backend: str) -> str:
    """Return the installation hint for a backend's optional dependencies."""
    return f"pip install localagentcli[{backend_extra_name(backend)}]"


def backend_requirement_specs(backend: str) -> list[str]:
    """Return pinned requirement specifiers for a backend's optional dependencies."""
    try:
        return list(BACKEND_REQUIREMENTS[backend])
    except KeyError as exc:
        raise ValueError(f"Unknown backend: {backend}") from exc


def backend_requirement_names(backend: str) -> list[str]:
    """Return human-friendly package names for a backend's optional dependencies."""
    names: list[str] = []
    for requirement in backend_requirement_specs(backend):
        name = re.split(r"[<>=!~]", requirement, maxsplit=1)[0].strip()
        names.append(name)
    return names


def check_backend_dependencies(backend: str) -> tuple[bool, list[str]]:
    """Check whether the optional dependencies for a backend are installed."""
    importlib.invalidate_caches()
    missing: list[str] = []
    for module_name in BACKEND_DEPENDENCIES.get(backend, []):
        try:
            importlib.import_module(module_name)
        except ImportError:
            missing.append(module_name)
    return len(missing) == 0, missing


def install_backend_dependencies(
    backend: str,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> tuple[bool, str]:
    """Install the optional dependencies for a backend using pip."""
    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        *backend_requirement_specs(backend),
    ]
    run = runner or subprocess.run
    try:
        completed = run(
            command,
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)

    if completed.returncode != 0:
        output = completed.stderr.strip() or completed.stdout.strip()
        return False, output or "pip install failed."

    installed, missing = check_backend_dependencies(backend)
    if not installed:
        return (
            False,
            f"Installation completed, but these modules are still missing: {', '.join(missing)}",
        )

    output = completed.stdout.strip() or completed.stderr.strip()
    return True, output or f"Installed {backend_label(backend)} backend dependencies."
