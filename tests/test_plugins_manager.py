"""Tests for localagentcli.plugins.manager."""

from __future__ import annotations

from pathlib import Path

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
