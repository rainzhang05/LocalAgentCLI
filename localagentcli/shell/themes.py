"""Theme tokens for shell rendering surfaces."""

from __future__ import annotations

from dataclasses import dataclass

_DEFAULT_THEME = "default"


@dataclass(frozen=True)
class ShellTheme:
    """Named style tokens consumed by stream/shell renderers."""

    name: str
    banner_style: str
    status_style: str
    success_style: str
    warning_style: str
    error_style: str
    details_border_style: str
    details_text_style: str
    panel_border_style: str
    dim_style: str


_THEMES: dict[str, ShellTheme] = {
    "default": ShellTheme(
        name="default",
        banner_style="bold #40E0D0",
        status_style="default",
        success_style="green",
        warning_style="yellow",
        error_style="red",
        details_border_style="#40E0D0",
        details_text_style="dim",
        panel_border_style="#40E0D0",
        dim_style="dim",
    ),
    "high-contrast": ShellTheme(
        name="high-contrast",
        banner_style="bold bright_cyan",
        status_style="bold white",
        success_style="bold bright_green",
        warning_style="bold bright_yellow",
        error_style="bold bright_red",
        details_border_style="bright_black",
        details_text_style="bright_white",
        panel_border_style="bright_cyan",
        dim_style="bright_black",
    ),
    "mono": ShellTheme(
        name="mono",
        banner_style="bold",
        status_style="",
        success_style="",
        warning_style="",
        error_style="bold",
        details_border_style="",
        details_text_style="",
        panel_border_style="",
        dim_style="",
    ),
}


def available_shell_themes() -> tuple[str, ...]:
    """Return supported shell theme names."""
    return tuple(sorted(_THEMES))


def resolve_shell_theme(name: str | None) -> ShellTheme:
    """Resolve a configured shell theme with safe fallback."""
    if not name:
        return _THEMES[_DEFAULT_THEME]
    key = name.strip().lower()
    return _THEMES.get(key, _THEMES[_DEFAULT_THEME])
