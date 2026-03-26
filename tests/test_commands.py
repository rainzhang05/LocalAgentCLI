"""Tests for all command handlers (help, status, config, setup, session, exit)."""

from __future__ import annotations

import os
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
from localagentcli.commands import mcp as mcp_cmd
from localagentcli.commands import (
    plugin as plugin_cmd,
)
from localagentcli.commands import (
    providers as providers_cmd,
)
from localagentcli.commands import (
    session as session_cmd,
)
from localagentcli.commands import skills as skills_cmd
from localagentcli.commands import (
    status as status_cmd,
)
from localagentcli.commands.router import CommandRouter
from localagentcli.mcp import McpManager
from localagentcli.models.detector import HardwareDetector
from localagentcli.models.registry import ModelRegistry
from localagentcli.plugins import PluginManager
from localagentcli.providers.keys import KeyManager
from localagentcli.providers.registry import ProviderRegistry
from localagentcli.session.state import Message
from localagentcli.shell.prompt import SelectionOption
from localagentcli.skills import SKILL_FILENAME, SkillsManager


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
        mcp_manager = McpManager.from_config(config.get("mcp_servers", {}))
        plugin_manager = PluginManager(tmp_path / "installed_plugins")
        skills_manager = SkillsManager(tmp_path / "skills")
        model_registry = ModelRegistry(tmp_path / "registry.json")
        hf_token_cmd.register(router, km)
        mcp_cmd.register(router, mcp_manager, km)
        plugin_cmd.register(router, plugin_manager, lambda: tmp_path)
        skills_cmd.register(router, skills_manager)
        providers_cmd.register(router, registry, km, session_manager, config, console)
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
        assert "Manage saved sessions" in result.message
        assert "Usage: /session <new|save|load|list|clear>" in result.message

    def test_help_shows_provider_group(self, config, session_manager, tmp_path):
        router = _make_router(config, session_manager, tmp_path)
        result = router.dispatch("help")
        assert result.success
        assert "Provider" in result.message
        assert "/providers" in result.message
        assert "/set" in result.message
        assert "/mcp" in result.message

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
        assert "Agent route:" in result.message
        assert "idle" in result.message
        assert "Pending tool:" in result.message
        assert "(none)" in result.message
        assert "Undo ready:" in result.message

    def test_toolbar_shows_idle_agent_label_when_no_active_task(self):
        snapshot = status_cmd.build_status_snapshot(
            mode="agent",
            target="(none)",
            workspace="~/repo",
            session_name="(unsaved)",
            approval_mode="balanced",
            message_count=0,
        )

        toolbar = status_cmd.format_status_toolbar(snapshot)

        assert "agent: idle" in toolbar

    def test_shows_agent_task_state_and_undo_count(self, config, session_manager):
        session_manager.current.metadata["agent_task_state"] = {
            "route": "single_step_task",
            "phase": "waiting_approval",
            "step_index": 1,
            "step_description": "Patch app.py",
            "pending_tool": "patch_apply",
            "rollback_count": 3,
        }
        router = _make_router(config, session_manager)

        result = router.dispatch("status")

        assert result.success
        assert "Agent route:" in result.message
        assert "single-step task" in result.message
        assert "Pending tool:" in result.message
        assert "Undo ready:" in result.message

    def test_shows_retry_wait_and_error_details(self, config, session_manager):
        session_manager.current.metadata["agent_task_state"] = {
            "route": "multi_step_task",
            "phase": "retrying",
            "step_index": 2,
            "step_description": "Run test suite",
            "wait_reason": "retrying after recent failure",
            "retry_count": 2,
            "last_error": "pytest exited with code 1",
        }
        router = _make_router(config, session_manager)

        result = router.dispatch("status")

        assert result.success
        assert "Wait reason:" in result.message
        assert "Retries:" in result.message
        assert "Last error:" in result.message

    def test_toolbar_shows_retry_badge_for_retrying_phase(self):
        snapshot = status_cmd.build_status_snapshot(
            mode="agent",
            target="(none)",
            workspace="~/repo",
            session_name="(unsaved)",
            approval_mode="balanced",
            message_count=0,
            agent_route="multi_step_task",
            agent_phase="retrying",
            agent_retry_count=3,
        )

        toolbar = status_cmd.format_status_toolbar(snapshot)

        assert "agent: multi-step task/retrying/retry 3" in toolbar


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

    @patch("localagentcli.commands.config_cmd.prompt_text", return_value="/tmp/project")
    @patch("localagentcli.commands.config_cmd.select_option")
    @patch("localagentcli.commands.config_cmd.supports_interactive_prompt", return_value=True)
    def test_interactive_editor_uses_prompt_text_for_free_form_values(
        self,
        _mock_interactive,
        mock_select,
        mock_prompt_text,
        config,
        session_manager,
    ):
        router = _make_router(config, session_manager)
        mock_select.return_value = SelectionOption(
            value="general.workspace",
            label="general.workspace",
            description='Current: "."',
        )

        result = router.dispatch("config")

        assert result.success
        assert config.get("general.workspace") == "/tmp/project"
        mock_prompt_text.assert_called_once()


class TestSetupCommand:
    """Tests for /setup."""

    @patch("localagentcli.commands.setup_cmd.select_option")
    @patch("localagentcli.commands.setup_cmd.prompt_text")
    @patch("localagentcli.commands.setup_cmd.supports_interactive_prompt", return_value=True)
    def test_setup_sets_config(
        self,
        _mock_interactive,
        mock_prompt_text,
        mock_select_option,
        config,
        session_manager,
    ):
        mock_prompt_text.return_value = "/tmp/workspace"
        mock_select_option.side_effect = [
            SelectionOption(value="chat", label="chat"),
            SelectionOption(value="verbose", label="verbose"),
        ]

        router = _make_router(config, session_manager)
        result = router.dispatch("setup")
        assert result.success
        assert "Setup complete" in result.message
        assert config.get("general.default_mode") == "chat"
        assert config.get("general.workspace") == "/tmp/workspace"
        assert config.get("general.logging_level") == "verbose"

    @patch("localagentcli.commands.setup_cmd.prompt_text")
    @patch("localagentcli.commands.setup_cmd.supports_interactive_prompt", return_value=False)
    def test_setup_uses_defaults_without_tty(
        self, _mock_interactive, mock_prompt_text, config, session_manager
    ):
        router = _make_router(config, session_manager)
        result = router.dispatch("setup")

        assert result.success
        mock_prompt_text.assert_not_called()
        assert config.get("general.workspace") == "."
        assert config.get("general.default_mode") == "agent"
        assert config.get("general.logging_level") == "normal"


class TestHFTokenCommand:
    @patch("localagentcli.commands.hf_token.prompt_secret", return_value="interactive-token")
    @patch("localagentcli.commands.hf_token.supports_interactive_prompt", return_value=True)
    def test_hf_token_command_uses_secret_prompt(
        self,
        _mock_interactive,
        mock_prompt_secret,
        config,
        session_manager,
        tmp_path,
    ):
        router = _make_router(config, session_manager, tmp_path)

        result = router.dispatch("hf-token")

        assert result.success
        assert os.environ["HF_TOKEN"] == "interactive-token"
        mock_prompt_secret.assert_called_once_with("Hugging Face token")

    def test_hf_token_command_remains_visible_after_set(self, config, session_manager, tmp_path):
        router = _make_router(config, session_manager, tmp_path)

        result = router.dispatch("hf-token test-token")

        assert result.success
        assert "saved" in result.message.lower()
        assert "hf-token" in router.get_visible_commands()

    def test_hf_token_command_can_replace_existing_token(self, config, session_manager, tmp_path):
        router = _make_router(config, session_manager, tmp_path)

        first = router.dispatch("hf-token first-token")
        second = router.dispatch("hf-token second-token")

        assert first.success
        assert second.success
        assert os.environ["HF_TOKEN"] == "second-token"


class TestMcpCommands:
    def test_mcp_list_reports_configured_servers(self, config, session_manager, tmp_path):
        config._config["mcp_servers"] = {
            "demo": {"command": "python", "args": ["fake.py"]},
            "remote": {"transport": "http", "url": "http://127.0.0.1:8123/mcp"},
        }
        router = _make_router(config, session_manager, tmp_path)

        result = router.dispatch("mcp list")

        assert result.success
        assert "demo" in result.message
        assert "remote" in result.message

    def test_mcp_login_and_logout_manage_key_storage(self, config, session_manager, tmp_path):
        config._config["mcp_servers"] = {
            "demo": {"transport": "http", "url": "http://127.0.0.1:8123/mcp"}
        }
        router = _make_router(config, session_manager, tmp_path)

        login = router.dispatch("mcp login demo test-token")
        assert login.success

        secrets_dir = tmp_path / "secrets"
        km = KeyManager(secrets_dir)
        km._keyring_available = False
        assert km.retrieve_key("mcp_server:demo") == "test-token"

        logout = router.dispatch("mcp logout demo")
        assert logout.success
        assert km.retrieve_key("mcp_server:demo") is None


class TestPluginCommands:
    def test_plugin_list_shows_empty_state(self, config, session_manager, tmp_path):
        router = _make_router(config, session_manager, tmp_path)

        result = router.dispatch("plugin list")

        assert result.success
        assert "No local plugins installed" in result.message

    def test_plugin_install_list_remove_round_trip(self, config, session_manager, tmp_path):
        src_dir = tmp_path / "source_plugin"
        src_dir.mkdir()
        (src_dir / "README.md").write_text("plugin", encoding="utf-8")

        router = _make_router(config, session_manager, tmp_path)

        install = router.dispatch(f"plugin install {src_dir} demo")
        assert install.success

        listed = router.dispatch("plugin list")
        assert listed.success
        assert "demo" in listed.message

        removed = router.dispatch("plugin remove demo")
        assert removed.success

        listed_again = router.dispatch("plugin list")
        assert "No local plugins installed" in listed_again.message

    def test_plugin_discover_and_sync_from_workspace(self, config, session_manager, tmp_path):
        (tmp_path / "plugins" / "workspace_demo").mkdir(parents=True)
        (tmp_path / "plugins" / "workspace_demo" / "README.md").write_text(
            "workspace plugin",
            encoding="utf-8",
        )

        router = _make_router(config, session_manager, tmp_path)

        discover = router.dispatch("plugin discover")
        assert discover.success
        assert "workspace_demo" in discover.message

        sync = router.dispatch("plugin sync")
        assert sync.success
        assert "workspace_demo" in sync.message


class TestSkillsCommands:
    def test_skills_list_shows_empty_state(self, config, session_manager, tmp_path):
        router = _make_router(config, session_manager, tmp_path)

        result = router.dispatch("skills list")

        assert result.success
        assert "No installed skills" in result.message

    def test_skills_install_list_remove_round_trip(self, config, session_manager, tmp_path):
        source = tmp_path / "source_skill"
        source.mkdir()
        (source / SKILL_FILENAME).write_text("Prefer minimal diffs.", encoding="utf-8")

        router = _make_router(config, session_manager, tmp_path)

        install = router.dispatch(f"skills install {source} minimal")
        assert install.success

        listed = router.dispatch("skills list")
        assert listed.success
        assert "minimal" in listed.message

        removed = router.dispatch("skills remove minimal")
        assert removed.success


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
        assert "/help session" in result.message


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
