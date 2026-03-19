"""Tool ABC and shared result schema."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path


class _ToolErrorDescriptor:
    """Expose `ToolResult.error` as both an instance attribute and a factory."""

    def __get__(
        self,
        instance: ToolResult | None,
        owner: type[ToolResult],
    ) -> Callable[..., ToolResult] | str | None:
        if instance is None:
            return owner.error_result
        return instance._error

    def __set__(self, instance: ToolResult, value: str | None) -> None:
        instance._error = value


class ToolResult:
    """Structured result returned by every tool."""

    error = _ToolErrorDescriptor()

    def __init__(
        self,
        status: str,
        summary: str,
        output: str,
        error: str | None = None,
        exit_code: int | None = None,
        files_changed: list[str] | None = None,
        duration: float = 0.0,
    ):
        self.status = status
        self.summary = summary
        self.output = output
        self._error = error
        self.exit_code = exit_code
        self.files_changed = list(files_changed or [])
        self.duration = duration

    def to_dict(self) -> dict[str, object]:
        """Serialize the result to a JSON-compatible dict."""
        return {
            "status": self.status,
            "summary": self.summary,
            "output": self.output,
            "error": self.error,
            "exit_code": self.exit_code,
            "files_changed": list(self.files_changed),
            "duration": self.duration,
        }

    @classmethod
    def success(
        cls,
        summary: str,
        output: str = "",
        *,
        exit_code: int | None = None,
        files_changed: list[str] | None = None,
        duration: float = 0.0,
    ) -> ToolResult:
        return cls(
            status="success",
            summary=summary,
            output=output,
            exit_code=exit_code,
            files_changed=files_changed or [],
            duration=duration,
        )

    @classmethod
    def error_result(
        cls,
        summary: str,
        error: str,
        *,
        output: str = "",
        exit_code: int | None = None,
        files_changed: list[str] | None = None,
        duration: float = 0.0,
    ) -> ToolResult:
        return cls(
            status="error",
            summary=summary,
            output=output,
            error=error,
            exit_code=exit_code,
            files_changed=files_changed or [],
            duration=duration,
        )

    @classmethod
    def timeout_result(
        cls,
        summary: str,
        error: str,
        *,
        output: str = "",
        duration: float = 0.0,
    ) -> ToolResult:
        return cls(
            status="timeout",
            summary=summary,
            output=output,
            error=error,
            duration=duration,
        )

    @classmethod
    def denied(
        cls,
        summary: str,
        *,
        output: str = "",
        duration: float = 0.0,
    ) -> ToolResult:
        return cls(
            status="denied",
            summary=summary,
            output=output,
            error="Action denied by user.",
            duration=duration,
        )


class Tool(ABC):
    """Base class for agent tools."""

    def __init__(self, workspace_root: Path):
        self._workspace_root = workspace_root.resolve()

    @property
    @abstractmethod
    def name(self) -> str:
        """Tool name as used in model tool calls."""

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable tool description."""

    @property
    @abstractmethod
    def parameters_schema(self) -> dict:
        """JSON schema describing the tool arguments."""

    execute: Callable[..., ToolResult]

    @property
    def requires_approval(self) -> bool:
        """Whether the tool needs approval in balanced mode."""
        return True

    @property
    def is_read_only(self) -> bool:
        """Whether the tool has no side effects."""
        return False

    def definition(self) -> dict:
        """Return the model-facing tool definition."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters_schema,
        }

    def resolve_path(self, path: str) -> Path:
        """Resolve a workspace-relative path and reject escapes."""
        raw = Path(path)
        candidate = raw if raw.is_absolute() else self._workspace_root / raw
        resolved = candidate.resolve(strict=False)
        if not resolved.is_relative_to(self._workspace_root):
            raise ValueError(
                f"Path '{path}' resolves outside the workspace root '{self._workspace_root}'"
            )
        return resolved

    def relative_path(self, path: Path) -> str:
        """Return a workspace-relative display path."""
        return str(path.relative_to(self._workspace_root))

    def started_at(self) -> float:
        """Return a monotonic timestamp for duration measurement."""
        return time.monotonic()
