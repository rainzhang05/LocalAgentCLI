"""Tests for SafetensorsBackend — mocked to run without torch/transformers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from localagentcli.models.backends.base import ModelMessage
from localagentcli.models.backends.safetensors import SafetensorsBackend


@pytest.fixture
def backend() -> SafetensorsBackend:
    return SafetensorsBackend()


# ---------------------------------------------------------------------------
# Import check
# ---------------------------------------------------------------------------


class TestSafetensorsImport:
    def test_import_error_torch(self, backend: SafetensorsBackend):
        with patch.object(
            backend,
            "_import_deps",
            side_effect=RuntimeError(
                "The Safetensors backend requires 'torch'. "
                "Install it with: pip install localagentcli[torch]"
            ),
        ):
            with pytest.raises(RuntimeError, match="requires 'torch'"):
                backend.load(Path("/fake"))

    def test_import_error_transformers(self, backend: SafetensorsBackend):
        with patch.object(
            backend,
            "_import_deps",
            side_effect=RuntimeError(
                "The Safetensors backend requires 'transformers'. "
                "Install it with: pip install localagentcli[torch]"
            ),
        ):
            with pytest.raises(RuntimeError, match="requires 'transformers'"):
                backend.load(Path("/fake"))


# ---------------------------------------------------------------------------
# Device selection
# ---------------------------------------------------------------------------


class TestDeviceSelection:
    def test_cuda_available(self, backend: SafetensorsBackend):
        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = True
        assert backend._select_device(mock_torch) == "cuda"

    def test_mps_available(self, backend: SafetensorsBackend):
        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = False
        mock_torch.backends.mps.is_available.return_value = True
        assert backend._select_device(mock_torch) == "mps"

    def test_cpu_fallback(self, backend: SafetensorsBackend):
        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = False
        mock_torch.backends.mps.is_available.return_value = False
        assert backend._select_device(mock_torch) == "cpu"


# ---------------------------------------------------------------------------
# Load / Unload
# ---------------------------------------------------------------------------


class TestSafetensorsLoadUnload:
    def test_load_creates_model(self, backend: SafetensorsBackend, tmp_path: Path):
        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = False
        mock_torch.backends.mps.is_available.return_value = False

        mock_transformers = MagicMock()
        mock_model = MagicMock()
        mock_tokenizer = MagicMock()
        mock_transformers.AutoModelForCausalLM.from_pretrained.return_value = mock_model
        mock_transformers.AutoTokenizer.from_pretrained.return_value = mock_tokenizer

        with patch.object(backend, "_import_deps", return_value=(mock_torch, mock_transformers)):
            backend.load(tmp_path)

        assert backend._model is mock_model
        assert backend._tokenizer is mock_tokenizer
        assert backend._device == "cpu"
        mock_model.to.assert_called_once_with("cpu")
        mock_model.eval.assert_called_once()

    def test_unload_clears_model(self, backend: SafetensorsBackend):
        backend._model = MagicMock()
        backend._tokenizer = MagicMock()
        backend._model_path = Path("/fake")
        backend._device = "cpu"

        backend.unload()

        assert backend._model is None
        assert backend._tokenizer is None
        assert backend._model_path is None


# ---------------------------------------------------------------------------
# Generate
# ---------------------------------------------------------------------------


class TestSafetensorsGenerate:
    def test_generate_no_model_raises(self, backend: SafetensorsBackend):
        with pytest.raises(RuntimeError, match="No model loaded"):
            backend.generate([ModelMessage(role="user", content="hello")])

    def test_generate_returns_result(self, backend: SafetensorsBackend):
        mock_torch = MagicMock()
        mock_torch.no_grad.return_value.__enter__ = MagicMock()
        mock_torch.no_grad.return_value.__exit__ = MagicMock()
        mock_transformers = MagicMock()

        mock_tokenizer = MagicMock()
        mock_tokenizer.apply_chat_template.side_effect = AttributeError
        mock_inputs = MagicMock()
        mock_inputs.__getitem__ = MagicMock(return_value=MagicMock(shape=(1, 5)))
        mock_inputs.to.return_value = mock_inputs
        mock_tokenizer.return_value = mock_inputs
        mock_tokenizer.decode.return_value = "Generated text"

        mock_model = MagicMock()
        mock_outputs = MagicMock()
        mock_outputs.__getitem__ = MagicMock(return_value=list(range(10)))
        mock_model.generate.return_value = mock_outputs

        backend._model = mock_model
        backend._tokenizer = mock_tokenizer

        with patch.object(backend, "_import_deps", return_value=(mock_torch, mock_transformers)):
            result = backend.generate([ModelMessage(role="user", content="hi")])

        assert result.text == "Generated text"


# ---------------------------------------------------------------------------
# Stream Generate
# ---------------------------------------------------------------------------


class TestSafetensorsStreamGenerate:
    def test_stream_no_model_raises(self, backend: SafetensorsBackend):
        with pytest.raises(RuntimeError, match="No model loaded"):
            list(backend.stream_generate([ModelMessage(role="user", content="hi")]))

    def test_stream_yields_chunks(self, backend: SafetensorsBackend):
        mock_torch = MagicMock()
        mock_transformers = MagicMock()

        mock_tokenizer = MagicMock()
        mock_tokenizer.apply_chat_template.side_effect = AttributeError
        mock_inputs = MagicMock()
        mock_inputs.to.return_value = mock_inputs
        mock_tokenizer.return_value = mock_inputs

        # Mock TextIteratorStreamer to yield text
        mock_streamer_instance = MagicMock()
        mock_streamer_instance.__iter__ = MagicMock(return_value=iter(["Hello", " world", ""]))
        mock_transformers.TextIteratorStreamer.return_value = mock_streamer_instance

        mock_model = MagicMock()
        backend._model = mock_model
        backend._tokenizer = mock_tokenizer

        with patch.object(backend, "_import_deps", return_value=(mock_torch, mock_transformers)):
            chunks = list(backend.stream_generate([ModelMessage(role="user", content="hi")]))

        texts = [c.text for c in chunks if c.text]
        assert texts == ["Hello", " world"]
        assert chunks[-1].is_done is True


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------


class TestSafetensorsCapabilities:
    def test_supports_tools(self, backend: SafetensorsBackend):
        assert backend.supports_tools() is False

    def test_supports_reasoning(self, backend: SafetensorsBackend):
        assert backend.supports_reasoning() is False

    def test_supports_streaming(self, backend: SafetensorsBackend):
        assert backend.supports_streaming() is True

    def test_capabilities_dict(self, backend: SafetensorsBackend):
        caps = backend.capabilities()
        assert caps["backend"] == "safetensors"
        assert caps["platform"] == "all"
        assert caps["device"] == "cpu"

    def test_memory_usage_no_model(self, backend: SafetensorsBackend):
        assert backend.memory_usage() == 0


# ---------------------------------------------------------------------------
# Prompt formatting
# ---------------------------------------------------------------------------


class TestSafetensorsPromptFormatting:
    def test_fallback_format(self, backend: SafetensorsBackend):
        messages = [
            ModelMessage(role="system", content="Be helpful."),
            ModelMessage(role="user", content="Hello"),
        ]
        result = backend._format_prompt(messages)
        assert "System: Be helpful." in result
        assert "User: Hello" in result
        assert result.endswith("Assistant: ")

    def test_chat_template_used(self, backend: SafetensorsBackend):
        mock_tokenizer = MagicMock()
        mock_tokenizer.apply_chat_template.return_value = "<formatted>"
        backend._tokenizer = mock_tokenizer

        result = backend._format_prompt([ModelMessage(role="user", content="hi")])
        assert result == "<formatted>"
