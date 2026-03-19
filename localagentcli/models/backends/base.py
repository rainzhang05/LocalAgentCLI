"""ModelBackend ABC — unified interface for all model backends."""

from __future__ import annotations

import importlib
import re
import subprocess
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator

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
