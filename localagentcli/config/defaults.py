"""Default configuration values and validation schema."""

from __future__ import annotations

import copy
from typing import Any

DEFAULT_CONFIG: dict = {
    "general": {
        "default_mode": "agent",
        "workspace": ".",
        "logging_level": "normal",
    },
    "model": {
        "active_model": "",
    },
    "provider": {
        "active_provider": "",
    },
    "safety": {
        "approval_mode": "balanced",
        "sandbox_mode": "workspace-write",
    },
    "generation": {
        "temperature": 0.7,
        "max_tokens": 4096,
        "top_p": 1.0,
    },
    "timeouts": {
        "shell_command": 120,
        "model_response": 300,
        "inactivity": 600,
    },
    "providers": {},
    "mcp_servers": {},
}

# Schema: maps dotted key -> (expected_type, optional_validator)
CONFIG_SCHEMA: dict[str, tuple[type, Any]] = {
    "general.default_mode": (str, lambda v: v in ("chat", "agent")),
    "general.workspace": (str, None),
    "general.logging_level": (str, lambda v: v in ("normal", "verbose", "debug")),
    "model.active_model": (str, None),
    "provider.active_provider": (str, None),
    "safety.approval_mode": (str, lambda v: v in ("balanced", "autonomous")),
    "safety.sandbox_mode": (
        str,
        lambda v: v in ("workspace-write", "read-only", "danger-full-access"),
    ),
    "generation.temperature": (float, lambda v: 0.0 <= v <= 2.0),
    "generation.max_tokens": (int, lambda v: v > 0),
    "generation.top_p": (float, lambda v: 0.0 <= v <= 1.0),
    "timeouts.shell_command": (int, lambda v: v > 0),
    "timeouts.model_response": (int, lambda v: v > 0),
    "timeouts.inactivity": (int, lambda v: v > 0),
}


def get_default_config() -> dict:
    """Return a deep copy of the default configuration."""
    return copy.deepcopy(DEFAULT_CONFIG)


def validate_config_value(key: str, value: Any) -> tuple[bool, str]:
    """Validate a config key and value against the schema.

    Returns (True, "") on success or (False, error_message) on failure.
    Attempts type coercion from strings for numeric types.
    """
    if key not in CONFIG_SCHEMA:
        valid_keys = ", ".join(sorted(CONFIG_SCHEMA.keys()))
        return False, f"Unknown config key: '{key}'. Valid keys: {valid_keys}"

    expected_type, validator = CONFIG_SCHEMA[key]

    # Attempt type coercion from string input
    if isinstance(value, str) and expected_type is not str:
        try:
            if expected_type is float:
                value = float(value)
            elif expected_type is int:
                value = int(value)
        except (ValueError, TypeError):
            return False, f"'{key}' expects {expected_type.__name__}, got '{value}'"

    if not isinstance(value, expected_type):
        return False, f"'{key}' expects {expected_type.__name__}, got {type(value).__name__}"

    if validator is not None and not validator(value):
        return False, f"Invalid value '{value}' for '{key}'"

    return True, ""


def coerce_value(key: str, value: Any) -> Any:
    """Coerce a string value to the expected type for a config key.

    Returns the coerced value, or the original if coercion is not needed.
    """
    if key not in CONFIG_SCHEMA:
        return value

    expected_type, _ = CONFIG_SCHEMA[key]

    if isinstance(value, str) and expected_type is not str:
        try:
            if expected_type is float:
                return float(value)
            elif expected_type is int:
                return int(value)
        except (ValueError, TypeError):
            pass

    return value
