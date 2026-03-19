"""SafetensorsBackend — local inference via PyTorch + Transformers."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Iterator

from localagentcli.models.backends.base import (
    GenerationResult,
    ModelBackend,
    ModelMessage,
    StreamChunk,
    backend_install_hint,
)


class SafetensorsBackend(ModelBackend):
    """Local inference backend using Hugging Face Transformers.

    Works on all platforms. Dependencies (torch, transformers) are
    imported lazily at load time.
    """

    def __init__(self) -> None:
        self._model: Any = None
        self._tokenizer: Any = None
        self._device: str = "cpu"
        self._model_path: Path | None = None
        self._cancel_event = threading.Event()
        self._generation_thread: threading.Thread | None = None

    def load(self, model_path: Path, **kwargs: object) -> None:
        """Load a safetensors model from disk."""
        torch, transformers = self._import_deps()
        self._device = self._select_device(torch)

        self._tokenizer = transformers.AutoTokenizer.from_pretrained(
            str(model_path), trust_remote_code=True
        )
        self._model = transformers.AutoModelForCausalLM.from_pretrained(
            str(model_path), trust_remote_code=True
        )
        self._model.to(self._device)
        self._model.eval()
        self._model_path = model_path

    def unload(self) -> None:
        """Unload the model and free memory."""
        self.cancel()
        if self._model is not None:
            try:
                import torch

                if self._device.startswith("cuda"):
                    del self._model
                    torch.cuda.empty_cache()
                else:
                    del self._model
            except ImportError:
                self._model = None
        self._model = None
        self._tokenizer = None
        self._model_path = None

    def cancel(self) -> None:
        """Signal an in-flight generation to stop."""
        self._cancel_event.set()
        if self._generation_thread is not None and self._generation_thread.is_alive():
            self._generation_thread.join(timeout=1)
        self._generation_thread = None

    def generate(self, messages: list[ModelMessage], **kwargs: object) -> GenerationResult:
        """Generate a complete response."""
        if self._model is None or self._tokenizer is None:
            raise RuntimeError("No model loaded. Call load() first.")

        torch, _ = self._import_deps()
        prompt = self._format_prompt(messages)
        max_tokens = int(kwargs.get("max_tokens", 1024))  # type: ignore[call-overload]
        temperature = float(kwargs.get("temperature", 0.7))  # type: ignore[arg-type]

        inputs = self._tokenizer(prompt, return_tensors="pt").to(self._device)
        input_len = inputs["input_ids"].shape[1]

        with torch.no_grad():
            outputs = self._model.generate(
                **inputs,
                max_new_tokens=max_tokens,
                temperature=temperature,
                do_sample=temperature > 0,
            )

        new_tokens = outputs[0][input_len:]
        text = self._tokenizer.decode(new_tokens, skip_special_tokens=True)

        return GenerationResult(text=text)

    def stream_generate(
        self, messages: list[ModelMessage], **kwargs: object
    ) -> Iterator[StreamChunk]:
        """Generate a streaming response using TextIteratorStreamer."""
        if self._model is None or self._tokenizer is None:
            raise RuntimeError("No model loaded. Call load() first.")

        torch, transformers = self._import_deps()
        prompt = self._format_prompt(messages)
        max_tokens = int(kwargs.get("max_tokens", 1024))  # type: ignore[call-overload]
        temperature = float(kwargs.get("temperature", 0.7))  # type: ignore[arg-type]

        inputs = self._tokenizer(prompt, return_tensors="pt").to(self._device)

        streamer = transformers.TextIteratorStreamer(
            self._tokenizer, skip_prompt=True, skip_special_tokens=True
        )
        self._cancel_event.clear()

        stopping_criteria = transformers.StoppingCriteriaList(
            [_CancelStoppingCriteria(self._cancel_event)]
        )

        gen_kwargs = {
            **inputs,
            "max_new_tokens": max_tokens,
            "temperature": temperature,
            "do_sample": temperature > 0,
            "streamer": streamer,
            "stopping_criteria": stopping_criteria,
        }

        self._generation_thread = threading.Thread(target=self._model.generate, kwargs=gen_kwargs)
        self._generation_thread.start()

        try:
            for text in streamer:
                if self._cancel_event.is_set():
                    break
                if text:
                    yield StreamChunk(text=text)
            yield StreamChunk(is_done=True)
        finally:
            if self._generation_thread is not None:
                self._generation_thread.join(timeout=5)
            self._generation_thread = None
            self._cancel_event.clear()

    def supports_tools(self) -> bool:
        return False

    def supports_reasoning(self) -> bool:
        return False

    def supports_streaming(self) -> bool:
        return True

    def memory_usage(self) -> int:
        """Return current memory usage in bytes."""
        try:
            import torch

            if torch.cuda.is_available():
                return int(torch.cuda.memory_allocated())
        except ImportError:
            pass

        # Estimate from model parameters
        if self._model is not None:
            try:
                params: int = sum(p.numel() * p.element_size() for p in self._model.parameters())
                return params
            except Exception:
                pass
        return 0

    def capabilities(self) -> dict:
        return {
            "tool_use": False,
            "reasoning": False,
            "streaming": True,
            "backend": "safetensors",
            "platform": "all",
            "device": self._device,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _import_deps(self) -> tuple[Any, Any]:
        """Lazily import torch and transformers."""
        try:
            import torch
        except ImportError:
            raise RuntimeError(
                "The Safetensors backend requires 'torch'. "
                f"Install it with: {backend_install_hint('safetensors')}"
            ) from None
        try:
            import transformers
        except ImportError:
            raise RuntimeError(
                "The Safetensors backend requires 'transformers'. "
                f"Install it with: {backend_install_hint('safetensors')}"
            ) from None
        return torch, transformers

    def _select_device(self, torch_module: Any) -> str:
        """Select the best available device."""
        if torch_module.cuda.is_available():
            return "cuda"
        if hasattr(torch_module.backends, "mps") and torch_module.backends.mps.is_available():
            return "mps"
        return "cpu"

    def _format_prompt(self, messages: list[ModelMessage]) -> str:
        """Format messages into a prompt string."""
        if self._tokenizer is not None:
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


class _CancelStoppingCriteria:
    """Transformers stopping criteria that responds to backend cancel events."""

    def __init__(self, cancel_event: threading.Event):
        self._cancel_event = cancel_event

    def __call__(self, input_ids: Any, scores: Any, **kwargs: object) -> bool:
        return self._cancel_event.is_set()
