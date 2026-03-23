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
from localagentcli.models.model_info import ModelInfo


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

    def cancel(self) -> None:
        """Best-effort cancellation hook for compatibility with the shell."""

    def generate(self, messages: list[ModelMessage], **kwargs: object) -> GenerationResult:
        """Generate a complete response."""
        if self._model is None or self._tokenizer is None:
            raise RuntimeError("No model loaded. Call load() first.")

        prompt = self._format_prompt(messages)
        mlx_lm = self._import_mlx_lm()

        max_tokens = int(kwargs.get("max_tokens", 1024))  # type: ignore[call-overload]
        temperature = float(kwargs.get("temperature", 0.7))  # type: ignore[arg-type]
        generate_kwargs = self._build_generation_kwargs(mlx_lm, prompt, max_tokens, temperature)

        try:
            response = mlx_lm.generate(self._model, self._tokenizer, **generate_kwargs)
            return GenerationResult(text=self._response_text(response))
        except TypeError as exc:
            if not self._is_temp_signature_error(exc):
                raise

        text = "".join(self._iter_stream_text(mlx_lm, generate_kwargs))
        return GenerationResult(text=text)

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
        generate_kwargs = self._build_generation_kwargs(mlx_lm, prompt, max_tokens, temperature)

        for text in self._iter_stream_text(mlx_lm, generate_kwargs):
            yield StreamChunk(text=text)
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

    def model_info(self) -> ModelInfo:
        return ModelInfo(
            id=str(self._model_path) if self._model_path else "mlx_model",
            name=self._model_path.name if self._model_path else "MLX Model",
            capabilities=self.capabilities(),
            selection_state="local_mlx",
        )

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

    def _build_generation_kwargs(
        self,
        mlx_lm: Any,
        prompt: str,
        max_tokens: int,
        temperature: float,
    ) -> dict[str, object]:
        """Build kwargs compatible with current mlx-lm generation APIs."""
        generate_kwargs: dict[str, object] = {
            "prompt": prompt,
            "max_tokens": max_tokens,
        }
        sampler = self._build_sampler(mlx_lm, temperature)
        if sampler is not None:
            generate_kwargs["sampler"] = sampler
        return generate_kwargs

    def _build_sampler(self, mlx_lm: Any, temperature: float) -> Any | None:
        """Build a sampler instead of relying on deprecated temp kwargs."""
        sample_utils = getattr(mlx_lm, "sample_utils", None)
        make_sampler = getattr(sample_utils, "make_sampler", None)
        if make_sampler is None:
            try:
                from mlx_lm.sample_utils import make_sampler as imported_make_sampler
            except ImportError:
                return None
            make_sampler = imported_make_sampler

        for keyword in ("temp", "temperature"):
            try:
                return make_sampler(**{keyword: temperature})
            except TypeError:
                continue
        return None

    def _iter_stream_text(
        self,
        mlx_lm: Any,
        generate_kwargs: dict[str, object],
    ) -> Iterator[str]:
        """Yield text using stream_generate when mlx-lm's generate path is incompatible."""
        stream_generate = getattr(mlx_lm, "stream_generate", None)
        if callable(stream_generate):
            try:
                for response in stream_generate(self._model, self._tokenizer, **generate_kwargs):
                    text = self._response_text(response)
                    if text:
                        yield text
                return
            except TypeError as exc:
                if not self._is_temp_signature_error(exc):
                    raise

        response = mlx_lm.generate(self._model, self._tokenizer, **generate_kwargs)
        text = self._response_text(response)
        if text:
            yield text

    def _response_text(self, response: object) -> str:
        """Extract text from mlx-lm responses across versions."""
        if isinstance(response, str):
            return response
        text = getattr(response, "text", None)
        if isinstance(text, str):
            return text
        return str(response)

    def _is_temp_signature_error(self, exc: TypeError) -> bool:
        """Detect the known mlx-lm temp/temperature compatibility error."""
        message = str(exc)
        return "unexpected keyword argument" in message and (
            "'temp'" in message or "'temperature'" in message
        )
