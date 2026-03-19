"""ModelDetector and HardwareDetector — format detection and hardware checks."""

from __future__ import annotations

import json
import os
import platform
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class DetectionResult:
    """Result of the model format detection pipeline."""

    format: str  # "gguf" | "mlx" | "safetensors"
    backend: str  # "gguf" | "mlx" | "safetensors"
    metadata: dict = field(default_factory=dict)


@dataclass
class HardwareInfo:
    """Detected hardware capabilities."""

    cpu_cores: int = 1
    ram_bytes: int = 0
    gpu_type: str = "none"  # "apple_silicon" | "cuda" | "none"
    gpu_vram: int = 0
    apple_silicon: bool = False


class DetectionError(Exception):
    """Raised when model format cannot be determined."""


class ModelDetector:
    """Detects model format, assigns backend, validates, and extracts metadata."""

    def detect(self, model_path: Path) -> DetectionResult:
        """Run the full detection pipeline on a model directory."""
        fmt = self._detect_format(model_path)
        backend = self._assign_backend(fmt)
        self._validate(model_path, fmt)
        metadata = self._extract_metadata(model_path, fmt)
        return DetectionResult(format=fmt, backend=backend, metadata=metadata)

    def _detect_format(self, model_path: Path) -> str:
        """Determine the model format from files in the directory."""
        if not model_path.is_dir():
            raise DetectionError(f"Model path is not a directory: {model_path}")

        has_gguf = any(f.suffix == ".gguf" for f in model_path.iterdir() if f.is_file())
        has_safetensors = any(
            f.suffix == ".safetensors" for f in model_path.iterdir() if f.is_file()
        )

        if has_gguf:
            return "gguf"

        if has_safetensors:
            # Check for MLX markers in config.json
            config_path = model_path / "config.json"
            if config_path.exists() and self._is_mlx_format(config_path, model_path):
                return "mlx"
            return "safetensors"

        raise DetectionError(
            f"Cannot determine model format in {model_path}. No .gguf or .safetensors files found."
        )

    def _is_mlx_format(self, config_path: Path, model_path: Path) -> bool:
        """Check if a safetensors model is in MLX format."""
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return False

        # Check for explicit MLX quantization config
        quant_config = config.get("quantization_config", {})
        if isinstance(quant_config, dict):
            if "group_size" in quant_config and "bits" in quant_config:
                # MLX-LM quantization typically uses group_size + bits
                quant_type = quant_config.get("quant_type", "")
                if quant_type == "" or "mlx" in str(quant_type).lower():
                    return True

        top_level_quant = config.get("quantization", {})
        if isinstance(top_level_quant, dict):
            if "group_size" in top_level_quant and "bits" in top_level_quant:
                return True

        # Check for MLX model type marker
        if config.get("model_type_mlx"):
            return True

        # Check for weights/ subdirectory (common in MLX repos)
        if (model_path / "weights").is_dir():
            return True

        readme_path = model_path / "README.md"
        if readme_path.exists():
            try:
                readme_text = readme_path.read_text(encoding="utf-8").lower()
            except OSError:
                return False
            if "use with mlx" in readme_text or "\n- mlx\n" in readme_text:
                return True

        return False

    def _assign_backend(self, fmt: str) -> str:
        """Assign the appropriate backend for the detected format."""
        if fmt == "mlx":
            if platform.system() != "Darwin":
                raise DetectionError(
                    "MLX models are only supported on macOS. "
                    "Consider converting to GGUF format for cross-platform use."
                )
            return "mlx"
        if fmt == "gguf":
            return "gguf"
        if fmt == "safetensors":
            return "safetensors"
        raise DetectionError(f"Unknown format: {fmt}")

    def _validate(self, model_path: Path, fmt: str) -> None:
        """Validate that all required files are present."""
        if fmt == "gguf":
            gguf_files = list(model_path.glob("*.gguf"))
            if not gguf_files:
                raise DetectionError("No .gguf file found in model directory")
        elif fmt in ("mlx", "safetensors"):
            st_files = list(model_path.glob("*.safetensors"))
            if not st_files:
                raise DetectionError("No .safetensors files found in model directory")

    def _extract_metadata(self, model_path: Path, fmt: str) -> dict:
        """Extract metadata from model files."""
        metadata: dict = {"backend": fmt}

        config_path = model_path / "config.json"
        if config_path.exists():
            try:
                config = json.loads(config_path.read_text(encoding="utf-8"))
                if "model_type" in config:
                    metadata["model_type"] = config["model_type"]
                if "hidden_size" in config:
                    metadata["hidden_size"] = config["hidden_size"]
                if "num_hidden_layers" in config:
                    metadata["num_hidden_layers"] = config["num_hidden_layers"]
                # Estimate parameter count from architecture
                param_count = self._estimate_parameters(config)
                if param_count:
                    metadata["parameter_count"] = param_count
                # Quantization info
                quant = config.get("quantization_config", {})
                if quant:
                    metadata["quantization"] = quant
            except (json.JSONDecodeError, OSError):
                pass

        if fmt == "gguf":
            gguf_files = list(model_path.glob("*.gguf"))
            if gguf_files:
                # Extract quantization from filename (e.g., Q4_K_M)
                fname = gguf_files[0].stem
                for q in (
                    "Q2_K",
                    "Q3_K_S",
                    "Q3_K_M",
                    "Q3_K_L",
                    "Q4_0",
                    "Q4_K_S",
                    "Q4_K_M",
                    "Q5_0",
                    "Q5_K_S",
                    "Q5_K_M",
                    "Q6_K",
                    "Q8_0",
                ):
                    if q in fname.upper():
                        metadata["quantization"] = q
                        break

        return metadata

    def _estimate_parameters(self, config: dict) -> str:
        """Estimate parameter count from model config."""
        hidden = config.get("hidden_size", 0)
        layers = config.get("num_hidden_layers", 0)
        vocab = config.get("vocab_size", 0)
        intermediate = config.get("intermediate_size", 0)

        if hidden and layers and vocab:
            # Rough estimate: embedding + transformer layers
            params = vocab * hidden  # embedding
            params += layers * (4 * hidden * hidden + 2 * hidden * intermediate)
            if params > 1_000_000_000:
                return f"{params / 1_000_000_000:.0f}B"
            if params > 1_000_000:
                return f"{params / 1_000_000:.0f}M"
            return str(params)
        return ""


class HardwareDetector:
    """Detects available hardware capabilities."""

    def detect(self) -> HardwareInfo:
        """Detect available hardware."""
        cpu_cores = os.cpu_count() or 1
        ram_bytes = self._get_ram()
        apple_silicon = platform.system() == "Darwin" and platform.machine() == "arm64"
        gpu_type = "none"
        gpu_vram = 0

        if apple_silicon:
            gpu_type = "apple_silicon"
            # Unified memory — GPU shares RAM
            gpu_vram = ram_bytes
        else:
            gpu_type, gpu_vram = self._detect_gpu()

        return HardwareInfo(
            cpu_cores=cpu_cores,
            ram_bytes=ram_bytes,
            gpu_type=gpu_type,
            gpu_vram=gpu_vram,
            apple_silicon=apple_silicon,
        )

    def can_run_model(self, size_bytes: int) -> tuple[bool, list[str]]:
        """Check if a model can run on this hardware.

        Returns (can_run, list_of_warnings).
        """
        info = self.detect()
        warnings: list[str] = []

        if info.ram_bytes > 0:
            usage_ratio = size_bytes / info.ram_bytes
            if usage_ratio > 0.95:
                return False, [
                    f"Model requires ~{_fmt_bytes(size_bytes)} but only "
                    f"{_fmt_bytes(info.ram_bytes)} RAM available. "
                    "Try a smaller quantization or use a remote provider."
                ]
            if usage_ratio > 0.80:
                warnings.append(
                    f"Model may use ~{_fmt_bytes(size_bytes)} "
                    f"({usage_ratio:.0%} of {_fmt_bytes(info.ram_bytes)} RAM). "
                    "Performance may be degraded."
                )

        return True, warnings

    def _get_ram(self) -> int:
        """Get total system RAM in bytes."""
        try:
            import psutil

            return int(psutil.virtual_memory().total)
        except ImportError:
            pass

        # Fallback: read from sysctl on macOS / /proc/meminfo on Linux
        if platform.system() == "Darwin":
            try:
                import subprocess

                result = subprocess.run(
                    ["sysctl", "-n", "hw.memsize"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                return int(result.stdout.strip())
            except (subprocess.SubprocessError, ValueError):
                pass
        elif platform.system() == "Linux":
            try:
                with open("/proc/meminfo") as f:
                    for line in f:
                        if line.startswith("MemTotal:"):
                            return int(line.split()[1]) * 1024  # kB to bytes
            except OSError:
                pass

        return 0

    def _detect_gpu(self) -> tuple[str, int]:
        """Detect GPU type and VRAM."""
        # Try CUDA via torch
        try:
            import torch

            if torch.cuda.is_available():
                vram = torch.cuda.get_device_properties(0).total_memory
                return "cuda", vram
        except ImportError:
            pass

        return "none", 0


def _fmt_bytes(n: int) -> str:
    """Format bytes as human-readable string."""
    if n >= 1_073_741_824:
        return f"{n / 1_073_741_824:.1f} GB"
    if n >= 1_048_576:
        return f"{n / 1_048_576:.1f} MB"
    return f"{n} bytes"
