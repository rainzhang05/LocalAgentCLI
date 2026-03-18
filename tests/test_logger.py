"""Tests for localagentcli.storage.logger."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import pytest

from localagentcli.storage.logger import NORMAL, VERBOSE, Logger


class TestLoggerInit:
    """Tests for Logger construction and level parsing."""

    def test_default_level(self, storage):
        logger = Logger(storage.logs_dir)
        assert logger._level == NORMAL

    def test_verbose_level(self, storage):
        logger = Logger(storage.logs_dir, level="verbose")
        assert logger._level == VERBOSE

    def test_debug_level(self, storage):
        logger = Logger(storage.logs_dir, level="debug")
        assert logger._level == logging.DEBUG

    def test_unknown_level_defaults_to_normal(self, storage):
        logger = Logger(storage.logs_dir, level="unknown")
        assert logger._level == NORMAL

    def test_creates_log_file(self, storage):
        Logger(storage.logs_dir)
        date_str = datetime.now().strftime("%Y%m%d")
        log_file = storage.logs_dir / f"localagent_{date_str}.log"
        assert log_file.exists()


class TestLoggerMethods:
    """Tests for logging at various levels."""

    def test_normal_writes_to_file(self, storage):
        logger = Logger(storage.logs_dir, level="normal")
        logger.normal("Test normal message")
        content = self._read_log(storage)
        assert "Test normal message" in content

    def test_verbose_not_written_at_normal_level(self, storage):
        logger = Logger(storage.logs_dir, level="normal")
        logger.verbose("Verbose message")
        content = self._read_log(storage)
        assert "Verbose message" not in content

    def test_verbose_written_at_verbose_level(self, storage):
        logger = Logger(storage.logs_dir, level="verbose")
        logger.verbose("Verbose message")
        content = self._read_log(storage)
        assert "Verbose message" in content

    def test_debug_written_at_debug_level(self, storage):
        logger = Logger(storage.logs_dir, level="debug")
        logger.debug("Debug message")
        content = self._read_log(storage)
        assert "Debug message" in content

    def test_debug_not_written_at_normal_level(self, storage):
        logger = Logger(storage.logs_dir, level="normal")
        logger.debug("Debug message")
        content = self._read_log(storage)
        assert "Debug message" not in content

    def test_error_always_written(self, storage):
        logger = Logger(storage.logs_dir, level="normal")
        logger.error("Error message")
        content = self._read_log(storage)
        assert "Error message" in content

    def test_format_string_args(self, storage):
        logger = Logger(storage.logs_dir, level="normal")
        logger.normal("Session %s started", "abc123")
        content = self._read_log(storage)
        assert "Session abc123 started" in content

    def _read_log(self, storage) -> str:
        date_str = datetime.now().strftime("%Y%m%d")
        log_file = storage.logs_dir / f"localagent_{date_str}.log"
        # Flush handlers
        for handler in logging.getLogger().handlers:
            handler.flush()
        return log_file.read_text() if log_file.exists() else ""


class TestLoggerSetLevel:
    """Tests for runtime level changes."""

    def test_change_to_debug(self, storage):
        logger = Logger(storage.logs_dir, level="normal")
        logger.set_level("debug")
        logger.debug("Now visible")
        content = self._read_log(storage)
        assert "Now visible" in content

    def test_change_to_normal(self, storage):
        logger = Logger(storage.logs_dir, level="debug")
        logger.set_level("normal")
        logger.debug("Should not appear")
        content = self._read_log(storage)
        assert "Should not appear" not in content

    def _read_log(self, storage) -> str:
        date_str = datetime.now().strftime("%Y%m%d")
        log_file = storage.logs_dir / f"localagent_{date_str}.log"
        return log_file.read_text() if log_file.exists() else ""


class TestCustomLevels:
    """Tests for custom log level registration."""

    def test_normal_level_value(self):
        assert NORMAL == 25

    def test_verbose_level_value(self):
        assert VERBOSE == 15

    def test_normal_between_info_and_warning(self):
        assert logging.INFO < NORMAL < logging.WARNING

    def test_verbose_between_debug_and_info(self):
        assert logging.DEBUG < VERBOSE < logging.INFO
