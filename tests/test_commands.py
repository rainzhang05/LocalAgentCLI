"""Tests for all command handlers (help, status, config, setup, session, exit)."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

from rich.console import Console

from localagentcli.commands import (
    config_cmd,
    exit_cmd,
    setup_cmd,
)
from localagentcli.commands import (
    help as help_cmd,
)
from localagentcli.commands import (
    session as session_cmd,
)
from localagentcli.commands import (
    status as status_cmd,
)
from localagentcli.commands.router import CommandRouter
from localagentcli.session.state import Message


def _make_router(config, session_manager):
    """Create a fully-registered router for testing."""
    router = CommandRouter()
    console = Console(force_terminal=True)
    help_cmd.register(router)
    status_cmd.register(router, session_manager, config)
    config_cmd.register(router, config)
    setup_cmd.register(router, config, session_manager, console)
    session_cmd.register(router, session_manager)
    exit_cmd.register(router)
    return router


class TestHelpCommand:
    """Tests for /help."""

    def test_help_lists_commands(self, config, session_manager):
        router = _make_router(config, session_manager)
        result = router.dispatch("help")
        assert result.success
        assert "Available commands" in result.message
        assert "/help" in result.message
        assert "/exit" in result.message

    def test_help_specific_command(self, config, session_manager):
        router = _make_router(config, session_manager)
        result = router.dispatch("help config")
        assert result.success
        assert "/config" in result.message

    def test_help_session_subcommands(self, config, session_manager):
        router = _make_router(config, session_manager)
        result = router.dispatch("help session")
        assert result.success
        assert "session save" in result.message
        assert "session load" in result.message

    def test_help_unknown_command(self, config, session_manager):
        router = _make_router(config, session_manager)
        result = router.dispatch("help nonexistent")
        assert not result.success
        assert "Unknown command" in result.message


class TestStatusCommand:
    """Tests for /status."""

    def test_shows_session_state(self, config, session_manager):
        router = _make_router(config, session_manager)
        result = router.dispatch("status")
        assert result.success
        assert "Mode:" in result.message
        assert "agent" in result.message
        assert "Workspace:" in result.message


class TestConfigCommand:
    """Tests for /config."""

    def test_show_all(self, config, session_manager):
        router = _make_router(config, session_manager)
        result = router.dispatch("config")
        assert result.success
        assert "Configuration" in result.message
        assert "default_mode" in result.message

    def test_show_key(self, config, session_manager):
        router = _make_router(config, session_manager)
        result = router.dispatch("config general.default_mode")
        assert result.success
        assert "agent" in result.message

    def test_show_unknown_key(self, config, session_manager):
        router = _make_router(config, session_manager)
        result = router.dispatch("config nonexistent.key")
        assert not result.success

    def test_set_value(self, config, session_manager):
        router = _make_router(config, session_manager)
        result = router.dispatch("config general.default_mode chat")
        assert result.success
        assert "chat" in result.message
        assert config.get("general.default_mode") == "chat"

    def test_set_invalid_value(self, config, session_manager):
        router = _make_router(config, session_manager)
        result = router.dispatch("config general.default_mode invalid")
        assert not result.success


class TestSetupCommand:
    """Tests for /setup."""

    @patch("localagentcli.commands.setup_cmd.Prompt.ask")
    def test_setup_sets_config(self, mock_ask, config, session_manager):
        # Return workspace, mode, logging level
        mock_ask.side_effect = ["/tmp/workspace", "chat", "verbose"]

        router = _make_router(config, session_manager)
        result = router.dispatch("setup")
        assert result.success
        assert "Setup complete" in result.message
        assert config.get("general.default_mode") == "chat"
        assert config.get("general.workspace") == "/tmp/workspace"
        assert config.get("general.logging_level") == "verbose"


class TestSessionCommands:
    """Tests for /session subcommands."""

    def test_session_new(self, config, session_manager):
        router = _make_router(config, session_manager)
        old_id = session_manager.current.id
        result = router.dispatch("session new")
        assert result.success
        assert session_manager.current.id != old_id

    def test_session_save(self, config, session_manager):
        router = _make_router(config, session_manager)
        result = router.dispatch("session save testsave")
        assert result.success
        assert "testsave" in result.message

    def test_session_list_empty(self, config, session_manager):
        router = _make_router(config, session_manager)
        result = router.dispatch("session list")
        assert result.success
        assert "No saved sessions" in result.message

    def test_session_list_with_sessions(self, config, session_manager):
        router = _make_router(config, session_manager)
        router.dispatch("session save listed")
        result = router.dispatch("session list")
        assert result.success
        assert "listed" in result.message

    def test_session_load(self, config, session_manager):
        router = _make_router(config, session_manager)
        session_manager.current.history.append(
            Message(role="user", content="persist me", timestamp=datetime.now())
        )
        router.dispatch("session save loadtest")
        router.dispatch("session new")
        assert len(session_manager.current.history) == 0

        result = router.dispatch("session load loadtest")
        assert result.success
        assert len(session_manager.current.history) == 1

    def test_session_load_missing(self, config, session_manager):
        router = _make_router(config, session_manager)
        result = router.dispatch("session load nonexistent")
        assert not result.success
        assert "not found" in result.message

    def test_session_load_no_name(self, config, session_manager):
        router = _make_router(config, session_manager)
        result = router.dispatch("session load")
        assert not result.success
        assert "required" in result.message

    def test_session_clear(self, config, session_manager):
        router = _make_router(config, session_manager)
        session_manager.current.history.append(
            Message(role="user", content="gone", timestamp=datetime.now())
        )
        result = router.dispatch("session clear")
        assert result.success
        assert len(session_manager.current.history) == 0

    def test_session_parent_without_subcommand(self, config, session_manager):
        router = _make_router(config, session_manager)
        result = router.dispatch("session")
        assert not result.success
        assert "subcommand" in result.message


class TestExitCommand:
    """Tests for /exit."""

    def test_returns_exit_action(self, config, session_manager):
        router = _make_router(config, session_manager)
        result = router.dispatch("exit")
        assert result.success
        assert result.data == {"action": "exit"}


class TestHelpTexts:
    """Verify all handlers have non-empty help_text."""

    def test_all_handlers_have_help(self, config, session_manager):
        router = _make_router(config, session_manager)
        for name, handler in router.get_commands().items():
            text = handler.help_text()
            assert text, f"Handler '{name}' has empty help_text"
            assert len(text) > 5, f"Handler '{name}' has trivial help_text"
