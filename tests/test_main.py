"""Tests for localagentcli.__main__ entry point."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from localagentcli.__main__ import main
from localagentcli.models.backends.base import StreamChunk


class TestMain:
    """Tests for the main entry point."""

    @patch("localagentcli.__main__.ShellUI")
    @patch("localagentcli.__main__.ConfigManager")
    @patch("localagentcli.__main__.StorageManager")
    def test_main_initializes_and_runs(self, mock_storage_cls, mock_config_cls, mock_ui_cls):
        mock_storage = MagicMock()
        mock_storage.config_path.exists.return_value = True
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

    @patch("localagentcli.__main__.SessionRuntime")
    @patch("localagentcli.__main__.SessionExecutionRuntime")
    @patch("localagentcli.__main__.SessionEventLog")
    @patch("localagentcli.__main__.RuntimeServices")
    @patch("localagentcli.__main__.ConfigManager")
    @patch("localagentcli.__main__.StorageManager")
    def test_main_exec_runs_non_interactive_turn(
        self,
        mock_storage_cls,
        mock_config_cls,
        mock_services_cls,
        mock_event_log_cls,
        mock_runtime_cls,
        mock_session_runtime_cls,
    ):
        mock_storage = MagicMock()
        mock_storage.config_path.exists.return_value = True
        mock_storage_cls.return_value = mock_storage

        mock_config = MagicMock()
        mock_config_cls.return_value = mock_config

        mock_services = MagicMock()
        mock_services_cls.create.return_value = mock_services

        exec_runtime = MagicMock()
        mock_runtime_cls.return_value = exec_runtime

        session_runtime = MagicMock()

        async def _exec_events():
            yield SimpleNamespace(
                type="stream_chunk",
                data=StreamChunk(text="chunk"),
                message="",
                to_dict=lambda: {"type": "stream_chunk"},
            )
            yield SimpleNamespace(
                type="turn_completed",
                data={"final_text": "chunk"},
                message="chunk",
                to_dict=lambda: {"type": "turn_completed"},
            )

        session_runtime.aiter_events.return_value = _exec_events()
        session_runtime.aclose = AsyncMock()
        mock_session_runtime_cls.return_value = session_runtime

        result = main(["exec", "hello", "world"])

        assert result == 0
        exec_runtime.sync_workspace_instruction.assert_called_once()
        session_runtime.submit.assert_called_once()
        session_runtime.aclose.assert_called_once()

    @patch("localagentcli.__main__.SessionRuntime")
    @patch("localagentcli.__main__.SessionExecutionRuntime")
    @patch("localagentcli.__main__.SessionEventLog")
    @patch("localagentcli.__main__.RuntimeServices")
    @patch("localagentcli.__main__.ConfigManager")
    @patch("localagentcli.__main__.StorageManager")
    def test_main_exec_loads_saved_session(
        self,
        mock_storage_cls,
        mock_config_cls,
        mock_services_cls,
        mock_event_log_cls,
        mock_runtime_cls,
        mock_session_runtime_cls,
    ):
        mock_storage = MagicMock()
        mock_storage.config_path.exists.return_value = True
        mock_storage_cls.return_value = mock_storage
        mock_config_cls.return_value = MagicMock()

        session_manager = MagicMock()
        session_manager.current.id = "session-1"
        services = MagicMock()
        services.session_manager = session_manager
        mock_services_cls.create.return_value = services
        mock_runtime_cls.return_value = MagicMock()

        session_runtime = MagicMock()

        async def _empty_events():
            if False:
                yield  # pragma: no cover

        session_runtime.aiter_events.return_value = _empty_events()
        session_runtime.aclose = AsyncMock()
        mock_session_runtime_cls.return_value = session_runtime

        main(["exec", "--session", "saved", "hello"])

        session_manager.load_session.assert_called_once_with("saved")
        session_manager.save_session.assert_called_once_with("saved")

    @patch("localagentcli.__main__.SessionRuntime")
    @patch("localagentcli.__main__.SessionExecutionRuntime")
    @patch("localagentcli.__main__.SessionEventLog")
    @patch("localagentcli.__main__.RuntimeServices")
    @patch("localagentcli.__main__.ConfigManager")
    @patch("localagentcli.__main__.StorageManager")
    def test_main_exec_forks_session(
        self,
        mock_storage_cls,
        mock_config_cls,
        mock_services_cls,
        mock_event_log_cls,
        mock_runtime_cls,
        mock_session_runtime_cls,
    ):
        mock_storage = MagicMock()
        mock_storage.config_path.exists.return_value = True
        mock_storage_cls.return_value = mock_storage
        mock_config_cls.return_value = MagicMock()

        session_manager = MagicMock()
        session_manager.current.id = "session-1"
        session_manager.fork_session.return_value = SimpleNamespace(name="saved_fork")
        services = MagicMock()
        services.session_manager = session_manager
        mock_services_cls.create.return_value = services
        mock_runtime_cls.return_value = MagicMock()

        session_runtime = MagicMock()

        async def _empty_events_fork():
            if False:
                yield  # pragma: no cover

        session_runtime.aiter_events.return_value = _empty_events_fork()
        session_runtime.aclose = AsyncMock()
        mock_session_runtime_cls.return_value = session_runtime

        main(["exec", "--fork", "saved", "hello"])

        session_manager.fork_session.assert_called_once_with("saved")
        session_manager.save_session.assert_called_once_with("saved_fork")

    @patch("localagentcli.__main__.SessionRuntime")
    @patch("localagentcli.__main__.SessionExecutionRuntime")
    @patch("localagentcli.__main__.SessionEventLog")
    @patch("localagentcli.__main__.RuntimeServices")
    @patch("localagentcli.__main__.ConfigManager")
    @patch("localagentcli.__main__.StorageManager")
    def test_main_exec_saves_session_on_keyboard_interrupt(
        self,
        mock_storage_cls,
        mock_config_cls,
        mock_services_cls,
        mock_event_log_cls,
        mock_runtime_cls,
        mock_session_runtime_cls,
    ):
        mock_storage = MagicMock()
        mock_storage.config_path.exists.return_value = True
        mock_storage_cls.return_value = mock_storage
        mock_config_cls.return_value = MagicMock()

        session_manager = MagicMock()
        session_manager.current.id = "session-1"
        services = MagicMock()
        services.session_manager = session_manager
        mock_services_cls.create.return_value = services

        exec_runtime = MagicMock()
        mock_runtime_cls.return_value = exec_runtime

        session_runtime = MagicMock()

        async def _raise_interrupt():
            raise KeyboardInterrupt()
            yield  # pragma: no cover

        session_runtime.aiter_events.return_value = _raise_interrupt()

        async def _empty_interrupt():
            if False:
                yield  # pragma: no cover

        session_runtime.ainterrupt.return_value = _empty_interrupt()
        session_runtime.aclose = AsyncMock()
        mock_session_runtime_cls.return_value = session_runtime

        result = main(["exec", "--session", "saved", "hello"])

        assert result == 1
        session_manager.load_session.assert_called_once_with("saved")
        session_manager.save_session.assert_called_once_with("saved")
        session_runtime.aclose.assert_called_once()

    @patch("localagentcli.__main__.SessionRuntime")
    @patch("localagentcli.__main__.SessionExecutionRuntime")
    @patch("localagentcli.__main__.SessionEventLog")
    @patch("localagentcli.__main__.RuntimeServices")
    @patch("localagentcli.__main__.ConfigManager")
    @patch("localagentcli.__main__.StorageManager")
    def test_main_exec_saves_session_when_sync_raises(
        self,
        mock_storage_cls,
        mock_config_cls,
        mock_services_cls,
        mock_event_log_cls,
        mock_runtime_cls,
        mock_session_runtime_cls,
    ):
        mock_storage = MagicMock()
        mock_storage.config_path.exists.return_value = True
        mock_storage_cls.return_value = mock_storage
        mock_config_cls.return_value = MagicMock()

        session_manager = MagicMock()
        session_manager.current.id = "session-1"
        services = MagicMock()
        services.session_manager = session_manager
        mock_services_cls.create.return_value = services

        exec_runtime = MagicMock()
        exec_runtime.sync_workspace_instruction.side_effect = RuntimeError("sync failed")
        mock_runtime_cls.return_value = exec_runtime

        session_runtime = MagicMock()
        session_runtime.aclose = AsyncMock()
        mock_session_runtime_cls.return_value = session_runtime

        result = main(["exec", "--session", "saved", "hello"])

        assert result == 1
        session_manager.save_session.assert_called_once_with("saved")
        session_runtime.aclose.assert_called_once()
