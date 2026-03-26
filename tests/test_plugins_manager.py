"""Tests for localagentcli.plugins.manager."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from localagentcli.plugins import PluginManager


def test_plugin_manager_lists_empty_directory(tmp_path: Path):
    manager = PluginManager(tmp_path / "plugins")

    assert manager.list_plugins() == []


def test_plugin_manager_install_and_remove_directory(tmp_path: Path):
    manager = PluginManager(tmp_path / "plugins")
    source = tmp_path / "plugin_src"
    source.mkdir()
    (source / "plugin.txt").write_text("data", encoding="utf-8")

    installed = manager.install_from_path(source, name="demo")

    assert installed.name == "demo"
    assert installed.kind == "directory"
    assert (manager.plugins_dir / "demo" / "plugin.txt").exists()

    removed = manager.remove("demo")
    assert removed.name == "demo"
    assert not (manager.plugins_dir / "demo").exists()


def test_plugin_manager_install_file_and_detect_duplicate(tmp_path: Path):
    manager = PluginManager(tmp_path / "plugins")
    source_file = tmp_path / "plugin.py"
    source_file.write_text("print('hi')\n", encoding="utf-8")

    installed = manager.install_from_path(source_file)
    assert installed.name == "plugin"
    assert installed.kind == "file"

    try:
        manager.install_from_path(source_file)
        raise AssertionError("Expected duplicate install to fail")
    except FileExistsError:
        pass


def test_plugin_manager_discovers_workspace_plugins(tmp_path: Path):
    manager = PluginManager(tmp_path / "plugins_store")
    workspace = tmp_path / "workspace"
    plugin_dir = workspace / "plugins" / "demo_plugin"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.txt").write_text("x", encoding="utf-8")

    discovered = manager.discover_workspace_plugins(workspace)

    assert len(discovered) == 1
    assert discovered[0].name == "demo_plugin"
    assert discovered[0].kind == "directory"


def test_plugin_manager_sync_from_workspace_installs_missing(tmp_path: Path):
    manager = PluginManager(tmp_path / "plugins_store")
    workspace = tmp_path / "workspace"
    plugin_dir = workspace / "plugins" / "demo_plugin"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.txt").write_text("x", encoding="utf-8")

    synced = manager.sync_from_workspace(workspace)

    assert len(synced) == 1
    assert synced[0].name == "demo_plugin"
    assert (manager.plugins_dir / "demo_plugin" / "plugin.txt").exists()


def test_plugin_manager_sync_from_manifest_url_downloads_and_installs(tmp_path: Path):
    manager = PluginManager(tmp_path / "plugins_store")

    class _FakeResponse:
        def __init__(self, payload: bytes):
            self._payload = payload

        def read(self) -> bytes:
            return self._payload

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def _fake_urlopen(url: str, timeout: float):
        if url.endswith("manifest.json"):
            return _FakeResponse(
                b'{"plugins":[{"name":"remote_demo","url":"https://example/plugin.py"}]}'
            )
        return _FakeResponse(b"print('remote plugin')\n")

    with patch("localagentcli.plugins.manager.urllib.request.urlopen", side_effect=_fake_urlopen):
        synced = manager.sync_from_manifest_url("https://example/manifest.json")

    assert len(synced) == 1
    assert synced[0].name == "remote_demo"
    assert synced[0].path.exists()
