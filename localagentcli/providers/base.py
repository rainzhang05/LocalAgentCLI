"""RemoteProvider ABC — base class for all remote API providers."""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from localagentcli.models.backends.base import ModelBackend


@dataclass
class ConnectionTestResult:
    """Result of a provider connectivity test."""

    success: bool
    message: str
    latency_ms: float = 0.0


@dataclass
class RemoteModelInfo:
    """Metadata about a model available from a remote provider."""

    id: str
    name: str
    capabilities: dict = field(default_factory=dict)


class RemoteProvider(ModelBackend):
    """Abstract base class for remote API providers.

    Extends ModelBackend with provider-specific methods (test_connection,
    list_models). Remote providers have no local model to load, so load(),
    unload(), and memory_usage() are concrete no-ops.
    """

    def __init__(
        self,
        name: str,
        base_url: str,
        api_key: str,
        default_model: str,
        options: dict | None = None,
    ):
        self._name = name
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._default_model = default_model
        self._options = options or {}

    @property
    def name(self) -> str:
        """Provider name."""
        return self._name

    @property
    def base_url(self) -> str:
        """API base URL."""
        return self._base_url

    @property
    def default_model(self) -> str:
        """Default model identifier."""
        return self._default_model

    # ------------------------------------------------------------------
    # ModelBackend no-ops (remote providers have no local model)
    # ------------------------------------------------------------------

    def load(self, model_path: Path, **kwargs: object) -> None:
        """No-op for remote providers."""

    def unload(self) -> None:
        """No-op for remote providers."""

    def memory_usage(self) -> int:
        """Remote providers use no local memory."""
        return 0

    # ------------------------------------------------------------------
    # Abstract methods unique to remote providers
    # ------------------------------------------------------------------

    @abstractmethod
    def test_connection(self) -> ConnectionTestResult:
        """Test connectivity to the provider."""
        ...

    @abstractmethod
    def list_models(self) -> list[RemoteModelInfo]:
        """List available models from this provider."""
        ...

    # ------------------------------------------------------------------
    # Inherited abstract methods from ModelBackend (must be implemented
    # by concrete subclasses): generate, stream_generate, supports_tools,
    # supports_reasoning, supports_streaming, capabilities
    # ------------------------------------------------------------------
