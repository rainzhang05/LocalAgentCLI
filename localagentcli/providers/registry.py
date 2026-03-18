"""ProviderRegistry — CRUD management of configured remote providers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from localagentcli.config.manager import ConfigManager
from localagentcli.providers.base import RemoteProvider
from localagentcli.providers.keys import KeyManager

VALID_PROVIDER_TYPES = ("openai", "anthropic", "rest")


@dataclass
class ProviderEntry:
    """A configured provider stored in config.toml."""

    name: str
    type: str  # "openai" | "anthropic" | "rest"
    base_url: str
    default_model: str
    options: dict = field(default_factory=dict)
    status: str = "configured"  # "configured" | "tested"
    added_at: str = ""

    def to_dict(self) -> dict:
        """Serialize to a dict for config storage."""
        return {
            "type": self.type,
            "base_url": self.base_url,
            "default_model": self.default_model,
            "options": self.options,
            "status": self.status,
            "added_at": self.added_at,
        }

    @classmethod
    def from_dict(cls, name: str, data: dict) -> ProviderEntry:
        """Deserialize from a config dict."""
        return cls(
            name=name,
            type=data.get("type", "openai"),
            base_url=data.get("base_url", ""),
            default_model=data.get("default_model", ""),
            options=data.get("options", {}),
            status=data.get("status", "configured"),
            added_at=data.get("added_at", ""),
        )


class ProviderRegistry:
    """Manages configured remote providers, stored in config.toml [providers]."""

    def __init__(self, config: ConfigManager, key_manager: KeyManager):
        self._config = config
        self._key_manager = key_manager

    def add(self, entry: ProviderEntry, api_key: str) -> None:
        """Add a provider to the registry and store its API key."""
        if entry.type not in VALID_PROVIDER_TYPES:
            raise ValueError(
                f"Invalid provider type '{entry.type}'. "
                f"Must be one of: {', '.join(VALID_PROVIDER_TYPES)}"
            )
        if not entry.added_at:
            entry.added_at = datetime.now(tz=timezone.utc).isoformat()

        providers = self._get_providers_section()
        providers[entry.name] = entry.to_dict()
        self._save_providers(providers)
        self._key_manager.store_key(entry.name, api_key)

    def remove(self, name: str) -> None:
        """Remove a provider and its stored credentials."""
        providers = self._get_providers_section()
        if name not in providers:
            raise KeyError(f"Provider '{name}' not found")
        del providers[name]
        self._save_providers(providers)
        self._key_manager.delete_key(name)

    def get(self, name: str) -> ProviderEntry | None:
        """Get a provider entry by name, or None if not found."""
        providers = self._get_providers_section()
        if name not in providers:
            return None
        return ProviderEntry.from_dict(name, providers[name])

    def list_providers(self) -> list[ProviderEntry]:
        """List all configured providers."""
        providers = self._get_providers_section()
        return [ProviderEntry.from_dict(name, data) for name, data in providers.items()]

    def update_status(self, name: str, status: str) -> None:
        """Update the status of a provider entry."""
        providers = self._get_providers_section()
        if name not in providers:
            raise KeyError(f"Provider '{name}' not found")
        providers[name]["status"] = status
        self._save_providers(providers)

    def get_active_name(self) -> str:
        """Return the name of the globally active provider."""
        return self._config.get("provider.active_provider", "") or ""

    def set_active(self, name: str) -> None:
        """Set the globally active provider in config."""
        if name and self.get(name) is None:
            raise KeyError(f"Provider '{name}' not found")
        # Write directly to config since provider.active_provider is schema-validated
        self._config.set("provider.active_provider", name)

    def create_provider(self, name: str) -> RemoteProvider:
        """Instantiate a RemoteProvider subclass from a registry entry."""
        entry = self.get(name)
        if entry is None:
            raise KeyError(f"Provider '{name}' not found")

        api_key = self._key_manager.retrieve_key(name)
        if api_key is None:
            raise ValueError(f"No API key found for provider '{name}'")

        return self._build_provider(entry, api_key)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_providers_section(self) -> dict:
        """Get the providers dict from config."""
        providers = self._config.get("providers", {})
        if not isinstance(providers, dict):
            return {}
        return providers

    def _save_providers(self, providers: dict) -> None:
        """Write the providers section back to config and save."""
        self._config._config["providers"] = providers
        self._config.save()

    def _build_provider(self, entry: ProviderEntry, api_key: str) -> RemoteProvider:
        """Build the correct provider subclass based on entry type."""
        provider: RemoteProvider
        if entry.type == "openai":
            from localagentcli.providers.openai import OpenAIProvider

            provider = OpenAIProvider(
                name=entry.name,
                base_url=entry.base_url,
                api_key=api_key,
                default_model=entry.default_model,
                options=entry.options,
            )
        elif entry.type == "anthropic":
            from localagentcli.providers.anthropic import AnthropicProvider

            provider = AnthropicProvider(
                name=entry.name,
                base_url=entry.base_url,
                api_key=api_key,
                default_model=entry.default_model,
                options=entry.options,
            )
        elif entry.type == "rest":
            from localagentcli.providers.rest import GenericRESTProvider

            provider = GenericRESTProvider(
                name=entry.name,
                base_url=entry.base_url,
                api_key=api_key,
                default_model=entry.default_model,
                options=entry.options,
            )
        else:
            raise ValueError(f"Unknown provider type: '{entry.type}'")
        return provider
