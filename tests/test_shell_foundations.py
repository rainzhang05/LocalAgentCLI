"""Unit tests for shell animation/theme/notification foundations."""

from __future__ import annotations

from localagentcli.shell.animation import ThinkingAnimationConfig, ThinkingAnimator
from localagentcli.shell.notifications import (
    NotificationDedupe,
    ShellNotification,
    format_notification,
)
from localagentcli.shell.themes import available_shell_themes, resolve_shell_theme


class TestThinkingAnimationConfig:
    def test_normalizes_unknown_style(self):
        config = ThinkingAnimationConfig(style="unknown")
        assert config.normalized_style() == "dots"

    def test_normalizes_interval_floor(self):
        config = ThinkingAnimationConfig(interval_ms=5)
        assert config.normalized_interval_ms() == 40


class TestThinkingAnimator:
    def test_cycles_frames(self):
        animator = ThinkingAnimator(style="line", interval_ms=80)
        first = animator.next_frame()
        second = animator.next_frame()
        assert first != second

    def test_ascii_fallback(self):
        animator = ThinkingAnimator(style="dots", prefer_ascii=True)
        assert animator.current_frame() == "."

    def test_available_styles(self):
        styles = ThinkingAnimator.available_styles()
        assert "dots" in styles
        assert "line" in styles
        assert "pulse" in styles


class TestShellThemes:
    def test_available_shell_themes(self):
        themes = available_shell_themes()
        assert "default" in themes
        assert "high-contrast" in themes
        assert "mono" in themes

    def test_unknown_theme_falls_back_to_default(self):
        resolved = resolve_shell_theme("not-a-theme")
        assert resolved.name == "default"

    def test_default_theme_uses_turquoise_accent_tokens(self):
        resolved = resolve_shell_theme("default")
        assert resolved.banner_style == "bold #40E0D0"
        assert resolved.panel_border_style == "#40E0D0"
        assert resolved.details_border_style == "#40E0D0"


class TestNotifications:
    def test_adjacent_dedupe_blocks_duplicates(self):
        dedupe = NotificationDedupe(enabled=True)
        note = ShellNotification(level="status", message="Working")
        assert dedupe.should_emit(note) is True
        assert dedupe.should_emit(note) is False

    def test_dedupe_disabled_allows_duplicates(self):
        dedupe = NotificationDedupe(enabled=False)
        note = ShellNotification(level="warning", message="Heads up")
        assert dedupe.should_emit(note) is True
        assert dedupe.should_emit(note) is True

    def test_format_notification_with_source_and_hint(self):
        note = ShellNotification(
            level="error",
            message="Failed request",
            source="runtime",
            hint="retry soon",
        )
        assert format_notification(note) == "runtime: Failed request (retry soon)"
