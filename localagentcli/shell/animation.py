"""Animation helpers for shell thinking/streaming indicators."""

from __future__ import annotations

from dataclasses import dataclass

_DEFAULT_STYLE = "dots"
_DEFAULT_INTERVAL_MS = 120
_MIN_INTERVAL_MS = 40

_UNICODE_FRAMES: dict[str, tuple[str, ...]] = {
    "dots": ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"),
    "line": ("|", "/", "-", "\\"),
    "pulse": ("▁", "▂", "▃", "▄", "▅", "▆", "▇", "█", "▇", "▆", "▅", "▄", "▃", "▂"),
}

_ASCII_FRAMES: dict[str, tuple[str, ...]] = {
    "dots": (".", "..", "...", "....", "...", ".."),
    "line": ("|", "/", "-", "\\"),
    "pulse": (".", "o", "O", "@", "O", "o"),
}


@dataclass(frozen=True)
class ThinkingAnimationConfig:
    """Configuration for the shell thinking indicator animation."""

    enabled: bool = True
    style: str = _DEFAULT_STYLE
    interval_ms: int = _DEFAULT_INTERVAL_MS

    def normalized_style(self) -> str:
        style = self.style.strip().lower()
        if style in _UNICODE_FRAMES:
            return style
        return _DEFAULT_STYLE

    def normalized_interval_ms(self) -> int:
        return max(_MIN_INTERVAL_MS, int(self.interval_ms))


class ThinkingAnimator:
    """Stateful frame cycler used by the ShellUI heartbeat loop."""

    def __init__(
        self,
        *,
        style: str = _DEFAULT_STYLE,
        interval_ms: int = _DEFAULT_INTERVAL_MS,
        prefer_ascii: bool = False,
    ):
        self._style = style.strip().lower() if style else _DEFAULT_STYLE
        self._interval_ms = max(_MIN_INTERVAL_MS, int(interval_ms))
        self._prefer_ascii = prefer_ascii
        self._frames = self._resolve_frames(self._style, prefer_ascii)
        self._index = 0

    @property
    def interval_seconds(self) -> float:
        """Heartbeat interval in seconds."""
        return self._interval_ms / 1000.0

    @property
    def style(self) -> str:
        return self._style

    def reset(self) -> None:
        self._index = 0

    def current_frame(self) -> str:
        return self._frames[self._index]

    def next_frame(self) -> str:
        frame = self.current_frame()
        self._index = (self._index + 1) % len(self._frames)
        return frame

    @staticmethod
    def available_styles() -> tuple[str, ...]:
        return tuple(sorted(_UNICODE_FRAMES))

    @staticmethod
    def _resolve_frames(style: str, prefer_ascii: bool) -> tuple[str, ...]:
        normalized = style if style in _UNICODE_FRAMES else _DEFAULT_STYLE
        if prefer_ascii:
            return _ASCII_FRAMES.get(normalized, _ASCII_FRAMES[_DEFAULT_STYLE])
        return _UNICODE_FRAMES.get(normalized, _UNICODE_FRAMES[_DEFAULT_STYLE])
