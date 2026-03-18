"""Logger — leveled file-based logging for LocalAgentCLI."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

# Register custom log levels
NORMAL = 25
VERBOSE = 15

logging.addLevelName(NORMAL, "INFO")
logging.addLevelName(VERBOSE, "VERBOSE")


class Logger:
    """File-based logger with custom levels: normal, verbose, debug."""

    NORMAL = NORMAL
    VERBOSE = VERBOSE

    def __init__(self, logs_dir: Path, level: str = "normal"):
        self._logs_dir = logs_dir
        self._level = self._parse_level(level)
        self._logger = self._setup_logger()

    def _parse_level(self, level: str) -> int:
        """Convert string level to numeric level."""
        levels = {
            "normal": self.NORMAL,
            "verbose": self.VERBOSE,
            "debug": logging.DEBUG,
        }
        return levels.get(level, self.NORMAL)

    def _setup_logger(self) -> logging.Logger:
        """Configure the logger with file handler and formatter."""
        logger = logging.getLogger(f"localagent.{id(self)}")
        logger.setLevel(self._level)

        # Avoid duplicate handlers
        if logger.handlers:
            return logger

        date_str = datetime.now().strftime("%Y%m%d")
        log_file = self._logs_dir / f"localagent_{date_str}.log"

        handler = logging.FileHandler(log_file, encoding="utf-8")
        handler.setLevel(self._level)

        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

        # Prevent propagation to root logger
        logger.propagate = False

        return logger

    def set_level(self, level: str) -> None:
        """Change the log level at runtime."""
        self._level = self._parse_level(level)
        self._logger.setLevel(self._level)
        for handler in self._logger.handlers:
            handler.setLevel(self._level)

    def normal(self, message: str, **kwargs) -> None:
        """Log at normal level (key events)."""
        self._logger.log(self.NORMAL, message, **kwargs)

    def verbose(self, message: str, **kwargs) -> None:
        """Log at verbose level (detailed events)."""
        self._logger.log(self.VERBOSE, message, **kwargs)

    def debug(self, message: str, **kwargs) -> None:
        """Log at debug level (everything)."""
        self._logger.debug(message, **kwargs)

    def error(self, message: str, **kwargs) -> None:
        """Log an error (always logged regardless of level)."""
        self._logger.error(message, **kwargs)
