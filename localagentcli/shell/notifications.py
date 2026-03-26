"""Structured shell notification helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

NotificationLevel = Literal["status", "success", "warning", "error"]


@dataclass(frozen=True)
class ShellNotification:
    """A renderable shell notification."""

    level: NotificationLevel
    message: str
    source: str = ""
    hint: str = ""


class NotificationDedupe:
    """Simple adjacent-notification dedupe for noisy runtime streams."""

    def __init__(self, *, enabled: bool = True):
        self._enabled = enabled
        self._last_key: tuple[str, str, str, str] | None = None

    def should_emit(self, notification: ShellNotification) -> bool:
        if not self._enabled:
            return True
        key = (
            notification.level,
            notification.message,
            notification.source,
            notification.hint,
        )
        if self._last_key == key:
            return False
        self._last_key = key
        return True


def format_notification(notification: ShellNotification) -> str:
    """Render one notification body with optional source/hint context."""
    parts: list[str] = []
    if notification.source:
        parts.append(f"{notification.source}: ")
    parts.append(notification.message)
    if notification.hint:
        parts.append(f" ({notification.hint})")
    return "".join(parts).strip()
