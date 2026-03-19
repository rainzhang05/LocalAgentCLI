"""ModelInstaller — download models from HuggingFace Hub or direct URLs."""

from __future__ import annotations

import re
import shutil
from collections.abc import Sequence
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


@dataclass(frozen=True)
class _HFDownloadPlanItem:
    """One file in a live-progress Hugging Face download plan."""

    filename: str
    size_bytes: int | None
    is_cached: bool = False


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
            hf_hub_download, snapshot_download = _load_huggingface_downloaders()
        except ImportError:
            raise RuntimeError(
                "HuggingFace downloads require 'huggingface-hub'. "
                "Install it with: pip install huggingface-hub"
            ) from None

        download_plan = self._plan_hf_download(repo, target_dir, snapshot_download)
        if download_plan:
            self._download_hf_with_live_progress(
                repo,
                target_dir,
                hf_hub_download,
                download_plan,
            )
            return

        snapshot_download(
            repo_id=repo,
            local_dir=str(target_dir),
            max_workers=1,
            tqdm_class=_make_fast_tqdm_class(),
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

            with _build_download_progress(self._console) as progress:
                task = progress.add_task(
                    "Downloading",
                    total=total_bytes,
                    completed=existing_size,
                )
                with open(target_path, mode) as f:
                    for chunk in response.iter_bytes(chunk_size=8192):
                        f.write(chunk)
                        progress.update(task, advance=len(chunk), refresh=True)

    def _plan_hf_download(
        self,
        repo: str,
        target_dir: Path,
        snapshot_download,
    ) -> list[_HFDownloadPlanItem]:
        """Return a per-file download plan when the installed hub supports dry-run."""
        try:
            dry_run_items = snapshot_download(
                repo_id=repo,
                local_dir=str(target_dir),
                dry_run=True,
            )
        except TypeError:
            return []
        except Exception:
            return []

        if not isinstance(dry_run_items, Sequence):
            return []

        plan: list[_HFDownloadPlanItem] = []
        for item in dry_run_items:
            normalized = _normalize_hf_plan_item(item)
            if normalized is not None:
                plan.append(normalized)
        return plan

    def _download_hf_with_live_progress(
        self,
        repo: str,
        target_dir: Path,
        hf_hub_download,
        plan: Sequence[_HFDownloadPlanItem],
    ) -> None:
        """Download a repo file-by-file so the terminal progress stays live."""
        total_bytes = sum(item.size_bytes or 0 for item in plan)
        completed_bytes = sum((item.size_bytes or 0) for item in plan if item.is_cached)

        with _build_download_progress(self._console) as progress:
            task_id = progress.add_task(
                "Preparing download",
                total=total_bytes or None,
                completed=completed_bytes,
            )

            for item in plan:
                progress.update(
                    task_id,
                    description=_format_download_label(item.filename),
                    refresh=True,
                )
                hf_hub_download(
                    repo_id=repo,
                    filename=item.filename,
                    local_dir=str(target_dir),
                    tqdm_class=_make_rich_hf_tqdm_class(
                        progress,
                        task_id,
                        item.filename,
                    ),
                )

            if total_bytes > 0:
                progress.update(
                    task_id,
                    completed=total_bytes,
                    description="Download complete",
                    refresh=True,
                )
            else:
                progress.update(task_id, description="Download complete", refresh=True)

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
            capabilities=self._infer_capabilities(name, result.metadata, source_info),
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

    def _infer_capabilities(
        self,
        name: str,
        metadata: dict,
        source_info: dict,
    ) -> dict:
        """Infer conservative capabilities for a newly installed local model."""
        fingerprints = [
            name,
            str(metadata.get("model_type", "")),
            str(metadata.get("backend", "")),
            str(source_info.get("repo", "")),
            str(source_info.get("url", "")),
        ]
        fingerprint = " ".join(part.lower() for part in fingerprints if part)
        reasoning = any(
            re.search(pattern, fingerprint)
            for pattern in (
                r"reason",
                r"thinking",
                r"deepseek[-_/]r1",
                r"\bqwq\b",
                r"\br1\b",
            )
        )
        return {
            "tool_use": False,
            "reasoning": reasoning,
            "streaming": True,
        }

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


def _load_huggingface_downloaders():
    """Import the hub download helpers lazily."""
    from huggingface_hub import hf_hub_download, snapshot_download

    return hf_hub_download, snapshot_download


def _build_download_progress(console: Console):
    """Create a responsive progress bar for model downloads."""
    from rich.progress import (
        BarColumn,
        DownloadColumn,
        Progress,
        TextColumn,
        TimeRemainingColumn,
        TransferSpeedColumn,
    )

    return Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(compact=True),
        console=console,
        transient=False,
        refresh_per_second=20,
        speed_estimate_period=1.0,
    )


def _make_fast_tqdm_class():
    """Return a tqdm class tuned for more frequent refreshes."""
    tqdm_base = _load_tqdm_base()

    class FastTQDM(tqdm_base):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs):
            _strip_hf_tqdm_kwargs(kwargs)
            kwargs.setdefault("mininterval", 0.0)
            kwargs.setdefault("miniters", 1)
            kwargs.setdefault("smoothing", 0.0)
            super().__init__(*args, **kwargs)

    return FastTQDM


def _make_rich_hf_tqdm_class(progress, task_id: int, filename: str):
    """Bridge hf_hub_download byte updates into the shared Rich progress task."""
    tqdm_base = _load_tqdm_base()

    class RichHFTQDM(tqdm_base):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs):
            _strip_hf_tqdm_kwargs(kwargs)
            kwargs.setdefault("mininterval", 0.0)
            kwargs.setdefault("miniters", 1)
            kwargs.setdefault("leave", False)
            kwargs.setdefault("disable", False)
            kwargs.setdefault("smoothing", 0.0)
            super().__init__(*args, **kwargs)
            progress.update(task_id, description=_format_download_label(filename), refresh=True)

        def update(self, n=1):
            result = super().update(n)
            progress.update(
                task_id,
                advance=max(int(n), 0),
                description=_format_download_label(filename),
                refresh=True,
            )
            return result

    return RichHFTQDM


def _normalize_hf_plan_item(item: object) -> _HFDownloadPlanItem | None:
    """Best-effort normalization of huggingface_hub DryRunFileInfo objects."""
    filename = _attr_str(item, "filename", "file_name", "path")
    if not filename:
        return None
    return _HFDownloadPlanItem(
        filename=filename,
        size_bytes=_attr_int(item, "size", "file_size"),
        is_cached=_attr_bool(item, "is_cached", "already_exists", "already_downloaded"),
    )


def _format_download_label(filename: str) -> str:
    """Create a compact progress label for one file."""
    path = Path(filename)
    if len(path.parts) <= 2:
        return f"Downloading {filename}"
    return f"Downloading {path.parts[0]}/.../{path.name}"


def _attr_str(item: object, *names: str) -> str | None:
    for name in names:
        value = getattr(item, name, None)
        if isinstance(value, str) and value:
            return value
    return None


def _attr_int(item: object, *names: str) -> int | None:
    for name in names:
        value = getattr(item, name, None)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
    return None


def _attr_bool(item: object, *names: str) -> bool:
    for name in names:
        value = getattr(item, name, None)
        if isinstance(value, bool):
            return value
    return False


def _load_tqdm_base():
    """Return a tqdm-compatible base class, even when tqdm is unavailable in tests."""
    try:
        from tqdm.auto import tqdm as tqdm_auto

        return tqdm_auto
    except ImportError:

        class TQDMStub:
            def __init__(self, *args, **kwargs):
                initial = kwargs.get("initial", 0)
                self.n = initial if isinstance(initial, (int, float)) else 0
                self.total = kwargs.get("total")

            def update(self, n=1):
                if isinstance(n, (int, float)):
                    self.n += n
                return self.n

            def close(self):
                return None

        return TQDMStub


def _strip_hf_tqdm_kwargs(kwargs: dict[str, object]) -> None:
    """Remove hub-specific kwargs unsupported by some tqdm backends."""
    kwargs.pop("name", None)
