"""Local plugin manager (filesystem-backed)."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LocalPlugin:
    """One locally installed plugin artifact."""

    name: str
    path: Path
    kind: str


class PluginManager:
    """Manage local plugins under ~/.localagent/plugins."""

    def __init__(self, plugins_dir: Path):
        self._plugins_dir = plugins_dir
        self._plugins_dir.mkdir(parents=True, exist_ok=True)

    @property
    def plugins_dir(self) -> Path:
        return self._plugins_dir

    def list_plugins(self) -> list[LocalPlugin]:
        plugins: list[LocalPlugin] = []
        for entry in sorted(self._plugins_dir.iterdir(), key=lambda item: item.name.lower()):
            if entry.name.startswith("."):
                continue
            kind = "directory" if entry.is_dir() else "file"
            plugin_name = entry.stem if entry.is_file() else entry.name
            plugins.append(LocalPlugin(name=plugin_name, path=entry, kind=kind))
        return plugins

    def install_from_path(self, source: Path, *, name: str | None = None) -> LocalPlugin:
        src = source.expanduser().resolve()
        if not src.exists():
            raise FileNotFoundError(f"Plugin source does not exist: {src}")

        plugin_name = (name or src.stem or src.name).strip()
        if not plugin_name:
            raise ValueError("Plugin name could not be inferred; pass an explicit name.")
        if any(char in plugin_name for char in "/\\"):
            raise ValueError("Plugin name cannot contain path separators.")

        if src.is_dir():
            destination = self._plugins_dir / plugin_name
            if destination.exists():
                raise FileExistsError(f"Plugin '{plugin_name}' is already installed.")
            shutil.copytree(src, destination)
            return LocalPlugin(name=plugin_name, path=destination, kind="directory")

        destination = self._plugins_dir / f"{plugin_name}{src.suffix}"
        if destination.exists():
            raise FileExistsError(f"Plugin '{plugin_name}' is already installed.")
        shutil.copy2(src, destination)
        return LocalPlugin(name=plugin_name, path=destination, kind="file")

    def remove(self, name: str) -> LocalPlugin:
        target_name = name.strip()
        if not target_name:
            raise ValueError("Plugin name is required.")

        candidates = self.list_plugins()
        match = next((plugin for plugin in candidates if plugin.name == target_name), None)
        if match is None:
            raise FileNotFoundError(f"Plugin '{target_name}' is not installed.")

        if match.path.is_dir():
            shutil.rmtree(match.path)
        else:
            match.path.unlink()
        return match
