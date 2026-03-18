"""ConfigManager — TOML-based configuration read/write."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import toml
from filelock import FileLock

from localagentcli.config.defaults import (
    coerce_value,
    get_default_config,
    validate_config_value,
)


class ConfigManager:
    """Manages the global TOML configuration file."""

    def __init__(self, config_path: Path | None = None):
        self._path = config_path or Path.home() / ".localagent" / "config.toml"
        self._lock = FileLock(str(self._path) + ".lock")
        self._config: dict = {}

    def load(self) -> None:
        """Load config from disk. Creates default config if file doesn't exist."""
        if self._path.exists():
            with self._lock:
                with open(self._path, "r", encoding="utf-8") as f:
                    self._config = toml.load(f)
            # Merge defaults for any missing keys
            defaults = get_default_config()
            self._merge_defaults(defaults, self._config)
        else:
            self.reset_to_defaults()
            self.save()

    def save(self) -> None:
        """Write current config to disk."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            with open(self._path, "w", encoding="utf-8") as f:
                toml.dump(self._config, f)

    def get(self, key: str, default: Any = None) -> Any:
        """Get a config value by dotted key (e.g., 'general.default_mode')."""
        parts = key.split(".")
        current = self._config
        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return default
        return current

    def set(self, key: str, value: Any) -> None:
        """Set a config value by dotted key. Validates the key and value type."""
        value = coerce_value(key, value)
        valid, error = validate_config_value(key, value)
        if not valid:
            raise ValueError(error)

        parts = key.split(".")
        current = self._config
        for part in parts[:-1]:
            if part not in current:
                current[part] = {}
            current = current[part]
        current[parts[-1]] = value
        self.save()

    def get_all(self) -> dict:
        """Return the full config as a deep copy."""
        return copy.deepcopy(self._config)

    def reset_to_defaults(self) -> None:
        """Reset all config values to their defaults."""
        self._config = get_default_config()

    def _merge_defaults(self, defaults: dict, config: dict) -> None:
        """Merge default values into config for any missing keys (in-place)."""
        for key, value in defaults.items():
            if key not in config:
                config[key] = value
            elif isinstance(value, dict) and isinstance(config[key], dict):
                self._merge_defaults(value, config[key])
