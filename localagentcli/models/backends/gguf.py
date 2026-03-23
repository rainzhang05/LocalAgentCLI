"""GGUFBackend — local inference via llama-cpp-python."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterator

from localagentcli.models.backends.base import (
    GenerationResult,
    ModelBackend,
    ModelMessage,
    StreamChunk,
    backend_install_hint,
)
from localagentcli.models.model_info import ModelInfo


class GGUFBackend(ModelBackend):
    """Local inference backend using llama-cpp-python for GGUF models.

    Works on all platforms. The llama-cpp-python dependency is imported
    lazily at load time.
    """

    def __init__(self) -> None:
        self._model: Any = None
        self._model_path: Path | None = None

    def load(self, model_path: Path, **kwargs: object) -> None:
        """Load a GGUF model from disk."""
        llama_cpp = self._import_llama_cpp()
        gguf_file = self._find_gguf_file(model_path)

        n_threads = int(kwargs.get("n_threads", 0)) or max((os.cpu_count() or 2) // 2, 1)  # type: ignore[call-overload]
        n_ctx = int(kwargs.get("n_ctx", 2048))  # type: ignore[call-overload]
        n_gpu_layers = int(kwargs.get("n_gpu_layers", 0))  # type: ignore[call-overload]

        self._model = llama_cpp.Llama(
            model_path=str(gguf_file),
            n_threads=n_threads,
            n_ctx=n_ctx,
            n_gpu_layers=n_gpu_layers,
            verbose=False,
        )
        self._model_path = model_path

    def unload(self) -> None:
        """Unload the model and free memory."""
        self._model = None
        self._model_path = None

    def cancel(self) -> None:
        """Best-effort cancellation hook for compatibility with the shell."""

    def generate(self, messages: list[ModelMessage], **kwargs: object) -> GenerationResult:
        """Generate a complete response."""
        if self._model is None:
            raise RuntimeError("No model loaded. Call load() first.")

        chat_messages = [{"role": m.role, "content": m.content} for m in messages]
        max_tokens = int(kwargs.get("max_tokens", 1024))  # type: ignore[call-overload]
        temperature = float(kwargs.get("temperature", 0.7))  # type: ignore[arg-type]

        response = self._model.create_chat_completion(
            messages=chat_messages,
            max_tokens=max_tokens,
            temperature=temperature,
            stream=False,
        )

        text = ""
        choices = response.get("choices", [])
        if choices:
            text = choices[0].get("message", {}).get("content", "")

        usage = response.get("usage", {})
        finish = choices[0].get("finish_reason", "") if choices else ""

        return GenerationResult(
            text=text,
            usage=usage,
            finish_reason=finish,
        )

    def stream_generate(
        self, messages: list[ModelMessage], **kwargs: object
    ) -> Iterator[StreamChunk]:
        """Generate a streaming response."""
        if self._model is None:
            raise RuntimeError("No model loaded. Call load() first.")

        chat_messages = [{"role": m.role, "content": m.content} for m in messages]
        max_tokens = int(kwargs.get("max_tokens", 1024))  # type: ignore[call-overload]
        temperature = float(kwargs.get("temperature", 0.7))  # type: ignore[arg-type]

        stream = self._model.create_chat_completion(
            messages=chat_messages,
            max_tokens=max_tokens,
            temperature=temperature,
            stream=True,
        )

        for chunk in stream:
            choices = chunk.get("choices", [])
            if not choices:
                continue
            delta = choices[0].get("delta", {})
            content = delta.get("content", "")
            finish = choices[0].get("finish_reason")

            if content:
                yield StreamChunk(text=content)
            if finish:
                usage = chunk.get("usage", {})
                yield StreamChunk(is_done=True, usage=usage if usage else None)

    def supports_tools(self) -> bool:
        return False

    def supports_reasoning(self) -> bool:
        return False

    def supports_streaming(self) -> bool:
        return True

    def memory_usage(self) -> int:
        """Estimate memory usage from the model file size."""
        if self._model_path:
            gguf_files = list(self._model_path.glob("*.gguf"))
            if gguf_files:
                try:
                    return gguf_files[0].stat().st_size
                except OSError:
                    pass
        return 0

    def capabilities(self) -> dict:
        return {
            "tool_use": False,
            "reasoning": False,
            "streaming": True,
            "backend": "gguf",
            "platform": "all",
        }

    def model_info(self) -> ModelInfo:
        return ModelInfo(
            id=str(self._model_path) if self._model_path else "gguf_model",
            name=self._model_path.name if self._model_path else "GGUF Model",
            capabilities=self.capabilities(),
            selection_state="local_gguf",
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _import_llama_cpp(self) -> Any:
        """Lazily import llama_cpp."""
        try:
            import llama_cpp

            return llama_cpp
        except ImportError:
            raise RuntimeError(
                "The GGUF backend requires 'llama-cpp-python'. "
                f"Install it with: {backend_install_hint('gguf')}"
            ) from None

    def _find_gguf_file(self, model_path: Path) -> Path:
        """Find the .gguf file in the model directory."""
        if model_path.is_file() and model_path.suffix == ".gguf":
            return model_path

        gguf_files = sorted(model_path.glob("*.gguf"))
        if not gguf_files:
            raise FileNotFoundError(f"No .gguf file found in {model_path}")
        # If multiple, pick the largest (likely the main model file)
        return max(gguf_files, key=lambda f: f.stat().st_size)
