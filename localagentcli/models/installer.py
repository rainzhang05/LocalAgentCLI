"""ModelInstaller — download models from HuggingFace Hub or direct URLs."""

from __future__ import annotations

import json
import re
import shutil
import time
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from rich.console import Console

from localagentcli.models.detector import ModelDetector
from localagentcli.models.readiness import local_capability_provenance
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


@dataclass(frozen=True)
class _DownloadTelemetry:
    """Persisted metrics captured during one model install attempt."""

    source: str
    status: str
    started_at: str
    finished_at: str
    duration_seconds: float
    model_name: str
    version: str
    bytes_downloaded: int | None = None
    bytes_cached: int | None = None
    bytes_total: int | None = None
    files_total: int | None = None
    files_cached: int | None = None
    error: str | None = None
    repo: str | None = None
    url: str | None = None


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
        started_at = datetime.now(tz=timezone.utc)
        started_monotonic = time.perf_counter()

        self._console.print(f"[dim]Downloading {repo} → {name} ({version})...[/dim]")

        files_total: int | None = None
        files_cached: int | None = None
        bytes_total: int | None = None
        bytes_cached: int | None = None
        try:
            plan_metrics = self._download_hf(repo, target_dir)
            if plan_metrics is not None:
                files_total, files_cached, bytes_total, bytes_cached = plan_metrics
        except Exception as e:
            self._record_download_telemetry(
                _DownloadTelemetry(
                    source="huggingface",
                    status="failed",
                    started_at=started_at.isoformat(),
                    finished_at=datetime.now(tz=timezone.utc).isoformat(),
                    duration_seconds=max(time.perf_counter() - started_monotonic, 0.0),
                    model_name=name,
                    version=version,
                    bytes_cached=bytes_cached,
                    bytes_total=bytes_total,
                    files_total=files_total,
                    files_cached=files_cached,
                    error=str(e),
                    repo=repo,
                )
            )
            return InstallResult(success=False, message=f"Download failed: {e}")

        bytes_downloaded = (
            max((bytes_total or 0) - (bytes_cached or 0), 0) if bytes_total is not None else None
        )
        return self._detect_and_register(
            name,
            version,
            target_dir,
            {"source": "huggingface", "repo": repo},
            download_telemetry=_DownloadTelemetry(
                source="huggingface",
                status="success",
                started_at=started_at.isoformat(),
                finished_at=datetime.now(tz=timezone.utc).isoformat(),
                duration_seconds=max(time.perf_counter() - started_monotonic, 0.0),
                model_name=name,
                version=version,
                bytes_downloaded=bytes_downloaded,
                bytes_cached=bytes_cached,
                bytes_total=bytes_total,
                files_total=files_total,
                files_cached=files_cached,
                repo=repo,
            ),
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
        started_at = datetime.now(tz=timezone.utc)
        started_monotonic = time.perf_counter()

        # Download to cache first, then move
        download_dir = self._cache_dir / "downloads" / name
        download_dir.mkdir(parents=True, exist_ok=True)

        parsed = urlparse(url)
        filename = Path(parsed.path).name or "model"
        download_path = download_dir / filename

        self._console.print(f"[dim]Downloading {url} → {name} ({version})...[/dim]")

        try:
            bytes_downloaded = self._download_url(url, download_path)
        except Exception as e:
            # Clean up partial download
            if download_path.exists():
                download_path.unlink(missing_ok=True)
            self._record_download_telemetry(
                _DownloadTelemetry(
                    source="url",
                    status="failed",
                    started_at=started_at.isoformat(),
                    finished_at=datetime.now(tz=timezone.utc).isoformat(),
                    duration_seconds=max(time.perf_counter() - started_monotonic, 0.0),
                    model_name=name,
                    version=version,
                    error=str(e),
                    url=url,
                )
            )
            return InstallResult(success=False, message=f"Download failed: {e}")

        # Move downloaded file to target directory
        final_path = target_dir / filename
        shutil.move(str(download_path), str(final_path))

        # Clean up download dir if empty
        try:
            download_dir.rmdir()
        except OSError:
            pass

        return self._detect_and_register(
            name,
            version,
            target_dir,
            {"source": "url", "url": url},
            download_telemetry=_DownloadTelemetry(
                source="url",
                status="success",
                started_at=started_at.isoformat(),
                finished_at=datetime.now(tz=timezone.utc).isoformat(),
                duration_seconds=max(time.perf_counter() - started_monotonic, 0.0),
                model_name=name,
                version=version,
                bytes_downloaded=bytes_downloaded,
                bytes_total=bytes_downloaded,
                url=url,
            ),
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _download_hf(
        self,
        repo: str,
        target_dir: Path,
    ) -> tuple[int, int, int, int] | None:
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
            total_bytes = sum(item.size_bytes or 0 for item in download_plan)
            cached_bytes = sum((item.size_bytes or 0) for item in download_plan if item.is_cached)
            files_total = len(download_plan)
            files_cached = sum(1 for item in download_plan if item.is_cached)
            return files_total, files_cached, total_bytes, cached_bytes

        snapshot_download(
            repo_id=repo,
            local_dir=str(target_dir),
            max_workers=1,
            tqdm_class=_make_fast_tqdm_class(),
        )
        return None

    def _download_url(self, url: str, target_path: Path) -> int:
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
                return existing_size

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
        if total_bytes is not None:
            return total_bytes
        return target_path.stat().st_size

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
        label_width = _download_label_width(self._console)

        with _build_download_progress(self._console) as progress:
            task_id = progress.add_task(
                "Preparing download",
                total=total_bytes or None,
                completed=completed_bytes,
            )

            for item in plan:
                progress.update(
                    task_id,
                    description=_format_download_label(item.filename, max_width=label_width),
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
                        label_width=label_width,
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
        *,
        download_telemetry: _DownloadTelemetry | None = None,
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
        telemetry_for_record = download_telemetry
        if download_telemetry is not None:
            if download_telemetry.bytes_downloaded is None:
                inferred_downloaded = self._calculate_size(model_dir)
                telemetry_for_record = replace(
                    download_telemetry,
                    bytes_downloaded=inferred_downloaded,
                    bytes_total=inferred_downloaded,
                )
            assert telemetry_for_record is not None
            metadata["download_telemetry"] = _telemetry_dict(telemetry_for_record)

        capabilities = self._infer_capabilities(name, result.metadata, source_info)
        entry = ModelEntry(
            name=name,
            version=version,
            format=result.format,
            path=str(model_dir),
            size_bytes=size_bytes,
            capabilities=capabilities,
            capability_provenance=local_capability_provenance(
                reasoning_supported=bool(capabilities.get("reasoning", False))
            ),
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
        if telemetry_for_record is not None:
            self._record_download_telemetry(telemetry_for_record)
            self._console.print(f"[dim]{_format_download_summary(telemetry_for_record)}[/dim]")
        return InstallResult(success=True, model_entry=entry, message="Installed successfully")

    def _record_download_telemetry(self, telemetry: _DownloadTelemetry) -> None:
        """Append one JSONL telemetry record for installer diagnostics."""
        telemetry_path = self._cache_dir / "downloads" / "install_telemetry.jsonl"
        telemetry_path.parent.mkdir(parents=True, exist_ok=True)
        with telemetry_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(_telemetry_dict(telemetry), sort_keys=True))
            handle.write("\n")

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


def _fmt_speed(bytes_downloaded: int, duration_seconds: float) -> str:
    """Format average download speed for telemetry summary output."""
    if duration_seconds <= 0:
        return "n/a"
    return f"{_fmt_size(int(bytes_downloaded / duration_seconds))}/s"


def _telemetry_dict(telemetry: _DownloadTelemetry) -> dict[str, object]:
    """Convert telemetry dataclass to a JSON-serializable dict."""
    return {
        "source": telemetry.source,
        "status": telemetry.status,
        "started_at": telemetry.started_at,
        "finished_at": telemetry.finished_at,
        "duration_seconds": round(telemetry.duration_seconds, 3),
        "model_name": telemetry.model_name,
        "version": telemetry.version,
        "bytes_downloaded": telemetry.bytes_downloaded,
        "bytes_cached": telemetry.bytes_cached,
        "bytes_total": telemetry.bytes_total,
        "files_total": telemetry.files_total,
        "files_cached": telemetry.files_cached,
        "error": telemetry.error,
        "repo": telemetry.repo,
        "url": telemetry.url,
    }


def _format_download_summary(telemetry: _DownloadTelemetry) -> str:
    """Render a concise post-install telemetry line for operators."""
    downloaded = telemetry.bytes_downloaded or 0
    parts = [
        "Download telemetry:",
        f"source={telemetry.source}",
        f"time={telemetry.duration_seconds:.2f}s",
    ]
    if downloaded > 0:
        parts.append(f"downloaded={_fmt_size(downloaded)}")
        parts.append(f"avg={_fmt_speed(downloaded, telemetry.duration_seconds)}")
    if telemetry.bytes_cached:
        parts.append(f"cached={_fmt_size(telemetry.bytes_cached)}")
    if telemetry.files_total:
        files_cached = telemetry.files_cached or 0
        parts.append(f"files={telemetry.files_total} (cached {files_cached})")
    return ", ".join(parts)


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


def _make_rich_hf_tqdm_class(
    progress,
    task_id: int,
    filename: str,
    *,
    label_width: int | None = None,
):
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
            progress.update(
                task_id,
                description=_format_download_label(filename, max_width=label_width),
                refresh=True,
            )

        def update(self, n=1):
            result = super().update(n)
            progress.update(
                task_id,
                advance=max(int(n), 0),
                description=_format_download_label(filename, max_width=label_width),
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


def _format_download_label(filename: str, *, max_width: int | None = None) -> str:
    """Create a compact progress label for one file."""
    path = Path(filename)
    label: str
    if len(path.parts) <= 2:
        label = f"Downloading {filename}"
    else:
        label = f"Downloading {path.parts[0]}/.../{path.name}"
    if max_width is None:
        return label
    return _truncate_with_ellipsis(label, max_width)


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


def _download_label_width(console: Console) -> int | None:
    """Return a conservative label width budget for progress descriptions."""
    width = _console_width(console)
    if width is None:
        return None
    return max(width - 20, 16)


def _console_width(console: Console) -> int | None:
    """Best-effort lookup of terminal width for narrow-layout safeguards."""
    width = getattr(console, "width", None)
    if isinstance(width, int) and width > 0:
        return width
    size = getattr(console, "size", None)
    if size is not None:
        columns = getattr(size, "width", None)
        if isinstance(columns, int) and columns > 0:
            return columns
    return None


def _truncate_with_ellipsis(text: str, max_width: int) -> str:
    """Truncate one line to a fixed width, preserving readability."""
    if max_width <= 0:
        return ""
    if len(text) <= max_width:
        return text
    if max_width <= 2:
        return text[:max_width]
    return f"{text[: max_width - 1]}…"


def _strip_hf_tqdm_kwargs(kwargs: dict[str, object]) -> None:
    """Remove hub-specific kwargs unsupported by some tqdm backends."""
    kwargs.pop("name", None)
