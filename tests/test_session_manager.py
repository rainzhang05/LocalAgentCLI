"""Tests for localagentcli.session.manager."""

from __future__ import annotations

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
