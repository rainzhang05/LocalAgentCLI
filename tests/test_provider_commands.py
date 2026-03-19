"""Tests for /providers command handlers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from localagentcli.commands.providers import (
    ProvidersAddHandler,
    ProvidersListHandler,
    ProvidersParentHandler,
    ProvidersRemoveHandler,
    ProvidersTestHandler,
    ProvidersUseHandler,
)
from localagentcli.config.manager import ConfigManager
from localagentcli.providers.base import ConnectionTestResult
from localagentcli.providers.keys import KeyManager
from localagentcli.providers.registry import (
    ProviderEntry,
    ProviderRegistry,
)
from localagentcli.session.manager import SessionManager

_PROMPT_PATH = "localagentcli.commands.providers.Prompt.ask"
_CONFIRM_PATH = "localagentcli.commands.providers.Confirm.ask"


@pytest.fixture
def secrets_dir(tmp_path: Path) -> Path:
    d = tmp_path / "secrets"
    d.mkdir()
    return d


@pytest.fixture
def key_manager(secrets_dir: Path) -> KeyManager:
    km = KeyManager(secrets_dir)
    km._keyring_available = False
    return km


@pytest.fixture
def registry(config: ConfigManager, key_manager: KeyManager) -> ProviderRegistry:
    return ProviderRegistry(config, key_manager)


def _add_openai(registry: ProviderRegistry) -> None:
    entry = ProviderEntry(
        name="openai",
        type="openai",
        base_url="https://api.openai.com/v1",
        default_model="gpt-4o",
    )
    registry.add(entry, "sk-test")


# ------------------------------------------------------------------
# ProvidersParentHandler tests
# ------------------------------------------------------------------


class TestProvidersParent:
    def test_shows_error_without_subcommand(self):
        handler = ProvidersParentHandler()
        result = handler.execute([])
        assert result.success is False
        assert "subcommand" in result.message

    def test_help_text(self):
        handler = ProvidersParentHandler()
        assert "list" in handler.help_text()
        assert "add" in handler.help_text()


# ------------------------------------------------------------------
# ProvidersListHandler tests
# ------------------------------------------------------------------


class TestProvidersList:
    def test_list_empty(self, registry: ProviderRegistry):
        handler = ProvidersListHandler(registry)
        result = handler.execute([])
        assert result.success is True
        assert "No providers configured" in result.message

    def test_list_with_providers(self, registry: ProviderRegistry):
        _add_openai(registry)
        handler = ProvidersListHandler(registry)
        result = handler.execute([])
        assert result.success is True
        assert "openai" in result.message
        assert "gpt-4o" in result.message

    def test_list_shows_active_marker(self, registry: ProviderRegistry):
        _add_openai(registry)
        registry.set_active("openai")
        handler = ProvidersListHandler(registry)
        result = handler.execute([])
        assert "* = active" in result.message

    def test_help_text(self, registry: ProviderRegistry):
        handler = ProvidersListHandler(registry)
        assert handler.help_text() != ""


# ------------------------------------------------------------------
# ProvidersAddHandler tests
# ------------------------------------------------------------------


_ADD_INPUTS = [
    "openai",
    "my-openai",
    "https://api.openai.com/v1",
    "sk-test-key",
    "gpt-4o",
]


class TestProvidersAdd:
    @patch(_CONFIRM_PATH, return_value=False)
    @patch(_PROMPT_PATH, side_effect=_ADD_INPUTS)
    def test_add_success(
        self,
        _mock_prompt: MagicMock,
        _mock_confirm: MagicMock,
        registry: ProviderRegistry,
        key_manager: KeyManager,
    ):
        console = MagicMock()
        handler = ProvidersAddHandler(registry, key_manager, console)
        result = handler.execute([])
        assert result.success is True
        assert "my-openai" in result.message
        assert registry.get("my-openai") is not None

    @patch(_CONFIRM_PATH, return_value=False)
    @patch(
        _PROMPT_PATH,
        side_effect=[
            "openai",
            "openai",
            "https://api.openai.com/v1",
            "sk-key",
            "gpt-4o",
        ],
    )
    def test_add_duplicate_name(
        self,
        _mock_prompt: MagicMock,
        _mock_confirm: MagicMock,
        registry: ProviderRegistry,
        key_manager: KeyManager,
    ):
        _add_openai(registry)
        console = MagicMock()
        handler = ProvidersAddHandler(registry, key_manager, console)
        result = handler.execute([])
        assert result.success is False
        assert "already exists" in result.message

    @patch(
        _PROMPT_PATH,
        side_effect=["openai", "test", "http://x", "", "m"],
    )
    def test_add_empty_api_key(
        self,
        _mock_prompt: MagicMock,
        registry: ProviderRegistry,
        key_manager: KeyManager,
    ):
        console = MagicMock()
        handler = ProvidersAddHandler(registry, key_manager, console)
        result = handler.execute([])
        assert result.success is False
        assert "required" in result.message

    @patch(_PROMPT_PATH, side_effect=KeyboardInterrupt)
    def test_add_cancelled(
        self,
        _mock_prompt: MagicMock,
        registry: ProviderRegistry,
        key_manager: KeyManager,
    ):
        console = MagicMock()
        handler = ProvidersAddHandler(registry, key_manager, console)
        result = handler.execute([])
        assert result.success is True
        assert "cancelled" in result.message

    def test_help_text(self, registry: ProviderRegistry, key_manager: KeyManager):
        handler = ProvidersAddHandler(registry, key_manager, MagicMock())
        assert handler.help_text() != ""


# ------------------------------------------------------------------
# ProvidersRemoveHandler tests
# ------------------------------------------------------------------


class TestProvidersRemove:
    def test_remove_success(self, registry: ProviderRegistry):
        _add_openai(registry)
        handler = ProvidersRemoveHandler(registry)
        result = handler.execute(["openai"])
        assert result.success is True
        assert registry.get("openai") is None

    def test_remove_no_name(self, registry: ProviderRegistry):
        handler = ProvidersRemoveHandler(registry)
        result = handler.execute([])
        assert result.success is False
        assert "required" in result.message

    def test_remove_nonexistent(self, registry: ProviderRegistry):
        handler = ProvidersRemoveHandler(registry)
        result = handler.execute(["nonexistent"])
        assert result.success is False
        assert "not found" in result.message

    def test_help_text(self, registry: ProviderRegistry):
        handler = ProvidersRemoveHandler(registry)
        assert handler.help_text() != ""

    @patch("localagentcli.commands.providers.supports_interactive_prompt", return_value=True)
    @patch("localagentcli.commands.providers.select_option")
    def test_remove_uses_picker_when_name_missing(
        self,
        mock_select,
        _mock_supports,
        registry: ProviderRegistry,
    ):
        _add_openai(registry)
        mock_select.return_value = MagicMock(value="openai")

        handler = ProvidersRemoveHandler(registry)
        result = handler.execute([])

        assert result.success is True
        assert registry.get("openai") is None


# ------------------------------------------------------------------
# ProvidersUseHandler tests
# ------------------------------------------------------------------


class TestProvidersUse:
    def test_use_success(
        self,
        registry: ProviderRegistry,
        session_manager: SessionManager,
    ):
        _add_openai(registry)
        handler = ProvidersUseHandler(registry, session_manager)
        result = handler.execute(["openai"])
        assert result.success is True
        assert session_manager.current.provider == "openai"
        assert session_manager.current.model == "gpt-4o"

    def test_use_no_name(
        self,
        registry: ProviderRegistry,
        session_manager: SessionManager,
    ):
        handler = ProvidersUseHandler(registry, session_manager)
        result = handler.execute([])
        assert result.success is False

    def test_use_nonexistent(
        self,
        registry: ProviderRegistry,
        session_manager: SessionManager,
    ):
        handler = ProvidersUseHandler(registry, session_manager)
        result = handler.execute(["nonexistent"])
        assert result.success is False
        assert "not found" in result.message

    def test_help_text(
        self,
        registry: ProviderRegistry,
        session_manager: SessionManager,
    ):
        handler = ProvidersUseHandler(registry, session_manager)
        assert handler.help_text() != ""


# ------------------------------------------------------------------
# ProvidersTestHandler tests
# ------------------------------------------------------------------


class TestProvidersTest:
    def test_test_success(
        self,
        registry: ProviderRegistry,
        session_manager: SessionManager,
    ):
        _add_openai(registry)
        handler = ProvidersTestHandler(registry, session_manager)
        mock_provider = MagicMock()
        mock_provider.test_connection.return_value = ConnectionTestResult(
            success=True, message="Connected.", latency_ms=50.0
        )
        with patch.object(registry, "create_provider", return_value=mock_provider):
            result = handler.execute(["openai"])
        assert result.success is True
        assert "50ms" in result.message

    def test_test_failure(
        self,
        registry: ProviderRegistry,
        session_manager: SessionManager,
    ):
        _add_openai(registry)
        handler = ProvidersTestHandler(registry, session_manager)
        mock_provider = MagicMock()
        mock_provider.test_connection.return_value = ConnectionTestResult(
            success=False, message="Auth failed."
        )
        with patch.object(registry, "create_provider", return_value=mock_provider):
            result = handler.execute(["openai"])
        assert result.success is False
        assert "Auth failed" in result.message

    def test_test_no_name_no_active(
        self,
        registry: ProviderRegistry,
        session_manager: SessionManager,
    ):
        handler = ProvidersTestHandler(registry, session_manager)
        result = handler.execute([])
        assert result.success is False
        assert "No active provider" in result.message

    def test_test_uses_session_provider(
        self,
        registry: ProviderRegistry,
        session_manager: SessionManager,
    ):
        _add_openai(registry)
        session_manager.current.provider = "openai"
        handler = ProvidersTestHandler(registry, session_manager)
        mock_provider = MagicMock()
        mock_provider.test_connection.return_value = ConnectionTestResult(
            success=True, message="OK", latency_ms=10.0
        )
        with patch.object(registry, "create_provider", return_value=mock_provider):
            result = handler.execute([])
        assert result.success is True

    def test_test_nonexistent(
        self,
        registry: ProviderRegistry,
        session_manager: SessionManager,
    ):
        handler = ProvidersTestHandler(registry, session_manager)
        result = handler.execute(["nonexistent"])
        assert result.success is False
        assert "not found" in result.message

    def test_test_create_provider_error(
        self,
        registry: ProviderRegistry,
        session_manager: SessionManager,
    ):
        _add_openai(registry)
        handler = ProvidersTestHandler(registry, session_manager)
        with patch.object(registry, "create_provider", side_effect=ValueError("no key")):
            result = handler.execute(["openai"])
        assert result.success is False
        assert "Failed to create" in result.message

    def test_help_text(
        self,
        registry: ProviderRegistry,
        session_manager: SessionManager,
    ):
        handler = ProvidersTestHandler(registry, session_manager)
        assert handler.help_text() != ""

    @patch("localagentcli.commands.providers.supports_interactive_prompt", return_value=True)
    @patch("localagentcli.commands.providers.select_option")
    def test_test_uses_picker_when_name_missing(
        self,
        mock_select,
        _mock_supports,
        registry: ProviderRegistry,
        session_manager: SessionManager,
    ):
        _add_openai(registry)
        mock_select.return_value = MagicMock(value="openai")
        handler = ProvidersTestHandler(registry, session_manager)
        mock_provider = MagicMock()
        mock_provider.test_connection.return_value = ConnectionTestResult(
            success=True, message="OK", latency_ms=10.0
        )

        with patch.object(registry, "create_provider", return_value=mock_provider):
            result = handler.execute([])

        assert result.success is True
