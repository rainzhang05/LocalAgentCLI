"""Tests for localagentcli.__main__ entry point."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from localagentcli.__main__ import main


class TestMain:
    """Tests for the main entry point."""

    @patch("localagentcli.__main__.ShellUI")
    @patch("localagentcli.__main__.ConfigManager")
    @patch("localagentcli.__main__.StorageManager")
    def test_main_initializes_and_runs(self, mock_storage_cls, mock_config_cls, mock_ui_cls):
        mock_storage = MagicMock()
        mock_storage.config_path = Path("/fake/.localagent/config.toml")
        mock_storage.config_path.exists = MagicMock(return_value=True)
        mock_storage_cls.return_value = mock_storage

        mock_config = MagicMock()
        mock_config_cls.return_value = mock_config

        mock_ui = MagicMock()
        mock_ui_cls.return_value = mock_ui

        main()

        mock_storage.initialize.assert_called_once()
        mock_config.load.assert_called_once()
        mock_ui.run.assert_called_once()

    @patch("localagentcli.__main__.ShellUI")
    @patch("localagentcli.__main__.ConfigManager")
    @patch("localagentcli.__main__.StorageManager")
    def test_main_detects_first_run(self, mock_storage_cls, mock_config_cls, mock_ui_cls):
        mock_storage = MagicMock()
        mock_storage.config_path = MagicMock()
        mock_storage.config_path.exists.return_value = False
        mock_storage_cls.return_value = mock_storage

        mock_config = MagicMock()
        mock_config_cls.return_value = mock_config

        mock_ui = MagicMock()
        mock_ui_cls.return_value = mock_ui

        main()

        # Should pass first_run=True to ShellUI
        _, kwargs = mock_ui_cls.call_args
        assert kwargs["first_run"] is True

    @patch("localagentcli.__main__.ShellUI")
    @patch("localagentcli.__main__.ConfigManager")
    @patch("localagentcli.__main__.StorageManager")
    def test_main_not_first_run(self, mock_storage_cls, mock_config_cls, mock_ui_cls):
        mock_storage = MagicMock()
        mock_storage.config_path = MagicMock()
        mock_storage.config_path.exists.return_value = True
        mock_storage_cls.return_value = mock_storage

        mock_config = MagicMock()
        mock_config_cls.return_value = mock_config

        mock_ui = MagicMock()
        mock_ui_cls.return_value = mock_ui

        main()

        _, kwargs = mock_ui_cls.call_args
        assert kwargs["first_run"] is False
