"""Tests for GGUFBackend — mocked to run without llama-cpp-python installed."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from localagentcli.models.backends.base import ModelMessage
from localagentcli.models.backends.gguf import GGUFBackend


@pytest.fixture
def backend() -> GGUFBackend:
    return GGUFBackend()


# ---------------------------------------------------------------------------
# Import check
# ---------------------------------------------------------------------------


class TestGGUFImport:
    def test_import_error_message(self, backend: GGUFBackend):
        with pytest.raises(RuntimeError, match="pip install localagentcli"):
            backend.load(Path("/fake"))


# ---------------------------------------------------------------------------
# Find GGUF file
# ---------------------------------------------------------------------------


class TestFindGGUFFile:
    def test_find_single_file(self, backend: GGUFBackend, tmp_path: Path):
        gguf_file = tmp_path / "model.gguf"
        gguf_file.write_bytes(b"\x00" * 100)
        found = backend._find_gguf_file(tmp_path)
        assert found == gguf_file

    def test_find_largest_file(self, backend: GGUFBackend, tmp_path: Path):
        small = tmp_path / "small.gguf"
        small.write_bytes(b"\x00" * 10)
        large = tmp_path / "large.gguf"
        large.write_bytes(b"\x00" * 1000)
        found = backend._find_gguf_file(tmp_path)
        assert found == large

    def test_no_gguf_raises(self, backend: GGUFBackend, tmp_path: Path):
        with pytest.raises(FileNotFoundError, match="No .gguf file"):
            backend._find_gguf_file(tmp_path)

    def test_direct_file_path(self, backend: GGUFBackend, tmp_path: Path):
        gguf_file = tmp_path / "model.gguf"
        gguf_file.write_bytes(b"\x00" * 100)
        found = backend._find_gguf_file(gguf_file)
        assert found == gguf_file


# ---------------------------------------------------------------------------
# Load / Unload
# ---------------------------------------------------------------------------


class TestGGUFLoadUnload:
    def test_load_creates_model(self, backend: GGUFBackend, tmp_path: Path):
        (tmp_path / "model.gguf").write_bytes(b"\x00" * 100)
        mock_llama_cpp = MagicMock()
        mock_model = MagicMock()
        mock_llama_cpp.Llama.return_value = mock_model

        with patch.object(backend, "_import_llama_cpp", return_value=mock_llama_cpp):
            backend.load(tmp_path)

        assert backend._model is mock_model
        mock_llama_cpp.Llama.assert_called_once()

    def test_load_thread_config(self, backend: GGUFBackend, tmp_path: Path):
        (tmp_path / "model.gguf").write_bytes(b"\x00" * 100)
        mock_llama_cpp = MagicMock()

        with patch.object(backend, "_import_llama_cpp", return_value=mock_llama_cpp):
            backend.load(tmp_path, n_threads=4, n_ctx=4096, n_gpu_layers=32)

        call_kwargs = mock_llama_cpp.Llama.call_args
        assert call_kwargs[1]["n_threads"] == 4
        assert call_kwargs[1]["n_ctx"] == 4096
        assert call_kwargs[1]["n_gpu_layers"] == 32

    def test_unload_clears_model(self, backend: GGUFBackend):
        backend._model = MagicMock()
        backend._model_path = Path("/fake")
        backend.unload()
        assert backend._model is None
        assert backend._model_path is None


# ---------------------------------------------------------------------------
# Generate
# ---------------------------------------------------------------------------


class TestGGUFGenerate:
    def test_generate_no_model_raises(self, backend: GGUFBackend):
        with pytest.raises(RuntimeError, match="No model loaded"):
            backend.generate([ModelMessage(role="user", content="hello")])

    def test_generate_returns_result(self, backend: GGUFBackend):
        mock_model = MagicMock()
        mock_model.create_chat_completion.return_value = {
            "choices": [{"message": {"content": "Hello!"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2},
        }
        backend._model = mock_model

        result = backend.generate([ModelMessage(role="user", content="hi")])
        assert result.text == "Hello!"
        assert result.finish_reason == "stop"
        assert result.usage["prompt_tokens"] == 5

    def test_generate_empty_choices(self, backend: GGUFBackend):
        mock_model = MagicMock()
        mock_model.create_chat_completion.return_value = {"choices": []}
        backend._model = mock_model

        result = backend.generate([ModelMessage(role="user", content="hi")])
        assert result.text == ""


# ---------------------------------------------------------------------------
# Stream Generate
# ---------------------------------------------------------------------------


class TestGGUFStreamGenerate:
    def test_stream_no_model_raises(self, backend: GGUFBackend):
        with pytest.raises(RuntimeError, match="No model loaded"):
            list(backend.stream_generate([ModelMessage(role="user", content="hi")]))

    def test_stream_yields_chunks(self, backend: GGUFBackend):
        mock_model = MagicMock()
        mock_model.create_chat_completion.return_value = iter(
            [
                {"choices": [{"delta": {"content": "Hello"}, "finish_reason": None}]},
                {"choices": [{"delta": {"content": " world"}, "finish_reason": None}]},
                {"choices": [{"delta": {}, "finish_reason": "stop"}]},
            ]
        )
        backend._model = mock_model

        chunks = list(backend.stream_generate([ModelMessage(role="user", content="hi")]))
        texts = [c.text for c in chunks if c.text]
        assert texts == ["Hello", " world"]
        assert chunks[-1].is_done is True

    def test_stream_empty_choices(self, backend: GGUFBackend):
        mock_model = MagicMock()
        mock_model.create_chat_completion.return_value = iter(
            [
                {"choices": []},
                {"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]},
            ]
        )
        backend._model = mock_model

        chunks = list(backend.stream_generate([ModelMessage(role="user", content="hi")]))
        texts = [c.text for c in chunks if c.text]
        assert texts == ["ok"]


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------


class TestGGUFCapabilities:
    def test_supports_tools(self, backend: GGUFBackend):
        assert backend.supports_tools() is False

    def test_supports_reasoning(self, backend: GGUFBackend):
        assert backend.supports_reasoning() is False

    def test_supports_streaming(self, backend: GGUFBackend):
        assert backend.supports_streaming() is True

    def test_capabilities_dict(self, backend: GGUFBackend):
        caps = backend.capabilities()
        assert caps["backend"] == "gguf"
        assert caps["platform"] == "all"

    def test_memory_usage_no_path(self, backend: GGUFBackend):
        assert backend.memory_usage() == 0

    def test_memory_usage_with_file(self, backend: GGUFBackend, tmp_path: Path):
        gguf = tmp_path / "model.gguf"
        gguf.write_bytes(b"\x00" * 500)
        backend._model_path = tmp_path
        assert backend.memory_usage() == 500
