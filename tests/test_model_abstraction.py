"""Tests for model abstraction layer — StreamChunk, GenerationResult,
ModelBackend, ModelAbstractionLayer."""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest

from localagentcli.models.abstraction import ModelAbstractionLayer
from localagentcli.models.backends.base import (
    GenerationResult,
    ModelBackend,
    ModelMessage,
    StreamChunk,
)

# ---------------------------------------------------------------------------
# StreamChunk tests
# ---------------------------------------------------------------------------


class TestStreamChunk:
    def test_defaults(self):
        chunk = StreamChunk()
        assert chunk.text == ""
        assert chunk.kind == "final_text"
        assert chunk.importance == "primary"
        assert chunk.transient is False
        assert chunk.payload is None
        assert chunk.is_reasoning is False
        assert chunk.is_tool_call is False
        assert chunk.tool_call_data is None
        assert chunk.is_done is False
        assert chunk.usage is None

    def test_with_text(self):
        chunk = StreamChunk(text="Hello")
        assert chunk.text == "Hello"

    def test_reasoning_chunk(self):
        chunk = StreamChunk(text="thinking...", is_reasoning=True)
        assert chunk.is_reasoning is True
        assert chunk.kind == "reasoning"

    def test_tool_call_chunk(self):
        data = {"name": "file_read", "arguments": {"path": "/tmp"}}
        chunk = StreamChunk(is_tool_call=True, tool_call_data=data)
        assert chunk.is_tool_call is True
        assert chunk.tool_call_data == data
        assert chunk.kind == "tool_call"

    def test_done_chunk_with_usage(self):
        usage = {"prompt_tokens": 10, "completion_tokens": 20}
        chunk = StreamChunk(is_done=True, usage=usage)
        assert chunk.is_done is True
        assert chunk.usage == usage
        assert chunk.kind == "done"


# ---------------------------------------------------------------------------
# GenerationResult tests
# ---------------------------------------------------------------------------


class TestGenerationResult:
    def test_minimal(self):
        result = GenerationResult(text="Hello world")
        assert result.text == "Hello world"
        assert result.reasoning == ""
        assert result.tool_calls == []
        assert result.usage == {}
        assert result.finish_reason == ""

    def test_full(self):
        result = GenerationResult(
            text="response",
            reasoning="thought process",
            tool_calls=[{"name": "test"}],
            usage={"prompt_tokens": 5},
            finish_reason="stop",
        )
        assert result.text == "response"
        assert result.reasoning == "thought process"
        assert result.tool_calls == [{"name": "test"}]
        assert result.usage == {"prompt_tokens": 5}
        assert result.finish_reason == "stop"

    def test_tool_calls_independent_instances(self):
        r1 = GenerationResult(text="a")
        r2 = GenerationResult(text="b")
        r1.tool_calls.append({"x": 1})
        assert r2.tool_calls == []


# ---------------------------------------------------------------------------
# ModelMessage tests
# ---------------------------------------------------------------------------


class TestModelMessage:
    def test_basic(self):
        msg = ModelMessage(role="user", content="Hi")
        assert msg.role == "user"
        assert msg.content == "Hi"
        assert msg.metadata == {}

    def test_with_metadata(self):
        msg = ModelMessage(role="assistant", content="Hello", metadata={"k": "v"})
        assert msg.metadata == {"k": "v"}

    def test_metadata_independent(self):
        m1 = ModelMessage(role="user", content="a")
        m2 = ModelMessage(role="user", content="b")
        m1.metadata["x"] = 1
        assert "x" not in m2.metadata


# ---------------------------------------------------------------------------
# ModelBackend ABC tests
# ---------------------------------------------------------------------------


class ConcreteBackend(ModelBackend):
    """Minimal concrete implementation for testing."""

    def load(self, model_path: Path, **kwargs: object) -> None:
        pass

    def unload(self) -> None:
        pass

    def cancel(self) -> None:
        pass

    def generate(self, messages: list[ModelMessage], **kwargs: object) -> GenerationResult:
        return GenerationResult(text="test response")

    def stream_generate(
        self, messages: list[ModelMessage], **kwargs: object
    ) -> Iterator[StreamChunk]:
        yield StreamChunk(text="chunk1")
        yield StreamChunk(text="chunk2", is_done=True)

    def supports_tools(self) -> bool:
        return True

    def supports_reasoning(self) -> bool:
        return False

    def supports_streaming(self) -> bool:
        return True

    def memory_usage(self) -> int:
        return 1024

    def capabilities(self) -> dict:
        return {
            "tool_use": self.supports_tools(),
            "reasoning": self.supports_reasoning(),
            "streaming": self.supports_streaming(),
        }


class TestModelBackendABC:
    def test_cannot_instantiate_abc(self):
        with pytest.raises(TypeError):
            ModelBackend()  # type: ignore[abstract]

    def test_concrete_backend_instantiates(self):
        backend = ConcreteBackend()
        assert isinstance(backend, ModelBackend)

    def test_generate(self):
        backend = ConcreteBackend()
        result = backend.generate([ModelMessage(role="user", content="hi")])
        assert result.text == "test response"

    def test_stream_generate(self):
        backend = ConcreteBackend()
        chunks = list(backend.stream_generate([ModelMessage(role="user", content="hi")]))
        assert len(chunks) == 2
        assert chunks[0].text == "chunk1"
        assert chunks[1].is_done is True

    def test_supports_tools(self):
        assert ConcreteBackend().supports_tools() is True

    def test_supports_reasoning(self):
        assert ConcreteBackend().supports_reasoning() is False

    def test_supports_streaming(self):
        assert ConcreteBackend().supports_streaming() is True

    def test_memory_usage(self):
        assert ConcreteBackend().memory_usage() == 1024

    def test_capabilities(self):
        caps = ConcreteBackend().capabilities()
        assert caps == {"tool_use": True, "reasoning": False, "streaming": True}

    def test_load_unload(self):
        backend = ConcreteBackend()
        backend.load(Path("/tmp/model"))
        backend.unload()


# ---------------------------------------------------------------------------
# ModelAbstractionLayer tests
# ---------------------------------------------------------------------------


class TestModelAbstractionLayer:
    def test_backend_property(self):
        backend = ConcreteBackend()
        layer = ModelAbstractionLayer(backend)
        assert layer.backend is backend

    def test_generate_delegates(self):
        backend = ConcreteBackend()
        layer = ModelAbstractionLayer(backend)
        result = layer.generate([ModelMessage(role="user", content="hi")])
        assert result.text == "chunk1"
        assert len(result.chunks) == 2

    def test_stream_generate_delegates(self):
        backend = ConcreteBackend()
        layer = ModelAbstractionLayer(backend)
        chunks = list(layer.stream_generate([ModelMessage(role="user", content="hi")]))
        assert len(chunks) == 2
        assert chunks[0].text == "chunk1"

    def test_supports_tools_delegates(self):
        layer = ModelAbstractionLayer(ConcreteBackend())
        assert layer.supports_tools() is True

    def test_supports_reasoning_delegates(self):
        layer = ModelAbstractionLayer(ConcreteBackend())
        assert layer.supports_reasoning() is False

    def test_supports_streaming_delegates(self):
        layer = ModelAbstractionLayer(ConcreteBackend())
        assert layer.supports_streaming() is True

    def test_cancel_delegates(self):
        backend = ConcreteBackend()
        layer = ModelAbstractionLayer(backend)
        assert layer.cancel() is None
