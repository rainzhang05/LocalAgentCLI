"""Shared test fixtures for LocalAgentCLI."""

from __future__ import annotations

from pathlib import Path

import pytest

from localagentcli.config.manager import ConfigManager
from localagentcli.session.manager import SessionManager
from localagentcli.storage.manager import StorageManager


@pytest.fixture
def tmp_root(tmp_path: Path) -> Path:
    """Return a temporary directory to use as ~/.localagent/ root."""
    return tmp_path / "localagent"


@pytest.fixture
def storage(tmp_root: Path) -> StorageManager:
    """Return an initialized StorageManager using a temp directory."""
    sm = StorageManager(root=tmp_root)
    sm.initialize()
    return sm


@pytest.fixture
def config(storage: StorageManager) -> ConfigManager:
    """Return a ConfigManager backed by the temp storage."""
    cm = ConfigManager(storage.config_path)
    cm.load()
    return cm


@pytest.fixture
def session_manager(storage: StorageManager, config: ConfigManager) -> SessionManager:
    """Return a SessionManager with a fresh session."""
    sm = SessionManager(storage.sessions_dir, config)
    sm.new_session()
    return sm
