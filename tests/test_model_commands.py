"""Tests for /models command handlers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console

from localagentcli.commands.models import (
    ModelsInspectHandler,
    ModelsInstallHandler,
    ModelsListHandler,
    ModelsParentHandler,
    ModelsRemoveHandler,
    ModelsSearchHandler,
    ModelsUseHandler,
    _parse_name_version,
)
from localagentcli.models.detector import HardwareDetector
from localagentcli.models.hf_catalog import HubModelChoice
from localagentcli.models.installer import InstallResult, ModelInstaller
from localagentcli.models.registry import ModelEntry, ModelRegistry
from localagentcli.session.manager import SessionManager


@pytest.fixture
def registry_path(tmp_path: Path) -> Path:
    return tmp_path / "registry.json"


@pytest.fixture
def registry(registry_path: Path) -> ModelRegistry:
    return ModelRegistry(registry_path)


@pytest.fixture
def models_dir(tmp_path: Path) -> Path:
    d = tmp_path / "models"
    d.mkdir()
    return d


@pytest.fixture
def console() -> Console:
    return Console(quiet=True)


@pytest.fixture
def hw_detector() -> HardwareDetector:
    return HardwareDetector()


def _make_entry(
    name: str = "codellama-7b",
    version: str = "v1",
    fmt: str = "gguf",
    size_bytes: int = 4_000_000_000,
    path: str = "/models/codellama-7b/v1",
) -> ModelEntry:
    return ModelEntry(
        name=name,
        version=version,
        format=fmt,
        path=path,
        size_bytes=size_bytes,
        metadata={"source": "huggingface", "backend": fmt},
    )


# ---------------------------------------------------------------------------
# Parse name@version
# ---------------------------------------------------------------------------


class TestParseNameVersion:
    def test_name_only(self):
        assert _parse_name_version("codellama-7b") == ("codellama-7b", None)

    def test_name_and_version(self):
        assert _parse_name_version("codellama-7b@v1") == ("codellama-7b", "v1")

    def test_multiple_at(self):
        assert _parse_name_version("a@b@v2") == ("a@b", "v2")


# ---------------------------------------------------------------------------
# Parent handler
# ---------------------------------------------------------------------------


class TestModelsParent:
    @patch("localagentcli.commands.models.supports_interactive_prompt", return_value=False)
    def test_noninteractive_falls_back_to_help(
        self,
        _mock_supports,
        hw_detector: HardwareDetector,
        session_manager: SessionManager,
        console: Console,
    ):
        handler = ModelsParentHandler(MagicMock(), hw_detector, session_manager, console)
        result = handler.execute([])
        assert result.success is True
        assert "Interactive model picker requires a terminal TTY" in result.message

    @patch("localagentcli.commands.models.supports_interactive_prompt", return_value=True)
    def test_picker_installs_and_activates_selected_model(
        self,
        _mock_supports,
        hw_detector: HardwareDetector,
        session_manager: SessionManager,
        console: Console,
    ):
        installer = MagicMock(spec=ModelInstaller)
        installer.install_from_hf.return_value = InstallResult(
            success=True,
            message="Installed successfully",
            model_entry=_make_entry(name="qwen3-8b-gguf", path="/models/qwen3-8b-gguf/v1"),
        )
        selector = MagicMock(
            side_effect=[
                MagicMock(value="gguf"),
                MagicMock(value="qwen"),
                MagicMock(value="qwen-qwen3-8b-gguf"),
            ]
        )
        catalog = MagicMock()
        catalog.list_families.return_value = [
            MagicMock(key="qwen", label="Qwen", description="Qwen family", aliases=("qwen",))
        ]
        catalog.list_models.return_value = [
            HubModelChoice(
                backend="gguf",
                family="qwen",
                repo="Qwen/Qwen3-8B-GGUF",
                label="Qwen3 8B GGUF [Qwen]",
                install_name="qwen-qwen3-8b-gguf",
                summary="1,000 downloads • Qwen/Qwen3-8B-GGUF",
                aliases=("Qwen/Qwen3-8B-GGUF", "qwen"),
            )
        ]
        with patch.object(hw_detector, "can_run_model", return_value=(True, [])):
            handler = ModelsParentHandler(
                installer,
                hw_detector,
                session_manager,
                console,
                selector=selector,
                catalog=catalog,
            )
            result = handler.execute([])

        assert result.success is True
        assert "Installed 'Qwen3 8B GGUF [Qwen]'" in result.message
        installer.install_from_hf.assert_called_once_with(
            "Qwen/Qwen3-8B-GGUF",
            name="qwen-qwen3-8b-gguf",
        )
        assert session_manager.current.model == "qwen3-8b-gguf@v1"
        assert session_manager.current.provider == ""

    @patch("localagentcli.commands.models.supports_interactive_prompt", return_value=True)
    def test_picker_cancelled(
        self,
        _mock_supports,
        hw_detector: HardwareDetector,
        session_manager: SessionManager,
        console: Console,
    ):
        handler = ModelsParentHandler(
            MagicMock(),
            hw_detector,
            session_manager,
            console,
            selector=MagicMock(return_value=None),
        )
        result = handler.execute([])
        assert result.success is True
        assert "cancelled" in result.message.lower()

    def test_help_text(self):
        handler = ModelsParentHandler(MagicMock(), MagicMock(), MagicMock(), MagicMock())
        assert "list" in handler.help_text()
        assert "install" in handler.help_text()


# ---------------------------------------------------------------------------
# List handler
# ---------------------------------------------------------------------------


class TestModelsList:
    def test_empty_list(self, registry: ModelRegistry):
        handler = ModelsListHandler(registry)
        result = handler.execute([])
        assert result.success is True
        assert "No models" in result.message

    def test_populated_list(self, registry: ModelRegistry):
        registry.register(_make_entry())
        registry.register(_make_entry("mistral-7b", path="/models/mistral/v1"))
        handler = ModelsListHandler(registry)
        result = handler.execute([])
        assert result.success is True
        assert "codellama-7b" in result.message
        assert "mistral-7b" in result.message


# ---------------------------------------------------------------------------
# Search handler
# ---------------------------------------------------------------------------


class TestModelsSearch:
    def test_no_query_error(self, registry: ModelRegistry):
        handler = ModelsSearchHandler(registry)
        result = handler.execute([])
        assert result.success is False

    def test_found_results(self, registry: ModelRegistry):
        registry.register(_make_entry())
        handler = ModelsSearchHandler(registry)
        result = handler.execute(["codellama"])
        assert result.success is True
        assert "codellama-7b" in result.message

    def test_no_results(self, registry: ModelRegistry):
        registry.register(_make_entry())
        handler = ModelsSearchHandler(registry)
        result = handler.execute(["nonexistent"])
        assert result.success is True
        assert "No models matching" in result.message


# ---------------------------------------------------------------------------
# Install handler
# ---------------------------------------------------------------------------


class TestModelsInstall:
    def test_missing_args_error(self):
        handler = ModelsInstallHandler(MagicMock())
        result = handler.execute([])
        assert result.success is False
        assert "Source type" in result.message

    def test_missing_location_error(self):
        handler = ModelsInstallHandler(MagicMock())
        result = handler.execute(["hf"])
        assert result.success is False

    def test_hf_install_success(self):
        mock_installer = MagicMock(spec=ModelInstaller)
        mock_installer.install_from_hf.return_value = InstallResult(
            success=True, message="Installed successfully"
        )
        handler = ModelsInstallHandler(mock_installer)
        result = handler.execute(["hf", "TheBloke/CodeLlama-7B-GGUF"])
        assert result.success is True
        mock_installer.install_from_hf.assert_called_once_with(
            "TheBloke/CodeLlama-7B-GGUF", name=None
        )

    def test_url_install_success(self):
        mock_installer = MagicMock(spec=ModelInstaller)
        mock_installer.install_from_url.return_value = InstallResult(
            success=True, message="Installed successfully"
        )
        handler = ModelsInstallHandler(mock_installer)
        result = handler.execute(["url", "https://example.com/model.gguf"])
        assert result.success is True

    def test_install_failure(self):
        mock_installer = MagicMock(spec=ModelInstaller)
        mock_installer.install_from_hf.return_value = InstallResult(
            success=False, message="Download failed"
        )
        handler = ModelsInstallHandler(mock_installer)
        result = handler.execute(["hf", "bad/repo"])
        assert result.success is False

    def test_unknown_source_type(self):
        handler = ModelsInstallHandler(MagicMock())
        result = handler.execute(["ftp", "host/model"])
        assert result.success is False
        assert "Unknown source type" in result.message

    def test_custom_name(self):
        mock_installer = MagicMock(spec=ModelInstaller)
        mock_installer.install_from_hf.return_value = InstallResult(success=True, message="ok")
        handler = ModelsInstallHandler(mock_installer)
        handler.execute(["hf", "repo/model", "my-name"])
        mock_installer.install_from_hf.assert_called_once_with("repo/model", name="my-name")


# ---------------------------------------------------------------------------
# Remove handler
# ---------------------------------------------------------------------------


class TestModelsRemove:
    def test_no_name_error(self, registry: ModelRegistry, models_dir: Path):
        handler = ModelsRemoveHandler(registry, models_dir)
        result = handler.execute([])
        assert result.success is False

    def test_remove_success(self, registry: ModelRegistry, models_dir: Path):
        entry = _make_entry(path=str(models_dir / "codellama-7b" / "v1"))
        (models_dir / "codellama-7b" / "v1").mkdir(parents=True)
        registry.register(entry)

        handler = ModelsRemoveHandler(registry, models_dir)
        result = handler.execute(["codellama-7b"])
        assert result.success is True
        assert "removed" in result.message.lower()
        assert len(registry.list_models()) == 0

    def test_remove_specific_version(self, registry: ModelRegistry, models_dir: Path):
        (models_dir / "model" / "v1").mkdir(parents=True)
        (models_dir / "model" / "v2").mkdir(parents=True)
        registry.register(_make_entry("model", "v1", path=str(models_dir / "model" / "v1")))
        registry.register(_make_entry("model", "v2", path=str(models_dir / "model" / "v2")))

        handler = ModelsRemoveHandler(registry, models_dir)
        result = handler.execute(["model@v1"])
        assert result.success is True
        assert len(registry.list_models()) == 1

    def test_remove_not_found(self, registry: ModelRegistry, models_dir: Path):
        handler = ModelsRemoveHandler(registry, models_dir)
        result = handler.execute(["nonexistent"])
        assert result.success is False
        assert "not found" in result.message.lower()

    @patch("localagentcli.commands.models.supports_interactive_prompt", return_value=True)
    @patch("localagentcli.commands.models.select_option")
    def test_remove_uses_picker_when_name_missing(
        self,
        mock_select,
        _mock_supports,
        registry: ModelRegistry,
        models_dir: Path,
    ):
        entry = _make_entry(path=str(models_dir / "codellama-7b" / "v1"))
        (models_dir / "codellama-7b" / "v1").mkdir(parents=True)
        registry.register(entry)
        mock_select.return_value = MagicMock(value="codellama-7b@v1")

        handler = ModelsRemoveHandler(registry, models_dir)
        result = handler.execute([])

        assert result.success is True
        assert len(registry.list_models()) == 0


# ---------------------------------------------------------------------------
# Use handler
# ---------------------------------------------------------------------------


class TestModelsUse:
    def test_no_name_error(
        self,
        registry: ModelRegistry,
        hw_detector: HardwareDetector,
        session_manager: SessionManager,
        console: Console,
    ):
        handler = ModelsUseHandler(registry, hw_detector, session_manager, console)
        result = handler.execute([])
        assert result.success is False

    def test_use_success(
        self,
        registry: ModelRegistry,
        hw_detector: HardwareDetector,
        session_manager: SessionManager,
        console: Console,
    ):
        registry.register(_make_entry(size_bytes=1_000_000))
        with patch.object(hw_detector, "can_run_model", return_value=(True, [])):
            handler = ModelsUseHandler(registry, hw_detector, session_manager, console)
            result = handler.execute(["codellama-7b"])
        assert result.success is True
        assert session_manager.current.model == "codellama-7b@v1"
        assert session_manager.current.provider == ""

    def test_use_specific_version(
        self,
        registry: ModelRegistry,
        hw_detector: HardwareDetector,
        session_manager: SessionManager,
        console: Console,
    ):
        registry.register(_make_entry(version="v1"))
        registry.register(_make_entry(version="v2", path="/models/codellama-7b/v2"))
        with patch.object(hw_detector, "can_run_model", return_value=(True, [])):
            handler = ModelsUseHandler(registry, hw_detector, session_manager, console)
            result = handler.execute(["codellama-7b@v1"])
        assert result.success is True
        assert "v1" in session_manager.current.model

    def test_use_not_found(
        self,
        registry: ModelRegistry,
        hw_detector: HardwareDetector,
        session_manager: SessionManager,
        console: Console,
    ):
        handler = ModelsUseHandler(registry, hw_detector, session_manager, console)
        result = handler.execute(["nonexistent"])
        assert result.success is False

    def test_use_too_large(
        self,
        registry: ModelRegistry,
        hw_detector: HardwareDetector,
        session_manager: SessionManager,
        console: Console,
    ):
        registry.register(_make_entry(size_bytes=100_000_000_000))
        with patch.object(
            hw_detector,
            "can_run_model",
            return_value=(False, ["Model is too large"]),
        ):
            handler = ModelsUseHandler(registry, hw_detector, session_manager, console)
            result = handler.execute(["codellama-7b"])
        assert result.success is False

    def test_use_with_warnings(
        self,
        registry: ModelRegistry,
        hw_detector: HardwareDetector,
        session_manager: SessionManager,
        console: Console,
    ):
        registry.register(_make_entry())
        with patch.object(
            hw_detector,
            "can_run_model",
            return_value=(True, ["High memory usage"]),
        ):
            handler = ModelsUseHandler(registry, hw_detector, session_manager, console)
            result = handler.execute(["codellama-7b"])
        assert result.success is True

    @patch("localagentcli.commands.models.supports_interactive_prompt", return_value=True)
    @patch("localagentcli.commands.models.select_option")
    def test_use_uses_picker_when_name_missing(
        self,
        mock_select,
        _mock_supports,
        registry: ModelRegistry,
        hw_detector: HardwareDetector,
        session_manager: SessionManager,
        console: Console,
    ):
        registry.register(_make_entry(size_bytes=1_000_000))
        mock_select.return_value = MagicMock(value="codellama-7b@v1")

        with patch.object(hw_detector, "can_run_model", return_value=(True, [])):
            handler = ModelsUseHandler(registry, hw_detector, session_manager, console)
            result = handler.execute([])

        assert result.success is True
        assert session_manager.current.model == "codellama-7b@v1"


# ---------------------------------------------------------------------------
# Inspect handler
# ---------------------------------------------------------------------------


class TestModelsInspect:
    def test_no_name_error(self, registry: ModelRegistry):
        handler = ModelsInspectHandler(registry)
        result = handler.execute([])
        assert result.success is False

    def test_inspect_success(self, registry: ModelRegistry):
        registry.register(_make_entry())
        handler = ModelsInspectHandler(registry)
        result = handler.execute(["codellama-7b"])
        assert result.success is True
        assert "codellama-7b" in result.message
        assert "gguf" in result.message
        assert "Version" in result.message

    def test_inspect_not_found(self, registry: ModelRegistry):
        handler = ModelsInspectHandler(registry)
        result = handler.execute(["nonexistent"])
        assert result.success is False

    def test_inspect_specific_version(self, registry: ModelRegistry):
        registry.register(_make_entry(version="v1"))
        registry.register(_make_entry(version="v2", path="/models/codellama-7b/v2"))
        handler = ModelsInspectHandler(registry)
        result = handler.execute(["codellama-7b@v1"])
        assert result.success is True
        assert "v1" in result.message

    @patch("localagentcli.commands.models.supports_interactive_prompt", return_value=True)
    @patch("localagentcli.commands.models.select_option")
    def test_inspect_uses_picker_when_name_missing(
        self,
        mock_select,
        _mock_supports,
        registry: ModelRegistry,
    ):
        registry.register(_make_entry())
        mock_select.return_value = MagicMock(value="codellama-7b@v1")

        handler = ModelsInspectHandler(registry)
        result = handler.execute([])

        assert result.success is True
        assert "codellama-7b" in result.message
