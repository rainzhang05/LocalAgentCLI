"""Packaging-oriented integration tests."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from localagentcli.models.registry import ModelEntry
from localagentcli.session.state import Message
from localagentcli.shell.prompt import SelectionOption
from localagentcli.shell.ui import ShellUI


def _register_model(ui: ShellUI, model_dir: Path, fmt: str = "gguf") -> str:
    """Register a test model and make it active for the current session."""
    entry = ModelEntry(
        name="demo-model",
        version="v1",
        format=fmt,
        path=str(model_dir),
        size_bytes=1024,
        metadata={"backend": fmt},
    )
    ui._model_registry.register(entry)
    ui._session_manager.current.model = "demo-model@v1"
    ui._session_manager.current.provider = ""
    return entry.path


class TestSessionLifecycleIntegration:
    def test_setup_save_and_load_round_trip(self, config, storage, tmp_path: Path):
        ui = ShellUI(config=config, storage=storage)
        session = ui._session_manager.current
        session.history.append(Message(role="user", content="persist me", timestamp=datetime.now()))

        workspace = str(tmp_path / "workspace")
        with (
            patch(
                "localagentcli.commands.setup_cmd.prompt_text",
                return_value=workspace,
            ),
            patch(
                "localagentcli.commands.setup_cmd.select_option",
                side_effect=[
                    SelectionOption(value="chat", label="chat"),
                    SelectionOption(value="debug", label="debug"),
                ],
            ),
            patch(
                "localagentcli.commands.setup_cmd.supports_interactive_prompt", return_value=True
            ),
        ):
            result = ui._router.dispatch("setup")

        assert result.success is True
        assert config.get("general.workspace") == workspace
        assert config.get("general.default_mode") == "chat"
        assert config.get("general.logging_level") == "debug"

        save_result = ui._router.dispatch("session save packaging-flow")
        assert save_result.success is True

        new_result = ui._router.dispatch("session new")
        assert new_result.success is True
        assert ui._session_manager.current.id != session.id
        assert ui._session_manager.current.history == []

        load_result = ui._router.dispatch("session load packaging-flow")
        assert load_result.success is True
        restored = ui._session_manager.current
        assert restored.mode == "chat"
        assert restored.workspace == workspace
        assert len(restored.history) == 1
        assert restored.history[0].content == "persist me"


class TestBackendAutoInstallIntegration:
    def test_missing_backend_dependencies_can_be_installed(self, config, storage, tmp_path: Path):
        ui = ShellUI(config=config, storage=storage)
        ui._console = MagicMock()
        model_dir = tmp_path / "model"
        model_dir.mkdir()
        model_path = _register_model(ui, model_dir)

        backend = MagicMock()

        with (
            patch(
                "localagentcli.runtime.core.check_backend_dependencies",
                return_value=(False, ["llama_cpp"]),
            ),
            patch(
                "localagentcli.runtime.core.install_backend_dependencies",
                return_value=(True, "ok"),
            ) as mock_install,
            patch("localagentcli.shell.ui.confirm_choice", return_value=True) as mock_confirm,
            patch.object(
                ui._runtime, "_create_backend", return_value=backend
            ) as mock_create_backend,
        ):
            ui._stream_renderer = MagicMock()
            active_backend = ui._get_active_backend("demo-model@v1")

        assert active_backend is backend
        mock_confirm.assert_called_once()
        mock_install.assert_called_once_with("gguf")
        mock_create_backend.assert_called_once_with("gguf")
        backend.load.assert_called_once_with(Path(model_path))

    def test_declining_backend_install_keeps_model_unloaded(self, config, storage, tmp_path: Path):
        ui = ShellUI(config=config, storage=storage)
        ui._console = MagicMock()
        model_dir = tmp_path / "model"
        model_dir.mkdir()
        _register_model(ui, model_dir)

        with (
            patch(
                "localagentcli.runtime.core.check_backend_dependencies",
                return_value=(False, ["llama_cpp"]),
            ),
            patch("localagentcli.shell.ui.confirm_choice", return_value=False),
            patch.object(ui._runtime, "_create_backend") as mock_create_backend,
        ):
            ui._stream_renderer = MagicMock()
            active_backend = ui._get_active_backend("demo-model@v1")

        assert active_backend is None
        mock_create_backend.assert_not_called()

    def test_failed_backend_install_reports_error(self, config, storage, tmp_path: Path):
        ui = ShellUI(config=config, storage=storage)
        ui._console = MagicMock()
        model_dir = tmp_path / "model"
        model_dir.mkdir()
        _register_model(ui, model_dir)

        with (
            patch(
                "localagentcli.runtime.core.check_backend_dependencies",
                return_value=(False, ["llama_cpp"]),
            ),
            patch(
                "localagentcli.runtime.core.install_backend_dependencies",
                return_value=(False, "pip failed"),
            ),
            patch("localagentcli.shell.ui.confirm_choice", return_value=True),
            patch.object(ui._runtime, "_create_backend") as mock_create_backend,
        ):
            ui._stream_renderer = MagicMock()
            active_backend = ui._get_active_backend("demo-model@v1")

        assert active_backend is None
        mock_create_backend.assert_not_called()
        ui._stream_renderer.render_error.assert_called_once_with(
            "Failed to install GGUF backend dependencies: pip failed"
        )
