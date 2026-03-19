"""Tests for all command handlers (help, status, config, setup, session, exit)."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

from rich.console import Console

from localagentcli.commands import (
    config_cmd,
    exit_cmd,
    set_cmd,
    setup_cmd,
)
from localagentcli.commands import (
    help as help_cmd,
)
from localagentcli.commands import (
    hf_token as hf_token_cmd,
)
from localagentcli.commands import (
    providers as providers_cmd,
)
from localagentcli.commands import (
    session as session_cmd,
)
from localagentcli.commands import (
    status as status_cmd,
)
from localagentcli.commands.router import CommandRouter
from localagentcli.models.detector import HardwareDetector
from localagentcli.models.registry import ModelRegistry
from localagentcli.providers.keys import KeyManager
from localagentcli.providers.registry import ProviderRegistry
from localagentcli.session.state import Message
from localagentcli.shell.prompt import SelectionOption


def _make_router(config, session_manager, tmp_path=None):
    """Create a fully-registered router for testing."""
    router = CommandRouter()
    console = Console(force_terminal=True)
    help_cmd.register(router)
    status_cmd.register(router, session_manager, config)
    config_cmd.register(router, config)
    setup_cmd.register(router, config, session_manager, console)
    session_cmd.register(router, session_manager)
    exit_cmd.register(router)
    if tmp_path is not None:
        secrets_dir = tmp_path / "secrets"
        secrets_dir.mkdir(exist_ok=True)
        km = KeyManager(secrets_dir)
        km._keyring_available = False
        registry = ProviderRegistry(config, km)
        model_registry = ModelRegistry(tmp_path / "registry.json")
        hf_token_cmd.register(router, km)
        providers_cmd.register(router, registry, km, session_manager, console)
        set_cmd.register(
            router,
            model_registry,
            registry,
            HardwareDetector(),
            config,
            session_manager,
            console,
        )
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

    def test_help_shows_provider_group(self, config, session_manager, tmp_path):
        router = _make_router(config, session_manager, tmp_path)
        result = router.dispatch("help")
        assert result.success
        assert "Provider" in result.message
        assert "/providers" in result.message
        assert "/set" in result.message

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

    @patch("localagentcli.commands.config_cmd.supports_interactive_prompt", return_value=True)
    @patch("localagentcli.commands.config_cmd.select_option")
    def test_interactive_editor_sets_valid_choice(
        self,
        mock_select,
        _mock_interactive,
        config,
        session_manager,
    ):
        router = _make_router(config, session_manager)
        mock_select.side_effect = [
            SelectionOption(
                value="general.default_mode",
                label="general.default_mode",
                description='Current: "agent"',
            ),
            SelectionOption(value="chat", label="chat"),
        ]

        result = router.dispatch("config")

        assert result.success
        assert config.get("general.default_mode") == "chat"
        assert 'Set general.default_mode = "chat"' == result.message

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

    def test_set_string_value_from_remaining_tokens(self, config, session_manager):
        router = _make_router(config, session_manager)
        result = router.dispatch("config general.workspace /tmp/my project")
        assert result.success
        assert config.get("general.workspace") == "/tmp/my project"

    def test_set_invalid_value(self, config, session_manager):
        router = _make_router(config, session_manager)
        result = router.dispatch("config general.default_mode invalid")
        assert not result.success


class TestSetupCommand:
    """Tests for /setup."""

    @patch("localagentcli.commands.setup_cmd.Prompt.ask")
    @patch("localagentcli.commands.setup_cmd.supports_interactive_prompt", return_value=True)
    def test_setup_sets_config(self, _mock_interactive, mock_ask, config, session_manager):
        # Return workspace, mode, logging level
        mock_ask.side_effect = ["/tmp/workspace", "chat", "verbose"]

        router = _make_router(config, session_manager)
        result = router.dispatch("setup")
        assert result.success
        assert "Setup complete" in result.message
        assert config.get("general.default_mode") == "chat"
        assert config.get("general.workspace") == "/tmp/workspace"
        assert config.get("general.logging_level") == "verbose"

    @patch("localagentcli.commands.setup_cmd.supports_interactive_prompt", return_value=False)
    @patch("localagentcli.commands.setup_cmd.Prompt.ask")
    def test_setup_uses_defaults_without_tty(
        self, mock_ask, _mock_interactive, config, session_manager
    ):
        router = _make_router(config, session_manager)
        result = router.dispatch("setup")

        assert result.success
        mock_ask.assert_not_called()
        assert config.get("general.workspace") == "."
        assert config.get("general.default_mode") == "agent"
        assert config.get("general.logging_level") == "normal"


class TestHFTokenCommand:
    def test_hf_token_command_hides_after_set(self, config, session_manager, tmp_path):
        router = _make_router(config, session_manager, tmp_path)

        result = router.dispatch("hf-token test-token")

        assert result.success
        assert "saved" in result.message.lower()
        assert "hf-token" not in router.get_visible_commands()


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

    @patch("localagentcli.commands.session.supports_interactive_prompt", return_value=True)
    @patch("localagentcli.commands.session.select_option")
    def test_session_load_uses_picker_when_name_missing(
        self,
        mock_select,
        _mock_supports,
        config,
        session_manager,
    ):
        router = _make_router(config, session_manager)
        router.dispatch("session save chosen")
        session_manager.new_session()
        mock_select.return_value = MagicMock(value="chosen")

        result = router.dispatch("session load")

        assert result.success
        assert session_manager.current.name == "chosen"

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
