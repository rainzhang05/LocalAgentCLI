"""Tests for localagentcli.config.defaults."""

from __future__ import annotations

from localagentcli.config.defaults import (
    CONFIG_SCHEMA,
    DEFAULT_CONFIG,
    coerce_value,
    get_default_config,
    validate_config_value,
)


class TestDefaultConfig:
    """Tests for default config structure."""

    def test_has_all_sections(self):
        assert "general" in DEFAULT_CONFIG
        assert "model" in DEFAULT_CONFIG
        assert "provider" in DEFAULT_CONFIG
        assert "safety" in DEFAULT_CONFIG
        assert "generation" in DEFAULT_CONFIG
        assert "timeouts" in DEFAULT_CONFIG
        assert "shell" in DEFAULT_CONFIG
        assert "providers" in DEFAULT_CONFIG
        assert "sessions" in DEFAULT_CONFIG

    def test_default_mode_is_agent(self):
        assert DEFAULT_CONFIG["general"]["default_mode"] == "agent"

    def test_default_temperature(self):
        assert DEFAULT_CONFIG["generation"]["temperature"] == 0.7

    def test_get_default_config_returns_deep_copy(self):
        c1 = get_default_config()
        c2 = get_default_config()
        c1["general"]["default_mode"] = "chat"
        assert c2["general"]["default_mode"] == "agent"


class TestValidateConfigValue:
    """Tests for config validation."""

    def test_valid_string_value(self):
        ok, msg = validate_config_value("general.default_mode", "chat")
        assert ok
        assert msg == ""

    def test_invalid_string_choice(self):
        ok, msg = validate_config_value("general.default_mode", "invalid")
        assert not ok
        assert "Invalid value" in msg

    def test_valid_float_value(self):
        ok, msg = validate_config_value("generation.temperature", 0.5)
        assert ok

    def test_float_out_of_range(self):
        ok, msg = validate_config_value("generation.temperature", 3.0)
        assert not ok

    def test_valid_int_value(self):
        ok, msg = validate_config_value("generation.max_tokens", 8192)
        assert ok

    def test_int_zero_invalid(self):
        ok, msg = validate_config_value("generation.max_tokens", 0)
        assert not ok

    def test_string_coercion_to_float(self):
        ok, msg = validate_config_value("generation.temperature", "0.5")
        assert ok

    def test_string_coercion_to_int(self):
        ok, msg = validate_config_value("generation.max_tokens", "8192")
        assert ok

    def test_bad_string_coercion(self):
        ok, msg = validate_config_value("generation.temperature", "notanumber")
        assert not ok

    def test_valid_bool_sessions_autosave(self):
        ok, msg = validate_config_value("sessions.autosave_named", True)
        assert ok
        assert msg == ""

    def test_bool_coercion_from_string(self):
        ok, msg = validate_config_value("sessions.autosave_named", "true")
        assert ok

    def test_sessions_debounce_positive(self):
        ok, msg = validate_config_value("sessions.autosave_debounce_seconds", 3)
        assert ok

    def test_sessions_debounce_zero_invalid(self):
        ok, msg = validate_config_value("sessions.autosave_debounce_seconds", 0)
        assert not ok

    def test_shell_persistent_details_lane_bool(self):
        ok, msg = validate_config_value("shell.persistent_details_lane", True)
        assert ok
        assert msg == ""

    def test_shell_persistent_details_lane_string_coercion(self):
        ok, msg = validate_config_value("shell.persistent_details_lane", "false")
        assert ok
        assert msg == ""

    def test_unknown_key(self):
        ok, msg = validate_config_value("nonexistent.key", "value")
        assert not ok
        assert "Unknown config key" in msg

    def test_wrong_type(self):
        ok, msg = validate_config_value("generation.temperature", [1, 2])
        assert not ok
        assert "expects float" in msg

    def test_string_value_no_validator(self):
        ok, msg = validate_config_value("general.workspace", "/any/path")
        assert ok

    def test_sandbox_mode_valid_values(self):
        for value in ("workspace-write", "read-only", "danger-full-access"):
            ok, msg = validate_config_value("safety.sandbox_mode", value)
            assert ok, msg
            assert msg == ""

    def test_sandbox_mode_invalid_uses_parse_error_message(self):
        ok, msg = validate_config_value("safety.sandbox_mode", "nope")
        assert not ok
        assert "Invalid sandbox mode" in msg

    def test_all_schema_keys_have_defaults(self):
        """Every key in the schema should exist in the default config."""
        defaults = get_default_config()
        for key in CONFIG_SCHEMA:
            parts = key.split(".")
            current = defaults
            for part in parts:
                assert part in current, f"Schema key '{key}' not in defaults"
                current = current[part]


class TestCoerceValue:
    """Tests for value coercion."""

    def test_coerce_string_to_float(self):
        assert coerce_value("generation.temperature", "0.5") == 0.5

    def test_coerce_string_to_int(self):
        assert coerce_value("generation.max_tokens", "4096") == 4096

    def test_no_coercion_for_string_type(self):
        assert coerce_value("general.default_mode", "chat") == "chat"

    def test_no_coercion_needed_for_correct_type(self):
        assert coerce_value("generation.temperature", 0.5) == 0.5

    def test_unknown_key_returns_original(self):
        assert coerce_value("unknown.key", "value") == "value"

    def test_bad_coercion_returns_original(self):
        assert coerce_value("generation.temperature", "bad") == "bad"

    def test_coerce_string_to_bool(self):
        assert coerce_value("sessions.autosave_named", "true") is True
        assert coerce_value("sessions.autosave_named", "false") is False

    def test_coerce_shell_bool(self):
        assert coerce_value("shell.persistent_details_lane", "true") is True
        assert coerce_value("shell.persistent_details_lane", "false") is False
