"""Tests for backend dependency helpers."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from localagentcli.models.backends.base import (
    backend_extra_name,
    backend_install_hint,
    backend_label,
    backend_requirement_names,
    backend_requirement_specs,
    check_backend_dependencies,
    install_backend_dependencies,
)


class TestBackendMetadata:
    def test_backend_label(self):
        assert backend_label("gguf") == "GGUF"

    def test_backend_extra_name(self):
        assert backend_extra_name("safetensors") == "torch"

    def test_backend_install_hint(self):
        assert backend_install_hint("mlx") == "pip install localagentcli[mlx]"

    def test_backend_requirement_specs(self):
        assert backend_requirement_specs("gguf") == ["llama-cpp-python>=0.2"]

    def test_backend_requirement_names(self):
        assert backend_requirement_names("safetensors") == [
            "torch",
            "transformers",
            "safetensors",
        ]

    def test_unknown_backend_raises(self):
        with pytest.raises(ValueError, match="Unknown backend"):
            backend_requirement_specs("unknown")


class TestCheckBackendDependencies:
    def test_returns_missing_modules(self):
        with patch(
            "localagentcli.models.backends.base.importlib.import_module",
            side_effect=[ImportError, object()],
        ):
            installed, missing = check_backend_dependencies("gguf")

        assert installed is False
        assert missing == ["llama_cpp"]

    def test_returns_success_when_all_present(self):
        with patch("localagentcli.models.backends.base.importlib.import_module") as mock_import:
            installed, missing = check_backend_dependencies("mlx")

        assert installed is True
        assert missing == []
        assert mock_import.call_count == 2


class TestInstallBackendDependencies:
    def test_installs_direct_backend_requirements(self):
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="installed",
            stderr="",
        )

        with (
            patch(
                "localagentcli.models.backends.base.check_backend_dependencies",
                return_value=(True, []),
            ),
            patch(
                "localagentcli.models.backends.base.sys.executable",
                "/tmp/python",
            ),
        ):
            calls = []

            def runner(*args, **kwargs):
                calls.append((args, kwargs))
                return completed

            success, message = install_backend_dependencies("gguf", runner=runner)

        assert success is True
        assert message == "installed"
        command = calls[0][0][0]
        assert command == [
            "/tmp/python",
            "-m",
            "pip",
            "install",
            "llama-cpp-python>=0.2",
        ]

    def test_returns_failure_when_pip_fails(self):
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr="boom",
        )

        success, message = install_backend_dependencies("mlx", runner=lambda *a, **k: completed)

        assert success is False
        assert message == "boom"

    def test_returns_failure_when_modules_still_missing(self):
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="installed",
            stderr="",
        )

        with patch(
            "localagentcli.models.backends.base.check_backend_dependencies",
            return_value=(False, ["mlx_lm"]),
        ):
            success, message = install_backend_dependencies("mlx", runner=lambda *a, **k: completed)

        assert success is False
        assert "mlx_lm" in message
