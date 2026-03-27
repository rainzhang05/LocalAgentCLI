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

    def test_default_features_include_sqlite_session_store_toggle(self):
        assert DEFAULT_CONFIG["features"]["sqlite_session_store"] is False

    def test_default_sessions_include_unnamed_autosave_settings(self):
        sessions = DEFAULT_CONFIG["sessions"]
        assert sessions["autosave_unnamed"] is False
        assert sessions["autosave_unnamed_prefix"] == "autosave_"
        assert sessions["autosave_unnamed_retention_days"] == 14

    def test_default_mode_is_agent(self):
        assert DEFAULT_CONFIG["general"]["default_mode"] == "agent"

    def test_default_temperature(self):
        assert DEFAULT_CONFIG["generation"]["temperature"] == 0.7

    def test_default_os_sandbox_backend(self):
        assert DEFAULT_CONFIG["safety"]["os_sandbox_backend"] == "off"

    def test_default_sandbox_policy_override_fields(self):
        safety = DEFAULT_CONFIG["safety"]
        assert safety["sandbox_network_access"] == "auto"
        assert safety["sandbox_writable_roots"] == ""
        assert safety["os_sandbox_container_image"] == "python:3.12-slim"
        assert safety["os_sandbox_container_cpu_limit"] == ""
        assert safety["os_sandbox_container_memory_limit"] == ""

    def test_shell_ux_defaults_present(self):
        shell = DEFAULT_CONFIG["shell"]
        assert shell["thinking_indicator_enabled"] is True
        assert shell["thinking_indicator_style"] == "dots"
        assert shell["thinking_animation_interval_ms"] == 120
        assert shell["theme"] == "default"
        assert shell["notification_dedupe"] is True
        assert shell["startup_banner"] is True

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

    def test_reasoning_effort_valid_values(self):
        for value in ("", "low", "medium", "high"):
            ok, msg = validate_config_value("generation.reasoning_effort", value)
            assert ok, msg

    def test_reasoning_effort_invalid_value(self):
        ok, msg = validate_config_value("generation.reasoning_effort", "extreme")
        assert not ok

    def test_bad_string_coercion(self):
        ok, msg = validate_config_value("generation.temperature", "notanumber")
        assert not ok

    def test_valid_bool_sessions_autosave(self):
        ok, msg = validate_config_value("sessions.autosave_named", True)
        assert ok
        assert msg == ""

    def test_valid_bool_sessions_autosave_unnamed(self):
        ok, msg = validate_config_value("sessions.autosave_unnamed", True)
        assert ok
        assert msg == ""

    def test_sessions_autosave_unnamed_prefix_non_empty(self):
        ok, msg = validate_config_value("sessions.autosave_unnamed_prefix", "auto_")
        assert ok
        ok, msg = validate_config_value("sessions.autosave_unnamed_prefix", "")
        assert not ok

    def test_sessions_autosave_unnamed_retention_days_positive(self):
        ok, msg = validate_config_value("sessions.autosave_unnamed_retention_days", 7)
        assert ok
        ok, msg = validate_config_value("sessions.autosave_unnamed_retention_days", 0)
        assert not ok

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

    def test_shell_thinking_indicator_style_valid(self):
        for value in ("dots", "line", "pulse"):
            ok, msg = validate_config_value("shell.thinking_indicator_style", value)
            assert ok, msg

    def test_shell_thinking_indicator_style_invalid(self):
        ok, msg = validate_config_value("shell.thinking_indicator_style", "spiral")
        assert not ok

    def test_shell_thinking_animation_interval_minimum(self):
        ok, msg = validate_config_value("shell.thinking_animation_interval_ms", 60)
        assert ok, msg
        ok, msg = validate_config_value("shell.thinking_animation_interval_ms", 10)
        assert not ok

    def test_shell_theme_valid_values(self):
        for value in ("default", "high-contrast", "mono"):
            ok, msg = validate_config_value("shell.theme", value)
            assert ok, msg

    def test_shell_notification_dedupe_bool(self):
        ok, msg = validate_config_value("shell.notification_dedupe", True)
        assert ok
        assert msg == ""

    def test_shell_startup_banner_coercion(self):
        ok, msg = validate_config_value("shell.startup_banner", "true")
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

    def test_os_sandbox_backend_valid_values(self):
        for value in ("off", "auto", "macos-seatbelt", "linux-bwrap", "container-docker"):
            ok, msg = validate_config_value("safety.os_sandbox_backend", value)
            assert ok, msg

    def test_os_sandbox_backend_invalid_value(self):
        ok, _msg = validate_config_value("safety.os_sandbox_backend", "seatbelt")
        assert not ok

    def test_sandbox_network_access_valid_values(self):
        for value in ("auto", "allow", "deny"):
            ok, msg = validate_config_value("safety.sandbox_network_access", value)
            assert ok, msg

    def test_sandbox_network_access_invalid_value(self):
        ok, _msg = validate_config_value("safety.sandbox_network_access", "full")
        assert not ok

    def test_sandbox_writable_roots_accepts_string(self):
        ok, msg = validate_config_value("safety.sandbox_writable_roots", "tmp,src")
        assert ok, msg

    def test_container_image_must_be_non_empty(self):
        ok, msg = validate_config_value("safety.os_sandbox_container_image", "python:3.12")
        assert ok, msg
        ok, _msg = validate_config_value("safety.os_sandbox_container_image", "")
        assert not ok

    def test_container_resource_limit_fields_accept_strings(self):
        ok, msg = validate_config_value("safety.os_sandbox_container_cpu_limit", "1.5")
        assert ok, msg
        ok, msg = validate_config_value("safety.os_sandbox_container_memory_limit", "2g")
        assert ok, msg

    def test_all_schema_keys_have_defaults(self):
        """Every key in the schema should exist in the default config."""
        defaults = get_default_config()
        for key in CONFIG_SCHEMA:
            parts = key.split(".")
            current = defaults
            for part in parts:
                assert part in current, f"Schema key '{key}' not in defaults"
                current = current[part]

    def test_feature_key_valid_bool(self):
        ok, msg = validate_config_value("features.dummy_feature", True)
        assert ok, msg
        assert msg == ""

    def test_feature_key_string_coercion(self):
        for val in ("true", "1", "yes", "on"):
            ok, msg = validate_config_value("features.dummy_feature", val)
            assert ok
            assert msg == ""
        for val in ("false", "0", "no", "off"):
            ok, msg = validate_config_value("features.dummy_feature", val)
            assert ok
            assert msg == ""

    def test_feature_key_invalid_string(self):
        ok, msg = validate_config_value("features.dummy_feature", "invalid")
        assert not ok
        assert "expects bool" in msg

    def test_feature_key_wrong_type(self):
        ok, msg = validate_config_value("features.dummy_feature", 123)
        assert not ok
        assert "expects bool, got int" in msg


class TestCoerceValue:
    """Tests for value coercion."""

    def test_coerce_string_to_float(self):
        assert coerce_value("generation.temperature", "0.5") == 0.5

    def test_coerce_string_to_int(self):
        assert coerce_value("generation.max_tokens", "4096") == 4096

    def test_reasoning_effort_remains_string(self):
        assert coerce_value("generation.reasoning_effort", "medium") == "medium"

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

    def test_coerce_new_sessions_bool(self):
        assert coerce_value("sessions.autosave_unnamed", "true") is True
        assert coerce_value("sessions.autosave_unnamed", "false") is False

    def test_coerce_shell_bool(self):
        assert coerce_value("shell.persistent_details_lane", "true") is True
        assert coerce_value("shell.persistent_details_lane", "false") is False

    def test_coerce_new_shell_bool_fields(self):
        assert coerce_value("shell.thinking_indicator_enabled", "true") is True
        assert coerce_value("shell.notification_dedupe", "false") is False
        assert coerce_value("shell.startup_banner", "true") is True

    def test_coerce_features_bool(self):
        assert coerce_value("features.dummy_feature", "true") is True
        assert coerce_value("features.dummy_feature", "1") is True
        assert coerce_value("features.dummy_feature", "false") is False
        assert coerce_value("features.dummy_feature", "0") is False
        assert coerce_value("features.dummy_feature", "bad") == "bad"
