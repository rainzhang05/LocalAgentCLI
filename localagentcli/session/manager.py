"""SessionManager — save/load/list/clear sessions."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from filelock import FileLock

from localagentcli.config.manager import ConfigManager
from localagentcli.session.state import Session

_SESSION_FORMAT_VERSION = 1


class SessionManager:
    """Manages session lifecycle: create, save, load, list, clear."""

    def __init__(
        self,
        sessions_dir: Path,
        config: ConfigManager,
        default_target_resolver: Callable[[str, str], tuple[str, str]] | None = None,
    ):
        self._dir = sessions_dir
        self._config = config
        self._current: Session | None = None
        self._default_target_resolver = default_target_resolver
        self._pending_default_target_warning = ""
        self._autosave_lock = threading.Lock()
        self._autosave_timer: threading.Timer | None = None

    def new_session(self) -> Session:
        """Create a fresh session with defaults from config."""
        now = datetime.now()
        provider = str(self._config.get("provider.active_provider", "") or "")
        model = str(self._config.get("model.active_model", "") or "")
        provider, model = self._resolve_default_target(provider, model)
        session = Session(
            id=str(uuid4()),
            name=None,
            mode=self._config.get("general.default_mode", "agent"),
            model=model,
            provider=provider,
            workspace=self._config.get("general.workspace", "."),
            created_at=now,
            updated_at=now,
        )
        self._current = session
        return session

    def save_session(self, name: str | None = None) -> Path:
        """Save the current session to disk. Returns the file path."""
        session = self.current
        if name is None:
            name = f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        session.name = name
        session.updated_at = datetime.now()
        session.metadata["message_count"] = len(session.history)

        path = self._dir / f"{name}.json"
        payload = dict(session.to_dict())
        payload["format_version"] = _SESSION_FORMAT_VERSION
        lock = FileLock(str(path) + ".lock")
        with lock:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)

        return path

    def load_session(self, name: str) -> Session:
        """Load a session from disk. Sets it as the current session."""
        path = self._dir / f"{name}.json"
        if not path.exists():
            raise FileNotFoundError(f"Session '{name}' not found")

        lock = FileLock(str(path) + ".lock")
        with lock:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)

        if isinstance(data, dict):
            data.pop("format_version", None)

        session = Session.from_dict(data)
        self._current = session
        return session

    def fork_session(self, name: str, fork_name: str | None = None) -> Session:
        """Fork a saved session into a new in-memory session with a fresh id."""
        source = self.load_session(name)
        parent_id = source.id
        data = source.to_dict()
        now = datetime.now()
        data["id"] = str(uuid4())
        data["name"] = fork_name or f"{name}_fork_{now.strftime('%Y%m%d_%H%M%S')}"
        data["created_at"] = now.isoformat()
        data["updated_at"] = now.isoformat()
        forked = Session.from_dict(data)
        forked.metadata["fork_parent_name"] = name
        forked.metadata["fork_parent_id"] = parent_id
        forked.metadata["forked_at"] = now.isoformat()
        self._current = forked
        return forked

    def list_sessions(self) -> list[dict]:
        """List all saved sessions with summary info."""
        sessions: list[dict] = []
        if not self._dir.exists():
            return sessions

        for path in sorted(self._dir.glob("*.json")):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                sessions.append(
                    {
                        "name": data.get("name", path.stem),
                        "created_at": data.get("created_at", ""),
                        "model": data.get("model", ""),
                        "mode": data.get("mode", ""),
                        "message_count": len(data.get("history", [])),
                    }
                )
            except (json.JSONDecodeError, OSError):
                continue

        return sessions

    def clear_session(self) -> None:
        """Clear history and tasks of the current session."""
        session = self.current
        session.history.clear()
        session.tasks.clear()
        session.updated_at = datetime.now()

    @property
    def current(self) -> Session:
        """The active session."""
        if self._current is None:
            raise RuntimeError("No active session. Call new_session() first.")
        return self._current

    def apply_config_override(self, key: str, value: Any) -> None:
        """Set a session-level config override (in memory only)."""
        self.current.config_overrides[key] = value

    def get_effective_config(self, key: str) -> Any:
        """Get a config value with session overrides applied.

        Session override > global config > default.
        """
        session = self.current
        if key in session.config_overrides:
            return session.config_overrides[key]
        return self._config.get(key)

    def consume_default_target_warning(self) -> str:
        """Return and clear any pending default-target repair warning."""
        warning = self._pending_default_target_warning
        self._pending_default_target_warning = ""
        return warning

    def schedule_named_autosave(self) -> None:
        """Schedule a debounced save when the session is named and autosave is enabled."""
        if self._current is None or not self._named_autosave_enabled():
            return
        if self._current.name is None:
            return
        raw = self.get_effective_config("sessions.autosave_debounce_seconds")
        try:
            debounce = float(raw)
        except (TypeError, ValueError):
            debounce = 2.0
        if debounce <= 0:
            debounce = 2.0

        def fire() -> None:
            self._named_autosave_fire()

        with self._autosave_lock:
            if self._autosave_timer is not None:
                self._autosave_timer.cancel()
                self._autosave_timer = None
            timer = threading.Timer(float(debounce), fire)
            timer.daemon = True
            self._autosave_timer = timer
            timer.start()

    def flush_named_autosave(self) -> None:
        """Cancel pending debounced autosave and persist immediately if enabled and named."""
        with self._autosave_lock:
            if self._autosave_timer is not None:
                self._autosave_timer.cancel()
                self._autosave_timer = None
        if not self._named_autosave_enabled():
            return
        try:
            session = self.current
        except RuntimeError:
            return
        if session.name is None:
            return
        try:
            self.save_session(session.name)
        except Exception:
            pass

    def cancel_named_autosave_timer(self) -> None:
        """Cancel a pending debounced autosave without writing (e.g. before tests or shutdown)."""
        with self._autosave_lock:
            if self._autosave_timer is not None:
                self._autosave_timer.cancel()
                self._autosave_timer = None

    def _named_autosave_enabled(self) -> bool:
        if self._current is None:
            return bool(self._config.get("sessions.autosave_named", False))
        return bool(self.get_effective_config("sessions.autosave_named"))

    def _named_autosave_fire(self) -> None:
        with self._autosave_lock:
            self._autosave_timer = None
        if not self._named_autosave_enabled():
            return
        try:
            session = self.current
        except RuntimeError:
            return
        name = session.name
        if name is None:
            return
        try:
            self.save_session(name)
        except Exception:
            pass

    def _resolve_default_target(self, provider: str, model: str) -> tuple[str, str]:
        """Validate or replace the configured default target for new sessions."""
        if self._default_target_resolver is None:
            return provider, model
        if not provider and not model:
            return "", ""

        resolved_provider, resolved_model = self._default_target_resolver(provider, model)
        resolved_provider = resolved_provider or ""
        resolved_model = resolved_model or ""

        if (resolved_provider, resolved_model) != (provider, model):
            self._config.set("provider.active_provider", resolved_provider)
            self._config.set("model.active_model", resolved_model)
            old_target = _format_target(provider, model)
            new_target = _format_target(resolved_provider, resolved_model)
            self._pending_default_target_warning = (
                f"Default target repaired: {old_target} was unavailable, so LocalAgentCLI "
                f"switched to {new_target}."
            )
        return resolved_provider, resolved_model


def _format_target(provider: str, model: str) -> str:
    """Render one provider/model pair for warning output."""
    if provider:
        return f"{provider} ({model or 'remote'})"
    return model or "(none)"
