"""Tests for ModelInstaller — HuggingFace and URL downloads."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from rich.console import Console

from localagentcli.models.detector import ModelDetector
from localagentcli.models.installer import ModelInstaller, _fmt_size, _make_fast_tqdm_class
from localagentcli.models.registry import ModelRegistry


@pytest.fixture
def registry_path(tmp_path: Path) -> Path:
    return tmp_path / "registry.json"


@pytest.fixture
def registry(registry_path: Path) -> ModelRegistry:
    return ModelRegistry(registry_path)


@pytest.fixture
def detector() -> ModelDetector:
    return ModelDetector()


@pytest.fixture
def models_dir(tmp_path: Path) -> Path:
    d = tmp_path / "models"
    d.mkdir()
    return d


@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
    d = tmp_path / "cache"
    d.mkdir()
    (d / "downloads").mkdir()
    return d


@pytest.fixture
def console() -> Console:
    return Console(quiet=True)


@pytest.fixture
def installer(
    models_dir: Path,
    cache_dir: Path,
    registry: ModelRegistry,
    detector: ModelDetector,
    console: Console,
) -> ModelInstaller:
    return ModelInstaller(models_dir, cache_dir, registry, detector, console)


# ---------------------------------------------------------------------------
# Name derivation
# ---------------------------------------------------------------------------


class TestNameDerivation:
    def test_derive_from_repo(self, installer: ModelInstaller):
        assert installer._derive_name_from_repo("TheBloke/CodeLlama-7B-GGUF") == "codellama-7b-gguf"

    def test_derive_from_repo_single(self, installer: ModelInstaller):
        assert installer._derive_name_from_repo("mistral-7b") == "mistral-7b"

    def test_derive_from_url(self, installer: ModelInstaller):
        url = "https://example.com/models/phi-2-Q4_K_M.gguf"
        assert installer._derive_name_from_url(url) == "phi-2-q4_k_m"

    def test_derive_from_url_no_path(self, installer: ModelInstaller):
        assert installer._derive_name_from_url("https://example.com/") == "model"


# ---------------------------------------------------------------------------
# HuggingFace install
# ---------------------------------------------------------------------------


class TestInstallFromHF:
    def test_success(self, installer: ModelInstaller, models_dir: Path, registry: ModelRegistry):
        def fake_download(repo_id, local_dir, **kwargs):
            target = Path(local_dir)
            target.mkdir(parents=True, exist_ok=True)
            (target / "model.gguf").write_bytes(b"\x00" * 100)

        with patch.object(installer, "_download_hf", side_effect=fake_download):
            result = installer.install_from_hf("TheBloke/CodeLlama-7B-GGUF")

        assert result.success is True
        assert result.model_entry is not None
        assert result.model_entry.name == "codellama-7b-gguf"
        assert result.model_entry.version == "v1"
        assert result.model_entry.format == "gguf"
        assert result.model_entry.capability_provenance["tool_use"]["tier"] == "verified"
        assert result.model_entry.capability_provenance["reasoning"]["tier"] == "unknown"
        assert result.model_entry.capability_provenance["streaming"]["tier"] == "verified"
        assert len(registry.list_models()) == 1

    def test_reasoning_family_marks_inferred_readiness(self, installer: ModelInstaller):
        def fake_download(repo_id, local_dir, **kwargs):
            target = Path(local_dir)
            target.mkdir(parents=True, exist_ok=True)
            (target / "model.gguf").write_bytes(b"\x00" * 100)

        with patch.object(installer, "_download_hf", side_effect=fake_download):
            result = installer.install_from_hf("deepseek-ai/DeepSeek-R1-Distill-Qwen-7B-GGUF")

        assert result.success is True
        assert result.model_entry is not None
        assert result.model_entry.capabilities["reasoning"] is True
        assert result.model_entry.capability_provenance["reasoning"]["tier"] == "inferred"

    def test_custom_name(self, installer: ModelInstaller, registry: ModelRegistry):
        def fake_download(repo_id, local_dir, **kwargs):
            target = Path(local_dir)
            target.mkdir(parents=True, exist_ok=True)
            (target / "model.gguf").write_bytes(b"\x00" * 100)

        with patch.object(installer, "_download_hf", side_effect=fake_download):
            result = installer.install_from_hf("TheBloke/CodeLlama-7B-GGUF", name="my-model")

        assert result.success is True
        assert result.model_entry is not None
        assert result.model_entry.name == "my-model"

    def test_download_failure(self, installer: ModelInstaller):
        with patch.object(installer, "_download_hf", side_effect=RuntimeError("Network error")):
            result = installer.install_from_hf("bad/repo")

        assert result.success is False
        assert "Download failed" in result.message

    def test_version_increment(self, installer: ModelInstaller, models_dir: Path):
        def fake_download(repo_id, local_dir, **kwargs):
            target = Path(local_dir)
            target.mkdir(parents=True, exist_ok=True)
            (target / "model.gguf").write_bytes(b"\x00" * 100)

        with patch.object(installer, "_download_hf", side_effect=fake_download):
            r1 = installer.install_from_hf("repo/model")
            r2 = installer.install_from_hf("repo/model")

        assert r1.model_entry is not None
        assert r1.model_entry.version == "v1"
        assert r2.model_entry is not None
        assert r2.model_entry.version == "v2"

    def test_detection_failure(self, installer: ModelInstaller):
        def fake_download(repo_id, local_dir, **kwargs):
            # Create directory but no model files
            Path(local_dir).mkdir(parents=True, exist_ok=True)

        with patch.object(installer, "_download_hf", side_effect=fake_download):
            result = installer.install_from_hf("repo/empty-model")

        assert result.success is False
        assert "detection failed" in result.message.lower()

    def test_download_hf_uses_live_file_progress_when_dry_run_supported(
        self,
        installer: ModelInstaller,
        models_dir: Path,
    ):
        snapshot_calls: list[dict] = []
        file_calls: list[dict] = []
        target_dir = models_dir / "repo" / "v1"

        def fake_snapshot_download(**kwargs):
            snapshot_calls.append(kwargs)
            assert kwargs.get("dry_run") is True
            return [
                SimpleNamespace(
                    filename="weights/model-00001.safetensors",
                    size=12,
                    is_cached=False,
                ),
                SimpleNamespace(filename="README.md", size=4, is_cached=True),
            ]

        def fake_hf_hub_download(**kwargs):
            file_calls.append(kwargs)
            target = Path(kwargs["local_dir"]) / kwargs["filename"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"x" * 4)
            return str(target)

        with patch(
            "localagentcli.models.installer._load_huggingface_downloaders",
            return_value=(fake_hf_hub_download, fake_snapshot_download),
        ):
            installer._download_hf("repo/model", target_dir)

        assert len(snapshot_calls) == 1
        assert [call["filename"] for call in file_calls] == [
            "weights/model-00001.safetensors",
            "README.md",
        ]
        assert (target_dir / "weights" / "model-00001.safetensors").exists()


# ---------------------------------------------------------------------------
# URL install
# ---------------------------------------------------------------------------


class TestInstallFromURL:
    def test_success(self, installer: ModelInstaller, registry: ModelRegistry):
        def fake_download(url, target_path):
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_bytes(b"\x00" * 100)

        with patch.object(installer, "_download_url", side_effect=fake_download):
            result = installer.install_from_url("https://example.com/model.gguf")

        assert result.success is True
        assert result.model_entry is not None
        assert result.model_entry.format == "gguf"
        assert len(registry.list_models()) == 1

    def test_download_failure(self, installer: ModelInstaller):
        with patch.object(
            installer, "_download_url", side_effect=RuntimeError("Connection refused")
        ):
            result = installer.install_from_url("https://bad-url.com/model.gguf")

        assert result.success is False
        assert "Download failed" in result.message

    def test_custom_name(self, installer: ModelInstaller):
        def fake_download(url, target_path):
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_bytes(b"\x00" * 100)

        with patch.object(installer, "_download_url", side_effect=fake_download):
            result = installer.install_from_url("https://example.com/model.gguf", name="custom")

        assert result.success is True
        assert result.model_entry is not None
        assert result.model_entry.name == "custom"


# ---------------------------------------------------------------------------
# Size calculation
# ---------------------------------------------------------------------------


class TestCalculateSize:
    def test_empty_directory(self, installer: ModelInstaller, tmp_path: Path):
        d = tmp_path / "empty"
        d.mkdir()
        assert installer._calculate_size(d) == 0

    def test_with_files(self, installer: ModelInstaller, tmp_path: Path):
        d = tmp_path / "model"
        d.mkdir()
        (d / "a.bin").write_bytes(b"\x00" * 100)
        (d / "b.bin").write_bytes(b"\x00" * 200)
        assert installer._calculate_size(d) == 300


# ---------------------------------------------------------------------------
# Format size helper
# ---------------------------------------------------------------------------


class TestFmtSize:
    def test_gb(self):
        assert _fmt_size(4_294_967_296) == "4.0 GB"


class TestTQDMCompatibility:
    def test_fast_tqdm_ignores_hub_name_kwarg(self):
        tqdm_class = _make_fast_tqdm_class()
        progress = tqdm_class(total=1, name="huggingface_hub.snapshot_download")
        progress.update(1)
        progress.close()

    def test_mb(self):
        assert _fmt_size(1_048_576) == "1.0 MB"

    def test_kb(self):
        assert _fmt_size(1024) == "1.0 KB"

    def test_bytes(self):
        assert _fmt_size(100) == "100 bytes"
