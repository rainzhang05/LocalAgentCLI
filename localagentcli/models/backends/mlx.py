"""MLXBackend — Apple Silicon local inference via mlx-lm."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Iterator

from localagentcli.models.backends.base import (
    GenerationResult,
    ModelBackend,
    ModelMessage,
    StreamChunk,
    backend_install_hint,
)


class MLXBackend(ModelBackend):
    """Local inference backend using MLX on Apple Silicon.

    Requires macOS with Apple Silicon. Dependencies (mlx, mlx-lm)
    are imported lazily at load time.
    """

    def __init__(self) -> None:
        self._model: Any = None
        self._tokenizer: Any = None
        self._model_path: Path | None = None

    def load(self, model_path: Path, **kwargs: object) -> None:
        """Load an MLX model from disk."""
        self._check_platform()
        mlx_lm = self._import_mlx_lm()
        self._model, self._tokenizer = mlx_lm.load(str(model_path))
        self._model_path = model_path

    def unload(self) -> None:
        """Unload the model and free memory."""
        self._model = None
        self._tokenizer = None
        self._model_path = None

    def generate(self, messages: list[ModelMessage], **kwargs: object) -> GenerationResult:
        """Generate a complete response."""
        if self._model is None or self._tokenizer is None:
            raise RuntimeError("No model loaded. Call load() first.")

        prompt = self._format_prompt(messages)
        mlx_lm = self._import_mlx_lm()

        max_tokens = int(kwargs.get("max_tokens", 1024))  # type: ignore[call-overload]
        temperature = float(kwargs.get("temperature", 0.7))  # type: ignore[arg-type]

        response = mlx_lm.generate(
            self._model,
            self._tokenizer,
            prompt=prompt,
            max_tokens=max_tokens,
            temp=temperature,
        )

        return GenerationResult(text=response)

    def stream_generate(
        self, messages: list[ModelMessage], **kwargs: object
    ) -> Iterator[StreamChunk]:
        """Generate a streaming response token by token."""
        if self._model is None or self._tokenizer is None:
            raise RuntimeError("No model loaded. Call load() first.")

        prompt = self._format_prompt(messages)
        mlx_lm = self._import_mlx_lm()

        max_tokens = int(kwargs.get("max_tokens", 1024))  # type: ignore[call-overload]
        temperature = float(kwargs.get("temperature", 0.7))  # type: ignore[arg-type]

        response = mlx_lm.generate(
            self._model,
            self._tokenizer,
            prompt=prompt,
            max_tokens=max_tokens,
            temp=temperature,
        )

        # mlx_lm.generate returns a string; yield it as a single chunk
        # For true token-by-token streaming, mlx_lm.stream_generate can be used
        # when available in newer versions
        yield StreamChunk(text=response)
        yield StreamChunk(is_done=True)

    def supports_tools(self) -> bool:
        return False

    def supports_reasoning(self) -> bool:
        return False

    def supports_streaming(self) -> bool:
        return True

    def memory_usage(self) -> int:
        """Return current memory usage in bytes."""
        try:
            import mlx.core as mx

            return int(mx.metal.get_active_memory())
        except (ImportError, AttributeError):
            return 0

    def capabilities(self) -> dict:
        return {
            "tool_use": False,
            "reasoning": False,
            "streaming": True,
            "backend": "mlx",
            "platform": "macOS (Apple Silicon)",
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_platform(self) -> None:
        """Verify we're running on macOS."""
        if sys.platform != "darwin":
            raise RuntimeError(
                "MLXBackend requires macOS. "
                "Use GGUFBackend or SafetensorsBackend on other platforms."
            )

    def _import_mlx_lm(self) -> Any:
        """Lazily import mlx_lm."""
        try:
            import mlx_lm

            return mlx_lm
        except ImportError:
            raise RuntimeError(
                f"The MLX backend requires 'mlx-lm'. Install it with: {backend_install_hint('mlx')}"
            ) from None

    def _format_prompt(self, messages: list[ModelMessage]) -> str:
        """Format messages into a prompt string."""
        if self._tokenizer is not None:
            # Try to use the tokenizer's chat template if available
            try:
                chat_messages = [{"role": m.role, "content": m.content} for m in messages]
                result: str = self._tokenizer.apply_chat_template(
                    chat_messages, tokenize=False, add_generation_prompt=True
                )
                return result
            except (AttributeError, Exception):
                pass

        # Fallback: simple concatenation
        parts = []
        for msg in messages:
            if msg.role == "system":
                parts.append(f"System: {msg.content}\n")
            elif msg.role == "user":
                parts.append(f"User: {msg.content}\n")
            elif msg.role == "assistant":
                parts.append(f"Assistant: {msg.content}\n")
        parts.append("Assistant: ")
        return "".join(parts)
