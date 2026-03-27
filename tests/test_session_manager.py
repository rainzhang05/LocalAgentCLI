"""Tests for localagentcli.session.manager."""

from __future__ import annotations

import json
import os
from datetime import datetime

import pytest

from localagentcli.session.manager import SessionManager
from localagentcli.session.state import Message


class TestSessionManagerNewSession:
    """Tests for creating new sessions."""

    def test_creates_session(self, session_manager):
        session = session_manager.current
        assert session.id
        assert session.mode == "agent"
        assert session.history == []

    def test_uses_config_defaults(self, session_manager):
        session = session_manager.current
        assert session.workspace == "."

    def test_new_session_replaces_current(self, session_manager):
        old_id = session_manager.current.id
        session_manager.new_session()
        assert session_manager.current.id != old_id

    def test_new_session_uses_resolved_default_target(self, storage, config):
        config.set("provider.active_provider", "")
        config.set("model.active_model", "missing@v1")
        manager = SessionManager(
            storage.sessions_dir,
            config,
            default_target_resolver=lambda provider, model: ("", "fallback@v1"),
        )

        session = manager.new_session()

        assert session.model == "fallback@v1"
        assert session.provider == ""
        assert config.get("model.active_model") == "fallback@v1"

    def test_new_session_records_default_target_repair_warning(self, storage, config):
        config.set("provider.active_provider", "")
        config.set("model.active_model", "missing@v1")
        manager = SessionManager(
            storage.sessions_dir,
            config,
            default_target_resolver=lambda provider, model: ("", "fallback@v1"),
        )

        manager.new_session()

        warning = manager.consume_default_target_warning()
        assert warning == (
            "Default target repaired: missing@v1 was unavailable, so LocalAgentCLI "
            "switched to fallback@v1."
        )
        assert manager.consume_default_target_warning() == ""


class TestSessionManagerSaveLoad:
    """Tests for saving and loading sessions."""

    def test_save_with_name(self, session_manager):
        path = session_manager.save_session("test-session")
        assert path.exists()
        assert path.name == "test-session.json"

    def test_save_without_name(self, session_manager):
        path = session_manager.save_session()
        assert path.exists()
        assert path.stem.startswith("session_")

    def test_save_updates_name(self, session_manager):
        session_manager.save_session("named")
        assert session_manager.current.name == "named"

    def test_load_restores_session(self, session_manager):
        session_manager.current.history.append(
            Message(role="user", content="hello", timestamp=datetime.now())
        )
        session_manager.save_session("loadtest")

        session_manager.new_session()
        assert len(session_manager.current.history) == 0

        session_manager.load_session("loadtest")
        assert len(session_manager.current.history) == 1
        assert session_manager.current.history[0].content == "hello"

    def test_load_nonexistent_raises(self, session_manager):
        with pytest.raises(FileNotFoundError):
            session_manager.load_session("nonexistent")

    def test_save_sets_message_count(self, session_manager):
        session_manager.current.history.append(
            Message(role="user", content="hi", timestamp=datetime.now())
        )
        session_manager.save_session("counted")
        assert session_manager.current.metadata["message_count"] == 1

    def test_fork_session_creates_new_id_and_preserves_history(self, session_manager):
        session_manager.current.history.append(
            Message(role="user", content="hello", timestamp=datetime.now())
        )
        session_manager.save_session("base")
        base_id = session_manager.current.id

        forked = session_manager.fork_session("base", "forked")

        assert forked.id != base_id
        assert forked.name == "forked"
        assert len(forked.history) == 1
        assert forked.history[0].content == "hello"
        assert forked.metadata.get("fork_parent_name") == "base"
        assert forked.metadata.get("fork_parent_id") == base_id
        assert forked.metadata.get("forked_at")
        startup_context = forked.metadata.get("fork_parent_startup_context")
        assert isinstance(startup_context, dict)
        assert startup_context.get("session", {}).get("model") == session_manager.current.model
        assert forked.metadata.get("context_diff_baseline") == startup_context

    def test_fork_startup_context_persists_after_save_load(self, session_manager):
        session_manager.current.history.append(
            Message(role="user", content="hello", timestamp=datetime.now())
        )
        session_manager.save_session("base")
        forked = session_manager.fork_session("base", "forked")
        startup_context = forked.metadata.get("fork_parent_startup_context")

        session_manager.save_session("forked")
        loaded = session_manager.load_session("forked")

        assert loaded.metadata.get("fork_parent_startup_context") == startup_context
        assert loaded.metadata.get("context_diff_baseline") == startup_context


class TestSessionManagerList:
    """Tests for listing sessions."""

    def test_empty_list(self, session_manager):
        result = session_manager.list_sessions()
        assert result == []

    def test_lists_saved_sessions(self, session_manager):
        session_manager.save_session("session1")
        session_manager.new_session()
        session_manager.save_session("session2")

        result = session_manager.list_sessions()
        names = [s["name"] for s in result]
        assert "session1" in names
        assert "session2" in names

    def test_list_contains_metadata(self, session_manager):
        session_manager.save_session("meta")
        result = session_manager.list_sessions()
        assert len(result) == 1
        assert "name" in result[0]
        assert "created_at" in result[0]
        assert "model" in result[0]
        assert "message_count" in result[0]

    def test_list_handles_corrupt_file(self, session_manager, storage):
        # Write a corrupt JSON file
        (storage.sessions_dir / "corrupt.json").write_text("not json")
        session_manager.save_session("valid")
        result = session_manager.list_sessions()
        # Should skip corrupt and return valid
        assert len(result) == 1
        assert result[0]["name"] == "valid"


class TestSessionManagerClear:
    """Tests for clearing sessions."""

    def test_clear_removes_history(self, session_manager):
        session_manager.current.history.append(
            Message(role="user", content="hi", timestamp=datetime.now())
        )
        session_manager.clear_session()
        assert session_manager.current.history == []

    def test_clear_removes_tasks(self, session_manager):
        session_manager.current.tasks.append("task1")
        session_manager.clear_session()
        assert session_manager.current.tasks == []

    def test_clear_keeps_model(self, session_manager):
        session_manager.current.model = "test-model"
        session_manager.clear_session()
        assert session_manager.current.model == "test-model"


class TestSessionManagerCurrent:
    """Tests for the current session property."""

    def test_raises_without_session(self, storage, config):
        sm = SessionManager(storage.sessions_dir, config)
        with pytest.raises(RuntimeError, match="No active session"):
            _ = sm.current


class TestSessionManagerConfigOverrides:
    """Tests for session-level config overrides."""

    def test_apply_override(self, session_manager):
        session_manager.apply_config_override("generation.temperature", 0.5)
        assert session_manager.current.config_overrides["generation.temperature"] == 0.5

    def test_effective_config_uses_override(self, session_manager):
        session_manager.apply_config_override("generation.temperature", 0.1)
        assert session_manager.get_effective_config("generation.temperature") == 0.1

    def test_effective_config_falls_back_to_global(self, session_manager):
        result = session_manager.get_effective_config("generation.temperature")
        assert result == 0.7  # Default from config


class TestSessionFormatVersion:
    """Session JSON format_version on save/load."""

    def test_save_writes_format_version(self, session_manager):
        session_manager.save_session("v1")
        raw = json.loads((session_manager._dir / "v1.json").read_text(encoding="utf-8"))
        assert raw.get("format_version") == 1

    def test_load_legacy_without_format_version(self, session_manager, storage):
        legacy = {
            "id": "550e8400-e29b-41d4-a716-446655440000",
            "name": "legacy",
            "mode": "agent",
            "model": "",
            "provider": "",
            "workspace": ".",
            "history": [],
            "tasks": [],
            "pinned_instructions": [],
            "config_overrides": {},
            "created_at": "2025-01-15T10:29:55",
            "updated_at": "2025-01-15T10:29:55",
            "metadata": {},
        }
        path = storage.sessions_dir / "legacy.json"
        path.write_text(json.dumps(legacy), encoding="utf-8")

        session_manager.load_session("legacy")
        session_manager.save_session("legacy")

        roundtrip = json.loads(path.read_text(encoding="utf-8"))
        assert roundtrip.get("format_version") == 1


class TestSessionNamedAutosave:
    """Debounced named-session autosave."""

    def test_flush_persists_when_autosave_enabled(self, session_manager, config):
        config.set("sessions.autosave_named", True)
        session_manager.save_session("autosave")
        session_manager.current.history.append(
            Message(role="user", content="after", timestamp=datetime.now())
        )
        session_manager.flush_named_autosave()

        session_manager.cancel_named_autosave_timer()
        session_manager.load_session("autosave")
        assert len(session_manager.current.history) == 1
        assert session_manager.current.history[0].content == "after"

    def test_flush_skips_when_autosave_disabled(self, session_manager, config):
        config.set("sessions.autosave_named", False)
        session_manager.save_session("noauto")
        session_manager.current.history.append(
            Message(role="user", content="volatile", timestamp=datetime.now())
        )
        session_manager.flush_named_autosave()

        session_manager.cancel_named_autosave_timer()
        session_manager.load_session("noauto")
        assert session_manager.current.history == []

    def test_schedule_no_op_without_name(self, session_manager, config):
        config.set("sessions.autosave_named", True)
        session_manager.schedule_named_autosave()
        session_manager.cancel_named_autosave_timer()


class TestSessionUnnamedAutosave:
    def test_flush_persists_unnamed_session_when_enabled(self, session_manager, config):
        config.set("sessions.autosave_named", False)
        config.set("sessions.autosave_unnamed", True)
        session_manager.current.history.append(
            Message(role="user", content="volatile", timestamp=datetime.now())
        )

        session_manager.flush_named_autosave()

        generated = session_manager.current.metadata.get("autosave_generated_name")
        assert isinstance(generated, str)
        assert generated.startswith("autosave_")
        assert session_manager.current.name is None

        restored = session_manager.load_session(generated)
        assert len(restored.history) == 1
        assert restored.history[0].content == "volatile"

    def test_prune_unnamed_autosaves_removes_old_json_and_runtime_logs(
        self, session_manager, config, storage
    ):
        config.set("sessions.autosave_unnamed", True)
        config.set("sessions.autosave_unnamed_retention_days", 1)
        session_manager.current.history.append(
            Message(role="user", content="old", timestamp=datetime.now())
        )
        session_manager.flush_named_autosave()

        generated = str(session_manager.current.metadata["autosave_generated_name"])
        autosave_path = storage.sessions_dir / f"{generated}.json"
        assert autosave_path.exists()

        runtime_dir = storage.cache_dir / "runtime-events"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        stale_log = runtime_dir / "stale.jsonl"
        stale_log.write_text("{}\n", encoding="utf-8")

        old_ts = datetime(2000, 1, 1).timestamp()
        os.utime(autosave_path, (old_ts, old_ts))
        os.utime(stale_log, (old_ts, old_ts))

        removed = session_manager.prune_unnamed_autosaves()

        assert removed >= 2
        assert not autosave_path.exists()
        assert not stale_log.exists()
