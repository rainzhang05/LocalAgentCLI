"""Tests for MLXBackend — mocked to run without mlx installed."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from localagentcli.models.backends.base import ModelMessage
from localagentcli.models.backends.mlx import MLXBackend


@pytest.fixture
def backend() -> MLXBackend:
    return MLXBackend()


# ---------------------------------------------------------------------------
# Platform check
# ---------------------------------------------------------------------------


class TestMLXPlatformCheck:
    def test_non_macos_raises(self, backend: MLXBackend):
        with patch("localagentcli.models.backends.mlx.sys") as mock_sys:
            mock_sys.platform = "linux"
            with pytest.raises(RuntimeError, match="requires macOS"):
                backend.load(Path("/fake"))

    @patch("localagentcli.models.backends.mlx.sys")
    def test_macos_proceeds(self, mock_sys, backend: MLXBackend, tmp_path: Path):
        mock_sys.platform = "darwin"
        mock_mlx_lm = MagicMock()
        mock_mlx_lm.load.return_value = (MagicMock(), MagicMock())
        with patch.object(backend, "_import_mlx_lm", return_value=mock_mlx_lm):
            backend.load(tmp_path)
        assert backend._model is not None


# ---------------------------------------------------------------------------
# Import check
# ---------------------------------------------------------------------------


class TestMLXImport:
    def test_import_error_message(self, backend: MLXBackend):
        with patch("localagentcli.models.backends.mlx.sys") as mock_sys:
            mock_sys.platform = "darwin"
            with pytest.raises(RuntimeError, match="pip install localagentcli"):
                backend.load(Path("/fake"))


# ---------------------------------------------------------------------------
# Load / Unload
# ---------------------------------------------------------------------------


class TestMLXLoadUnload:
    def test_load_sets_model(self, backend: MLXBackend, tmp_path: Path):
        mock_mlx_lm = MagicMock()
        mock_model = MagicMock()
        mock_tokenizer = MagicMock()
        mock_mlx_lm.load.return_value = (mock_model, mock_tokenizer)

        with patch("localagentcli.models.backends.mlx.sys") as mock_sys:
            mock_sys.platform = "darwin"
            with patch.object(backend, "_import_mlx_lm", return_value=mock_mlx_lm):
                backend.load(tmp_path)

        assert backend._model is mock_model
        assert backend._tokenizer is mock_tokenizer

    def test_unload_clears_model(self, backend: MLXBackend):
        backend._model = MagicMock()
        backend._tokenizer = MagicMock()
        backend._model_path = Path("/fake")

        backend.unload()

        assert backend._model is None
        assert backend._tokenizer is None
        assert backend._model_path is None


# ---------------------------------------------------------------------------
# Generate
# ---------------------------------------------------------------------------


class TestMLXGenerate:
    def test_generate_no_model_raises(self, backend: MLXBackend):
        with pytest.raises(RuntimeError, match="No model loaded"):
            backend.generate([ModelMessage(role="user", content="hello")])

    def test_generate_returns_result(self, backend: MLXBackend):
        backend._model = MagicMock()
        backend._tokenizer = MagicMock()
        backend._tokenizer.apply_chat_template.side_effect = AttributeError

        mock_mlx_lm = MagicMock()
        mock_mlx_lm.stream_generate = None
        mock_mlx_lm.generate.return_value = "Hello world"

        with patch.object(backend, "_import_mlx_lm", return_value=mock_mlx_lm):
            result = backend.generate([ModelMessage(role="user", content="hi")])

        assert result.text == "Hello world"

    def test_generate_uses_sampler_instead_of_temp(self, backend: MLXBackend):
        backend._model = MagicMock()
        backend._tokenizer = MagicMock()
        backend._tokenizer.apply_chat_template.side_effect = AttributeError

        mock_mlx_lm = MagicMock()
        mock_mlx_lm.stream_generate = None
        mock_mlx_lm.generate.return_value = "Hello world"

        with patch.object(backend, "_import_mlx_lm", return_value=mock_mlx_lm):
            with patch.object(backend, "_build_sampler", return_value="sampler"):
                backend.generate(
                    [ModelMessage(role="user", content="hi")],
                    temperature=0.2,
                    max_tokens=123,
                )

        kwargs = mock_mlx_lm.generate.call_args.kwargs
        assert kwargs["sampler"] == "sampler"
        assert "temp" not in kwargs
        assert "temperature" not in kwargs

    def test_generate_retries_with_stream_generate_on_temp_signature_error(
        self,
        backend: MLXBackend,
    ):
        backend._model = MagicMock()
        backend._tokenizer = MagicMock()
        backend._tokenizer.apply_chat_template.side_effect = AttributeError

        mock_mlx_lm = MagicMock()
        mock_mlx_lm.generate.side_effect = TypeError(
            "generate_step() got an unexpected keyword argument 'temp'"
        )
        mock_mlx_lm.stream_generate.return_value = [
            MagicMock(text="Hello "),
            MagicMock(text="world"),
        ]

        with patch.object(backend, "_import_mlx_lm", return_value=mock_mlx_lm):
            result = backend.generate([ModelMessage(role="user", content="hi")])

        assert result.text == "Hello world"


# ---------------------------------------------------------------------------
# Stream Generate
# ---------------------------------------------------------------------------


class TestMLXStreamGenerate:
    def test_stream_no_model_raises(self, backend: MLXBackend):
        with pytest.raises(RuntimeError, match="No model loaded"):
            list(backend.stream_generate([ModelMessage(role="user", content="hello")]))

    def test_stream_yields_chunks(self, backend: MLXBackend):
        backend._model = MagicMock()
        backend._tokenizer = MagicMock()
        backend._tokenizer.apply_chat_template.side_effect = AttributeError

        mock_mlx_lm = MagicMock()
        mock_mlx_lm.stream_generate.return_value = [
            MagicMock(text="streamed "),
            MagicMock(text="text"),
        ]

        with patch.object(backend, "_import_mlx_lm", return_value=mock_mlx_lm):
            chunks = list(backend.stream_generate([ModelMessage(role="user", content="hi")]))

        assert len(chunks) == 3
        assert chunks[0].text == "streamed "
        assert chunks[1].text == "text"
        assert chunks[2].is_done is True


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------


class TestMLXCapabilities:
    def test_supports_tools(self, backend: MLXBackend):
        assert backend.supports_tools() is False

    def test_supports_reasoning(self, backend: MLXBackend):
        assert backend.supports_reasoning() is False

    def test_supports_streaming(self, backend: MLXBackend):
        assert backend.supports_streaming() is True

    def test_capabilities_dict(self, backend: MLXBackend):
        caps = backend.capabilities()
        assert caps["backend"] == "mlx"
        assert caps["tool_use"] is False
        assert caps["streaming"] is True

    def test_memory_usage_no_mlx(self, backend: MLXBackend):
        assert backend.memory_usage() == 0


# ---------------------------------------------------------------------------
# Prompt formatting
# ---------------------------------------------------------------------------


class TestMLXPromptFormatting:
    def test_fallback_format(self, backend: MLXBackend):
        messages = [
            ModelMessage(role="system", content="You are helpful."),
            ModelMessage(role="user", content="Hello"),
        ]
        result = backend._format_prompt(messages)
        assert "System: You are helpful." in result
        assert "User: Hello" in result
        assert result.endswith("Assistant: ")

    def test_chat_template_used(self, backend: MLXBackend):
        mock_tokenizer = MagicMock()
        mock_tokenizer.apply_chat_template.return_value = "<formatted>"
        backend._tokenizer = mock_tokenizer

        result = backend._format_prompt([ModelMessage(role="user", content="hi")])
        assert result == "<formatted>"
