"""Local plugin manager (filesystem-backed)."""

from __future__ import annotations

import json
import shutil
import tempfile
import urllib.parse
import urllib.request
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

    def discover_workspace_plugins(self, workspace: Path) -> list[LocalPlugin]:
        """Discover plugin-like artifacts in common workspace plugin directories."""
        root = workspace.expanduser().resolve()
        if not root.exists():
            return []

        discovered: dict[str, LocalPlugin] = {}
        for base in (root / "plugins", root / ".plugins", root / ".github" / "plugins"):
            if not base.is_dir():
                continue
            for entry in sorted(base.iterdir(), key=lambda item: item.name.lower()):
                if entry.name.startswith("."):
                    continue
                if entry.is_dir():
                    plugin = LocalPlugin(name=entry.name, path=entry, kind="directory")
                elif entry.is_file():
                    plugin = LocalPlugin(name=entry.stem, path=entry, kind="file")
                else:
                    continue
                discovered[str(plugin.path)] = plugin
        return list(discovered.values())

    def sync_from_workspace(self, workspace: Path) -> list[LocalPlugin]:
        """Install plugins discovered in a workspace that are not yet installed."""
        installed_names = {plugin.name for plugin in self.list_plugins()}
        synced: list[LocalPlugin] = []
        for candidate in self.discover_workspace_plugins(workspace):
            if candidate.name in installed_names:
                continue
            synced_plugin = self.install_from_path(candidate.path, name=candidate.name)
            synced.append(synced_plugin)
            installed_names.add(candidate.name)
        return synced

    def sync_from_manifest_url(
        self,
        manifest_url: str,
        *,
        timeout: float = 20.0,
    ) -> list[LocalPlugin]:
        """Sync plugins from a remote JSON manifest URL.

        Expected schema:
        {
          "plugins": [
            {"name": "example", "url": "https://.../plugin.py"}
          ]
        }
        """
        payload = self._load_manifest(manifest_url, timeout=timeout)
        entries = payload.get("plugins", []) if isinstance(payload, dict) else []
        if not isinstance(entries, list):
            return []

        installed_names = {plugin.name for plugin in self.list_plugins()}
        synced: list[LocalPlugin] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name", "")).strip()
            url = str(entry.get("url", "")).strip()
            if not name or not url or name in installed_names:
                continue
            downloaded = self._download_to_temp_file(url, timeout=timeout)
            try:
                plugin = self.install_from_path(downloaded, name=name)
            finally:
                try:
                    downloaded.unlink(missing_ok=True)
                except Exception:
                    pass
            synced.append(plugin)
            installed_names.add(name)
        return synced

    def _load_manifest(self, manifest_url: str, *, timeout: float) -> dict:
        with urllib.request.urlopen(manifest_url, timeout=max(timeout, 0.1)) as response:
            content = response.read().decode("utf-8", errors="replace")
        payload = json.loads(content)
        return payload if isinstance(payload, dict) else {}

    def _download_to_temp_file(self, url: str, *, timeout: float) -> Path:
        suffix = Path(urllib.parse.urlparse(url).path).suffix or ".bin"
        with urllib.request.urlopen(url, timeout=max(timeout, 0.1)) as response:
            data = response.read()
        fd, temp_path = tempfile.mkstemp(prefix="localagent-plugin-", suffix=suffix)
        path = Path(temp_path)
        with open(fd, "wb", closefd=True) as handle:
            handle.write(data)
        return path

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
