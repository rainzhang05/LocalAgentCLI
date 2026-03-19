"""Tests for ModelDetector and HardwareDetector."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from localagentcli.models.detector import (
    DetectionError,
    DetectionResult,
    HardwareDetector,
    HardwareInfo,
    ModelDetector,
    _fmt_bytes,
)


@pytest.fixture
def detector() -> ModelDetector:
    return ModelDetector()


@pytest.fixture
def hw_detector() -> HardwareDetector:
    return HardwareDetector()


# ---------------------------------------------------------------------------
# DetectionResult / HardwareInfo dataclass tests
# ---------------------------------------------------------------------------


class TestDetectionResult:
    def test_defaults(self):
        r = DetectionResult(format="gguf", backend="gguf")
        assert r.format == "gguf"
        assert r.backend == "gguf"
        assert r.metadata == {}

    def test_all_fields(self):
        r = DetectionResult(format="mlx", backend="mlx", metadata={"model_type": "llama"})
        assert r.format == "mlx"
        assert r.metadata["model_type"] == "llama"


class TestHardwareInfo:
    def test_defaults(self):
        h = HardwareInfo()
        assert h.cpu_cores == 1
        assert h.ram_bytes == 0
        assert h.gpu_type == "none"
        assert h.apple_silicon is False


# ---------------------------------------------------------------------------
# ModelDetector tests
# ---------------------------------------------------------------------------


class TestModelDetectorGGUF:
    def test_detect_gguf(self, detector: ModelDetector, tmp_path: Path):
        model_dir = tmp_path / "model"
        model_dir.mkdir()
        (model_dir / "model-Q4_K_M.gguf").write_bytes(b"\x00" * 100)
        result = detector.detect(model_dir)
        assert result.format == "gguf"
        assert result.backend == "gguf"
        assert result.metadata.get("quantization") == "Q4_K_M"

    def test_detect_gguf_no_quant_in_name(self, detector: ModelDetector, tmp_path: Path):
        model_dir = tmp_path / "model"
        model_dir.mkdir()
        (model_dir / "model.gguf").write_bytes(b"\x00" * 100)
        result = detector.detect(model_dir)
        assert result.format == "gguf"
        assert "quantization" not in result.metadata or result.metadata.get("quantization") == ""


class TestModelDetectorMLX:
    @patch("localagentcli.models.detector.platform")
    def test_detect_mlx_with_weights_dir(
        self, mock_platform, detector: ModelDetector, tmp_path: Path
    ):
        mock_platform.system.return_value = "Darwin"
        mock_platform.machine.return_value = "arm64"
        model_dir = tmp_path / "model"
        model_dir.mkdir()
        (model_dir / "model.safetensors").write_bytes(b"\x00" * 100)
        (model_dir / "weights").mkdir()
        config = {"model_type": "llama", "hidden_size": 4096}
        (model_dir / "config.json").write_text(json.dumps(config))
        result = detector.detect(model_dir)
        assert result.format == "mlx"
        assert result.backend == "mlx"

    @patch("localagentcli.models.detector.platform")
    def test_detect_mlx_with_quant_config(
        self, mock_platform, detector: ModelDetector, tmp_path: Path
    ):
        mock_platform.system.return_value = "Darwin"
        mock_platform.machine.return_value = "arm64"
        model_dir = tmp_path / "model"
        model_dir.mkdir()
        (model_dir / "model.safetensors").write_bytes(b"\x00" * 100)
        config = {
            "model_type": "llama",
            "quantization_config": {"group_size": 64, "bits": 4},
        }
        (model_dir / "config.json").write_text(json.dumps(config))
        result = detector.detect(model_dir)
        assert result.format == "mlx"

    @patch("localagentcli.models.detector.platform")
    def test_detect_mlx_with_top_level_quantization(
        self, mock_platform, detector: ModelDetector, tmp_path: Path
    ):
        mock_platform.system.return_value = "Darwin"
        mock_platform.machine.return_value = "arm64"
        model_dir = tmp_path / "model"
        model_dir.mkdir()
        (model_dir / "model.safetensors").write_bytes(b"\x00" * 100)
        config = {
            "model_type": "gemma3",
            "quantization": {"group_size": 64, "bits": 4},
        }
        (model_dir / "config.json").write_text(json.dumps(config))

        result = detector.detect(model_dir)

        assert result.format == "mlx"

    @patch("localagentcli.models.detector.platform")
    def test_mlx_on_non_macos_raises(self, mock_platform, tmp_path: Path):
        mock_platform.system.return_value = "Linux"
        mock_platform.machine.return_value = "x86_64"
        detector = ModelDetector()
        model_dir = tmp_path / "model"
        model_dir.mkdir()
        (model_dir / "model.safetensors").write_bytes(b"\x00" * 100)
        (model_dir / "weights").mkdir()
        (model_dir / "config.json").write_text(json.dumps({"model_type": "llama"}))
        with pytest.raises(DetectionError, match="only supported on macOS"):
            detector.detect(model_dir)


class TestModelDetectorSafetensors:
    def test_detect_safetensors(self, detector: ModelDetector, tmp_path: Path):
        model_dir = tmp_path / "model"
        model_dir.mkdir()
        (model_dir / "model.safetensors").write_bytes(b"\x00" * 100)
        config = {"model_type": "mistral", "hidden_size": 4096}
        (model_dir / "config.json").write_text(json.dumps(config))
        result = detector.detect(model_dir)
        assert result.format == "safetensors"
        assert result.backend == "safetensors"

    def test_detect_safetensors_no_config(self, detector: ModelDetector, tmp_path: Path):
        model_dir = tmp_path / "model"
        model_dir.mkdir()
        (model_dir / "model.safetensors").write_bytes(b"\x00" * 100)
        result = detector.detect(model_dir)
        assert result.format == "safetensors"


class TestModelDetectorErrors:
    def test_not_a_directory(self, detector: ModelDetector, tmp_path: Path):
        file_path = tmp_path / "file.txt"
        file_path.write_text("not a dir")
        with pytest.raises(DetectionError, match="not a directory"):
            detector.detect(file_path)

    def test_empty_directory(self, detector: ModelDetector, tmp_path: Path):
        model_dir = tmp_path / "empty"
        model_dir.mkdir()
        with pytest.raises(DetectionError, match="Cannot determine"):
            detector.detect(model_dir)

    def test_no_model_files(self, detector: ModelDetector, tmp_path: Path):
        model_dir = tmp_path / "model"
        model_dir.mkdir()
        (model_dir / "readme.txt").write_text("hello")
        with pytest.raises(DetectionError, match="Cannot determine"):
            detector.detect(model_dir)


class TestModelDetectorMetadata:
    def test_extracts_model_type(self, detector: ModelDetector, tmp_path: Path):
        model_dir = tmp_path / "model"
        model_dir.mkdir()
        (model_dir / "model.gguf").write_bytes(b"\x00")
        config = {"model_type": "llama", "hidden_size": 4096, "num_hidden_layers": 32}
        (model_dir / "config.json").write_text(json.dumps(config))
        result = detector.detect(model_dir)
        assert result.metadata["model_type"] == "llama"
        assert result.metadata["hidden_size"] == 4096

    def test_estimates_parameters(self, detector: ModelDetector, tmp_path: Path):
        model_dir = tmp_path / "model"
        model_dir.mkdir()
        (model_dir / "model.gguf").write_bytes(b"\x00")
        config = {
            "model_type": "llama",
            "hidden_size": 4096,
            "num_hidden_layers": 32,
            "vocab_size": 32000,
            "intermediate_size": 11008,
        }
        (model_dir / "config.json").write_text(json.dumps(config))
        result = detector.detect(model_dir)
        assert "parameter_count" in result.metadata
        assert result.metadata["parameter_count"].endswith("B")

    def test_corrupt_config_json(self, detector: ModelDetector, tmp_path: Path):
        model_dir = tmp_path / "model"
        model_dir.mkdir()
        (model_dir / "model.gguf").write_bytes(b"\x00")
        (model_dir / "config.json").write_text("{invalid json")
        result = detector.detect(model_dir)
        assert result.format == "gguf"
        # Metadata extraction should not crash
        assert result.metadata.get("backend") == "gguf"


# ---------------------------------------------------------------------------
# HardwareDetector tests
# ---------------------------------------------------------------------------


class TestHardwareDetector:
    def test_detect_returns_hardware_info(self, hw_detector: HardwareDetector):
        info = hw_detector.detect()
        assert isinstance(info, HardwareInfo)
        assert info.cpu_cores >= 1

    @patch("localagentcli.models.detector.platform")
    @patch("localagentcli.models.detector.os")
    def test_detect_apple_silicon(self, mock_os, mock_platform):
        mock_platform.system.return_value = "Darwin"
        mock_platform.machine.return_value = "arm64"
        mock_os.cpu_count.return_value = 10
        detector = HardwareDetector()
        # Mock _get_ram to avoid system calls
        with patch.object(detector, "_get_ram", return_value=16_000_000_000):
            info = detector.detect()
        assert info.apple_silicon is True
        assert info.gpu_type == "apple_silicon"
        assert info.cpu_cores == 10

    @patch("localagentcli.models.detector.platform")
    @patch("localagentcli.models.detector.os")
    def test_detect_linux(self, mock_os, mock_platform):
        mock_platform.system.return_value = "Linux"
        mock_platform.machine.return_value = "x86_64"
        mock_os.cpu_count.return_value = 8
        detector = HardwareDetector()
        with patch.object(detector, "_get_ram", return_value=32_000_000_000):
            with patch.object(detector, "_detect_gpu", return_value=("none", 0)):
                info = detector.detect()
        assert info.apple_silicon is False
        assert info.cpu_cores == 8

    def test_can_run_model_small(self, hw_detector: HardwareDetector):
        with patch.object(
            hw_detector,
            "detect",
            return_value=HardwareInfo(
                ram_bytes=16_000_000_000,
            ),
        ):
            can_run, warnings = hw_detector.can_run_model(4_000_000_000)
        assert can_run is True
        assert warnings == []

    def test_can_run_model_warning(self, hw_detector: HardwareDetector):
        with patch.object(
            hw_detector,
            "detect",
            return_value=HardwareInfo(
                ram_bytes=8_000_000_000,
            ),
        ):
            can_run, warnings = hw_detector.can_run_model(7_000_000_000)
        assert can_run is True
        assert len(warnings) == 1
        assert "Performance may be degraded" in warnings[0]

    def test_can_run_model_too_large(self, hw_detector: HardwareDetector):
        with patch.object(
            hw_detector,
            "detect",
            return_value=HardwareInfo(
                ram_bytes=8_000_000_000,
            ),
        ):
            can_run, warnings = hw_detector.can_run_model(8_000_000_000)
        assert can_run is False

    def test_can_run_model_no_ram_info(self, hw_detector: HardwareDetector):
        with patch.object(
            hw_detector,
            "detect",
            return_value=HardwareInfo(
                ram_bytes=0,
            ),
        ):
            can_run, warnings = hw_detector.can_run_model(4_000_000_000)
        assert can_run is True
        assert warnings == []


# ---------------------------------------------------------------------------
# Helper tests
# ---------------------------------------------------------------------------


class TestFmtBytes:
    def test_gb(self):
        assert _fmt_bytes(4_294_967_296) == "4.0 GB"

    def test_mb(self):
        assert _fmt_bytes(1_048_576) == "1.0 MB"

    def test_bytes(self):
        assert _fmt_bytes(1024) == "1024 bytes"
