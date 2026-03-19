"""ModelInstaller — download models from HuggingFace Hub or direct URLs."""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from rich.console import Console

from localagentcli.models.detector import ModelDetector
from localagentcli.models.registry import ModelEntry, ModelRegistry


@dataclass
class InstallResult:
    """Result of a model installation."""

    success: bool
    model_entry: ModelEntry | None = None
    message: str = ""


class ModelInstaller:
    """Downloads and installs models from HuggingFace Hub or direct URLs."""

    def __init__(
        self,
        models_dir: Path,
        cache_dir: Path,
        registry: ModelRegistry,
        detector: ModelDetector,
        console: Console,
    ):
        self._models_dir = models_dir
        self._cache_dir = cache_dir
        self._registry = registry
        self._detector = detector
        self._console = console

    def install_from_hf(self, repo: str, name: str | None = None) -> InstallResult:
        """Install a model from the HuggingFace Hub.

        Args:
            repo: HuggingFace repo ID (e.g., "TheBloke/CodeLlama-7B-GGUF").
            name: Optional custom name. Derived from repo if not given.
        """
        if not name:
            name = self._derive_name_from_repo(repo)

        version = self._registry.next_version(name)
        target_dir = self._models_dir / name / version

        self._console.print(f"[dim]Downloading {repo} → {name} ({version})...[/dim]")

        try:
            self._download_hf(repo, target_dir)
        except Exception as e:
            return InstallResult(success=False, message=f"Download failed: {e}")

        return self._detect_and_register(
            name, version, target_dir, {"source": "huggingface", "repo": repo}
        )

    def install_from_url(self, url: str, name: str | None = None) -> InstallResult:
        """Install a model from a direct URL.

        Args:
            url: HTTP/HTTPS URL to the model file.
            name: Optional custom name. Derived from URL if not given.
        """
        if not name:
            name = self._derive_name_from_url(url)

        version = self._registry.next_version(name)
        target_dir = self._models_dir / name / version
        target_dir.mkdir(parents=True, exist_ok=True)

        # Download to cache first, then move
        download_dir = self._cache_dir / "downloads" / name
        download_dir.mkdir(parents=True, exist_ok=True)

        parsed = urlparse(url)
        filename = Path(parsed.path).name or "model"
        download_path = download_dir / filename

        self._console.print(f"[dim]Downloading {url} → {name} ({version})...[/dim]")

        try:
            self._download_url(url, download_path)
        except Exception as e:
            # Clean up partial download
            if download_path.exists():
                download_path.unlink(missing_ok=True)
            return InstallResult(success=False, message=f"Download failed: {e}")

        # Move downloaded file to target directory
        final_path = target_dir / filename
        shutil.move(str(download_path), str(final_path))

        # Clean up download dir if empty
        try:
            download_dir.rmdir()
        except OSError:
            pass

        return self._detect_and_register(name, version, target_dir, {"source": "url", "url": url})

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _download_hf(self, repo: str, target_dir: Path) -> None:
        """Download a HuggingFace repo using huggingface_hub."""
        try:
            from huggingface_hub import snapshot_download
        except ImportError:
            raise RuntimeError(
                "HuggingFace downloads require 'huggingface-hub'. "
                "Install it with: pip install huggingface-hub"
            ) from None

        snapshot_download(
            repo_id=repo,
            local_dir=str(target_dir),
            local_dir_use_symlinks=False,
        )

    def _download_url(self, url: str, target_path: Path) -> None:
        """Download a file from a URL with resume support."""
        import httpx

        headers: dict[str, str] = {}

        # Resume support
        existing_size = 0
        if target_path.exists():
            existing_size = target_path.stat().st_size
            headers["Range"] = f"bytes={existing_size}-"

        with httpx.stream("GET", url, headers=headers, follow_redirects=True) as response:
            if response.status_code == 416:
                # Range not satisfiable — file already complete
                return

            response.raise_for_status()

            mode = "ab" if existing_size > 0 and response.status_code == 206 else "wb"
            total = response.headers.get("content-length")
            total_bytes = int(total) + existing_size if total else None

            from rich.progress import (
                BarColumn,
                DownloadColumn,
                Progress,
                TransferSpeedColumn,
            )

            with Progress(
                "[progress.description]{task.description}",
                BarColumn(),
                DownloadColumn(),
                TransferSpeedColumn(),
                console=self._console,
            ) as progress:
                task = progress.add_task(
                    "Downloading",
                    total=total_bytes,
                    completed=existing_size,
                )
                with open(target_path, mode) as f:
                    for chunk in response.iter_bytes(chunk_size=8192):
                        f.write(chunk)
                        progress.update(task, advance=len(chunk))

    def _detect_and_register(
        self,
        name: str,
        version: str,
        model_dir: Path,
        source_info: dict,
    ) -> InstallResult:
        """Detect format, extract metadata, and register the model."""
        try:
            result = self._detector.detect(model_dir)
        except Exception as e:
            return InstallResult(
                success=False,
                message=f"Format detection failed: {e}",
            )

        size_bytes = self._calculate_size(model_dir)

        metadata = {
            **result.metadata,
            **source_info,
            "installed_at": datetime.now(tz=timezone.utc).isoformat(),
            "backend": result.backend,
        }

        entry = ModelEntry(
            name=name,
            version=version,
            format=result.format,
            path=str(model_dir),
            size_bytes=size_bytes,
            capabilities={
                "tool_use": False,
                "reasoning": False,
                "streaming": True,
            },
            metadata=metadata,
        )

        try:
            self._registry.register(entry)
        except Exception as e:
            return InstallResult(success=False, message=f"Registration failed: {e}")

        self._console.print(
            f"[green]✓ Installed {name} ({version}) — "
            f"{result.format} format, {_fmt_size(size_bytes)}[/green]"
        )
        return InstallResult(success=True, model_entry=entry, message="Installed successfully")

    def _calculate_size(self, directory: Path) -> int:
        """Calculate total size of all files in a directory."""
        total = 0
        for f in directory.rglob("*"):
            if f.is_file():
                try:
                    total += f.stat().st_size
                except OSError:
                    pass
        return total

    def _derive_name_from_repo(self, repo: str) -> str:
        """Derive a model name from a HuggingFace repo ID."""
        # "TheBloke/CodeLlama-7B-GGUF" → "codellama-7b-gguf"
        parts = repo.split("/")
        name = parts[-1] if parts else repo
        name = re.sub(r"[^a-zA-Z0-9._-]", "-", name)
        return name.lower()

    def _derive_name_from_url(self, url: str) -> str:
        """Derive a model name from a URL."""
        parsed = urlparse(url)
        filename = Path(parsed.path).stem or "model"
        name = re.sub(r"[^a-zA-Z0-9._-]", "-", filename)
        return name.lower()


def _fmt_size(n: int) -> str:
    """Format bytes as human-readable size."""
    if n >= 1_073_741_824:
        return f"{n / 1_073_741_824:.1f} GB"
    if n >= 1_048_576:
        return f"{n / 1_048_576:.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n} bytes"
