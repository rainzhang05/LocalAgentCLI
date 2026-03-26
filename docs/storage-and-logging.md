# LocalAgentCLI — Storage and Logging

This document defines the filesystem layout, storage management, and logging system.

---

## Storage Root

All persistent data is stored under `~/.localagent/`:

```
~/.localagent/
├── config.toml              # Global configuration (see session-and-config.md)
├── registry.json            # Model registry (see model-system.md)
├── sessions.db              # SQLite session store (when features.sqlite_session_store=true)
├── models/                  # Downloaded model files
│   ├── codellama-7b/
│   │   └── v1/
│   │       ├── model.gguf
│   │       └── metadata.json
│   └── mistral-7b/
│       └── v1/
│           └── ...
├── sessions/                # Saved sessions
│   ├── refactor-auth.json
│   └── session_20250115_103000.json
├── logs/                    # Log files
│   ├── localagent_20250115.log
│   └── exports/
│       └── export_20250115_113000.json
├── cache/                   # Temporary data
│   ├── rollback/            # File backups for undo (see safety-and-permissions.md)
│   │   └── <session-id>/
│   ├── runtime-events/      # Append-only per-session submission/event JSONL logs
│   │   └── <session-id>.jsonl
│   └── downloads/           # In-progress model downloads + installer telemetry sidecar
│       └── install_telemetry.jsonl
└── secrets/                 # Encrypted API keys (fallback; see remote-providers.md)
    └── keys.enc
```

---

## Directory Responsibilities

| Directory | Contents | Lifetime |
|---|---|---|
| `config.toml` | Global configuration | Permanent (user-managed) |
| `registry.json` | Model index | Updated on model install/remove |
| `models/` | Model weights and metadata | Until user removes model |
| `sessions/` | Saved session snapshots (default store / legacy compatibility) | Until user deletes session |
| `sessions.db` | SQLite session store (opt-in via `features.sqlite_session_store`) | Until user deletes or resets session data |
| `logs/` | Runtime logs and exports | Configurable retention (default: 30 days) |
| `cache/` | Temporary data (rollbacks, runtime event logs, downloads, installer telemetry sidecar) | Short-lived (auto-cleaned) |

Runtime event logs (`cache/runtime-events/<session-id>.jsonl`) are append-only and are used for best-effort session replay reconciliation during `/session load`.

Installer download telemetry is append-only JSONL (`install_telemetry.jsonl`).
Current records use telemetry schema version `2` and include completion-path and
cache/download counters for completion recap diagnostics.
| `secrets/` | Encrypted API keys | Until user removes provider |

---

## StorageManager

The `StorageManager` is responsible for initializing the directory structure and providing path helpers.

```python
# localagentcli/storage/manager.py

class StorageManager:
    def __init__(self, root: Path = None):
        self._root = root or Path.home() / ".localagent"

    def initialize(self) -> None:
        """Create the directory structure if it doesn't exist.
        Called at application startup.

        Creates:
        - ~/.localagent/
        - ~/.localagent/models/
        - ~/.localagent/sessions/
        - ~/.localagent/logs/
        - ~/.localagent/logs/exports/
        - ~/.localagent/cache/
        - ~/.localagent/cache/rollback/
        - ~/.localagent/cache/downloads/
        - ~/.localagent/secrets/

        Sets permissions on secrets/ to 700 (owner-only).
        """

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
        """Remove cache entries older than max_age_hours.
        Called periodically (e.g., at startup).
        """

    def cleanup_logs(self, max_age_days: int = 30) -> None:
        """Remove log files older than max_age_days.
        Called periodically (e.g., at startup).
        """

    def disk_usage(self) -> dict:
        """Return disk usage breakdown by directory (models, sessions, logs, cache)."""
```

---

## Logging System

### Log Levels

| Level | Description | When to Use |
|---|---|---|
| `normal` | Key events: commands executed, mode changes, model loaded, errors | Default level. Always logged. |
| `verbose` | Detailed events: tool calls with arguments, approval decisions, compaction events | Debugging user-facing issues |
| `debug` | Everything: raw model input/output, HTTP requests/responses, internal state changes | Development and bug investigation |

The logging level is set via `general.logging_level` in `config.toml` or at runtime via `/config logging_level <level>`.

### Log Format

#### File Log (normal)
```
2025-01-15 10:30:05 [INFO] Session started (id: abc123)
2025-01-15 10:30:10 [INFO] Model loaded: codellama-7b (gguf)
2025-01-15 10:30:15 [INFO] Mode: agent
2025-01-15 10:30:20 [INFO] Tool: file_read src/main.py (approved)
2025-01-15 10:30:25 [ERROR] Tool: shell_execute failed (exit code 1)
```

#### File Log (verbose)
```
2025-01-15 10:30:20 [VERBOSE] Tool: file_read
  path: src/main.py
  result: success (245 lines, 0.02s)
2025-01-15 10:30:22 [VERBOSE] Approval: patch_apply src/main.py → approved by user
2025-01-15 10:30:25 [VERBOSE] Tool: shell_execute
  command: npm test
  exit_code: 1
  stderr: "2 tests failed"
  duration: 3.5s
```

#### File Log (debug)
```
2025-01-15 10:30:18 [DEBUG] Model request:
  messages: [{"role": "user", "content": "refactor auth..."}]
  tools: [file_read, file_write, patch_apply, shell_execute, ...]
2025-01-15 10:30:20 [DEBUG] Model response chunk: {"text": "I'll start by...", "is_reasoning": true}
```

### Log Storage

- Log files are stored in `~/.localagent/logs/`
- One log file per day: `localagent_YYYYMMDD.log`
- Logs are appended to the current day's file
- Log files older than the retention period (default: 30 days) are auto-deleted at startup

### Logger

```python
# localagentcli/storage/logger.py

import logging

class Logger:
    NORMAL = 25   # Custom level between INFO and WARNING
    VERBOSE = 15  # Custom level between DEBUG and INFO

    def __init__(self, logs_dir: Path, level: str = "normal"):
        self._logs_dir = logs_dir
        self._level = self._parse_level(level)
        self._logger = self._setup_logger()

    def _parse_level(self, level: str) -> int:
        """Convert string level to numeric level."""
        return {"normal": self.NORMAL, "verbose": self.VERBOSE, "debug": logging.DEBUG}[level]

    def _setup_logger(self) -> logging.Logger:
        """Configure the logger with file handler and formatter."""

    def set_level(self, level: str) -> None:
        """Change the log level at runtime."""

    def normal(self, message: str, **kwargs) -> None:
        """Log at normal level."""

    def verbose(self, message: str, **kwargs) -> None:
        """Log at verbose level."""

    def debug(self, message: str, **kwargs) -> None:
        """Log at debug level."""

    def error(self, message: str, **kwargs) -> None:
        """Log an error (always logged regardless of level)."""
```

### Log Commands

#### `/logs show`

```
> /logs show

2025-01-15 10:30:05 [INFO] Session started
2025-01-15 10:30:10 [INFO] Model loaded: codellama-7b
2025-01-15 10:30:15 [INFO] Mode: agent
...
```

- Optional level filter: `/logs show verbose` (shows verbose and above)
- Optional count: `/logs show 20` (last 20 entries)
- Combines: `/logs show verbose 20`

#### `/logs export`

```
> /logs export json
Exported to ~/.localagent/logs/exports/export_20250115_113000.json

> /logs export text ~/Desktop/session-log.txt
Exported to ~/Desktop/session-log.txt
```

### Export Formats

#### Text Export
Plain text format matching the log file format. Includes all log entries for the current session.

#### JSON Export

```json
{
  "session_id": "abc123",
  "exported_at": "2025-01-15T11:30:00Z",
  "entries": [
    {
      "timestamp": "2025-01-15T10:30:05Z",
      "level": "normal",
      "category": "session",
      "message": "Session started",
      "data": {"session_id": "abc123"}
    },
    {
      "timestamp": "2025-01-15T10:30:20Z",
      "level": "verbose",
      "category": "tool",
      "message": "file_read executed",
      "data": {
        "tool": "file_read",
        "args": {"path": "src/main.py"},
        "result": "success",
        "duration": 0.02
      }
    }
  ]
}
```

---

## File Locking

When multiple LocalAgentCLI instances run simultaneously, they share the `~/.localagent/` directory. File-level locking is required for:

| File | Lock Type | Reason |
|---|---|---|
| `config.toml` | Read/write lock | Prevent concurrent config writes |
| `registry.json` | Read/write lock | Prevent concurrent registry modifications |
| Log files | Append lock | Multiple instances may log simultaneously |

Use `fcntl.flock()` on POSIX systems and `msvcrt.locking()` on Windows, or a cross-platform library like `filelock`.

---

## Secrets Directory Permissions

The `~/.localagent/secrets/` directory must have restricted permissions:
- POSIX: `chmod 700` (owner read/write/execute only)
- Windows: ACL restricted to current user only
- Files within: `chmod 600` (owner read/write only)

The `StorageManager.initialize()` method must set these permissions when creating the directory.
