"""StorageManager — directory layout and file I/O for ~/.localagent/."""

from __future__ import annotations

import os
import time
from pathlib import Path


class StorageManager:
    """Manages the ~/.localagent/ directory structure and provides path helpers."""

    def __init__(self, root: Path | None = None):
        self._root = root or Path.home() / ".localagent"

    def initialize(self) -> None:
        """Create the directory structure if it doesn't exist.

        Called at application startup.
        """
        dirs = [
            self._root,
            self.models_dir,
            self.plugins_dir,
            self.sessions_dir,
            self.logs_dir,
            self.logs_dir / "exports",
            self.cache_dir,
            self.cache_dir / "rollback",
            self.cache_dir / "runtime-events",
            self.cache_dir / "downloads",
            self.secrets_dir,
        ]
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)

        # Restrict secrets directory to owner-only access
        try:
            os.chmod(self.secrets_dir, 0o700)
        except OSError:
            pass  # Best-effort on platforms where chmod is limited

    @property
    def root(self) -> Path:
        return self._root

    @property
    def config_path(self) -> Path:
        return self._root / "config.toml"

    @property
    def registry_path(self) -> Path:
        return self._root / "registry.json"

    @property
    def models_dir(self) -> Path:
        return self._root / "models"

    @property
    def plugins_dir(self) -> Path:
        return self._root / "plugins"

    @property
    def sessions_dir(self) -> Path:
        return self._root / "sessions"

    @property
    def logs_dir(self) -> Path:
        return self._root / "logs"

    @property
    def cache_dir(self) -> Path:
        return self._root / "cache"

    @property
    def secrets_dir(self) -> Path:
        return self._root / "secrets"

    def cleanup_cache(self, max_age_hours: int = 24) -> None:
        """Remove cache entries older than max_age_hours."""
        cutoff = time.time() - (max_age_hours * 3600)
        for subdir in (
            self.cache_dir / "rollback",
            self.cache_dir / "runtime-events",
            self.cache_dir / "downloads",
        ):
            if not subdir.exists():
                continue
            for entry in subdir.iterdir():
                try:
                    if entry.stat().st_mtime < cutoff:
                        if entry.is_dir():
                            _rmtree(entry)
                        else:
                            entry.unlink()
                except OSError:
                    pass

    def cleanup_logs(self, max_age_days: int = 30) -> None:
        """Remove log files older than max_age_days."""
        cutoff = time.time() - (max_age_days * 86400)
        if not self.logs_dir.exists():
            return
        for entry in self.logs_dir.iterdir():
            if entry.is_file() and entry.suffix == ".log":
                try:
                    if entry.stat().st_mtime < cutoff:
                        entry.unlink()
                except OSError:
                    pass

    def disk_usage(self) -> dict:
        """Return disk usage breakdown by directory in bytes."""
        result = {}
        for name, path in [
            ("models", self.models_dir),
            ("plugins", self.plugins_dir),
            ("sessions", self.sessions_dir),
            ("logs", self.logs_dir),
            ("cache", self.cache_dir),
        ]:
            total = 0
            if path.exists():
                for f in path.rglob("*"):
                    if f.is_file():
                        try:
                            total += f.stat().st_size
                        except OSError:
                            pass
            result[name] = total
        return result


def _rmtree(path: Path) -> None:
    """Recursively remove a directory tree."""
    for child in path.iterdir():
        if child.is_dir():
            _rmtree(child)
        else:
            child.unlink()
    path.rmdir()
