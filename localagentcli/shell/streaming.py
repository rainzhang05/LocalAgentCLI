"""StreamRenderer — renders streaming model output to the terminal."""

from __future__ import annotations

import time
from collections import deque
from typing import Iterator

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

from localagentcli.agents.events import (
    AgentEvent,
    GuardianReviewCompleted,
    GuardianReviewStarted,
    PhaseChanged,
    PlanGenerated,
    PlanUpdated,
    ReasoningOutput,
    StepStarted,
    TaskComplete,
    TaskFailed,
    TaskRouted,
    TaskStopped,
    TaskTimedOut,
    ToolCallRequested,
    ToolCallResult,
)
from localagentcli.models.backends.base import StreamChunk
from localagentcli.shell.notifications import (
    NotificationDedupe,
    ShellNotification,
    format_notification,
)
from localagentcli.shell.themes import ShellTheme, resolve_shell_theme

# Coalesce consecutive neutral status lines before emitting (reduces panel reflow).
_DEFAULT_STATUS_BATCH_LIMIT = 12
_CATCHUP_STATUS_BATCH_LIMIT = 4
_CATCHUP_BACKLOG_HIGH_WATER = 10
_CATCHUP_BACKLOG_LOW_WATER = 4
_MIN_LINE_WIDTH = 8
_LIVE_DETAILS_FLUSH_INTERVAL_SEC = 0.08


class StreamRenderer:
    """Render streaming output, reasoning, and activity updates in real time."""

    def __init__(
        self,
        console: Console,
        *,
        persistent_details_lane: bool = False,
        theme: ShellTheme | None = None,
        notification_dedupe: bool = True,
        thinking_indicator_enabled: bool = True,
    ):
        self._console = console
        self._persistent_details_lane = persistent_details_lane
        self._theme = theme or resolve_shell_theme(None)
        self._notifications = NotificationDedupe(enabled=notification_dedupe)
        self._buffer = ""
        self._secondary_entries: deque[str] = deque(maxlen=8)
        self._rendered_secondary_count = 0
        self._primary_started = False
        self._thinking_enabled = thinking_indicator_enabled
        self._thinking_visible = False
        self._thinking_label = "Thinking"
        self._status_batch: list[str] = []
        self._catchup_mode = False
        self._status_batch_limit = _DEFAULT_STATUS_BATCH_LIMIT
        self._last_secondary_flush_at = 0.0
        self._stream_markdown_tail = ""
        self._stream_code_fence_open = False
        self._stream_code_language = "text"
        self._stream_code_buffer = ""
        self._symbols = {
            "error": _safe_symbol(console, "✗", "x"),
            "status": _safe_symbol(console, "ℹ", "i"),
            "success": _safe_symbol(console, "✓", "OK"),
            "warning": _safe_symbol(console, "⟳", "!"),
            "pending": _safe_symbol(console, "•", "*"),
            "in_progress": _safe_symbol(console, "→", ">"),
            "completed": _safe_symbol(console, "✓", "OK"),
            "failed": _safe_symbol(console, "✗", "x"),
            "skipped": _safe_symbol(console, "○", "o"),
        }

    def render_stream(self, chunks: Iterator[StreamChunk]) -> str:
        """Render all chunks to the terminal and return the full response text."""
        self._buffer = ""
        self._secondary_entries.clear()
        self._rendered_secondary_count = 0
        self._primary_started = False
        self._thinking_visible = False
        self._status_batch.clear()
        self._catchup_mode = False
        self._status_batch_limit = _DEFAULT_STATUS_BATCH_LIMIT
        self._last_secondary_flush_at = 0.0
        self._stream_markdown_tail = ""
        self._stream_code_fence_open = False
        self._stream_code_language = "text"
        self._stream_code_buffer = ""
        for chunk in chunks:
            self.render_chunk(chunk)
        return self._buffer

    def render_chunk(self, chunk: StreamChunk) -> None:
        """Render a single streaming chunk."""
        if chunk.is_done:
            self._finalize()
            return
        if chunk.kind == "final_text":
            self.stop_thinking_indicator()
            if not self._primary_started:
                self.flush_pending_details()
            self._render_final_text(chunk.text)
            self._buffer += chunk.text
            return
        if chunk.importance == "primary":
            detail = chunk.text or self._format_chunk_payload(chunk)
            if not detail:
                return
            if chunk.kind == "error":
                self.render_error(detail)
                return
            self.render_status(detail)
            return
        if chunk.kind in {"reasoning", "tool_call", "notification", "error"}:
            detail = chunk.text or self._format_chunk_payload(chunk)
            if detail:
                self._append_secondary(detail)
                self._flush_live_secondary()
            return

    def finalize(self) -> None:
        """Finalize any pending stream output between turns."""
        if self._primary_started:
            self._finalize()
            return
        if self._status_batch:
            self._finalize()
            return
        if len(self._secondary_entries) > self._rendered_secondary_count:
            self._finalize()

    def _finalize(self) -> None:
        """Called when streaming is complete."""
        self.stop_thinking_indicator()
        self._flush_stream_render_state()
        has_pending_tail = len(self._secondary_entries) > self._rendered_secondary_count or bool(
            self._status_batch
        )
        if self._primary_started:
            self._console.print()
        self.flush_pending_details()
        if has_pending_tail or not self._primary_started:
            self._console.print()
        self._primary_started = False

    def render_error(self, error: str) -> None:
        """Render a streaming error."""
        self._prepare_block_output()
        self.flush_pending_details()
        self._console.print(
            self._apply_style(f"{self._symbols['error']} {error}", self._theme.error_style)
        )

    def render_status(self, message: str) -> None:
        """Render a neutral status line (batched until the next flush boundary)."""
        self._status_batch.append(self._fit_single_line(message, reserve=2))
        self._adjust_status_pacing()
        if len(self._status_batch) >= self._status_batch_limit:
            self._prepare_block_output()
            self.flush_pending_details()

    def render_success(self, message: str) -> None:
        """Render a success status line."""
        self._prepare_block_output()
        self.flush_pending_details()
        self._console.print(
            self._apply_style(f"{self._symbols['success']} {message}", self._theme.success_style)
        )

    def render_warning(self, message: str) -> None:
        """Render a warning status line."""
        self._prepare_block_output()
        self.flush_pending_details()
        self._console.print(
            self._apply_style(f"{self._symbols['warning']} {message}", self._theme.warning_style)
        )

    def render_notification(self, notification: ShellNotification) -> None:
        """Render a structured shell notification with optional adjacent dedupe."""
        if not self._notifications.should_emit(notification):
            return
        message = format_notification(notification)
        if not message:
            return
        if notification.level == "success":
            self.render_success(message)
        elif notification.level == "warning":
            self.render_warning(message)
        elif notification.level == "error":
            self.render_error(message)
        else:
            self.render_status(message)

    def render_activity(self, message: str) -> None:
        """Backward-compatible alias for neutral status messages."""
        self.render_status(message)

    def render_secondary(self, detail: str) -> None:
        """Queue a secondary detail entry for the dimmed details lane."""
        self._append_secondary(detail)

    def flush_pending_details(self) -> None:
        """Emit queued secondary details and any batched neutral status lines."""
        pending_secondary = len(self._secondary_entries) > self._rendered_secondary_count
        show_persistent_secondary = self._persistent_details_lane and bool(self._secondary_entries)
        pending_status = bool(self._status_batch)
        if not pending_secondary and not pending_status and not show_persistent_secondary:
            return
        emitted = False
        if pending_secondary or show_persistent_secondary:
            if self._persistent_details_lane:
                lane_lines = list(self._secondary_entries)
            else:
                lane_lines = list(self._secondary_entries)[self._rendered_secondary_count :]
            if lane_lines:
                panel_width = self._available_width(reserve=6)
                if panel_width is not None:
                    lane_lines = [_truncate_with_ellipsis(line, panel_width) for line in lane_lines]
                details_body = "\n".join(lane_lines)
                if self._theme.details_text_style:
                    panel = Panel(
                        details_body,
                        title="Details",
                        border_style=self._theme.details_border_style,
                        style=self._theme.details_text_style,
                    )
                else:
                    panel = Panel(
                        details_body,
                        title="Details",
                        border_style=self._theme.details_border_style,
                    )
                self._console.print(panel)
                emitted = True
                if pending_secondary:
                    self._rendered_secondary_count = len(self._secondary_entries)
        if pending_status:
            lines = self._dedupe_consecutive_status(self._status_batch)
            body = "\n".join(f"{self._symbols['status']} {line}" for line in lines)
            self._console.print(self._apply_style(body, self._theme.status_style))
            self._status_batch.clear()
            self._adjust_status_pacing()
            emitted = True
        if emitted:
            self._last_secondary_flush_at = time.monotonic()

    def render_approval_prompt(self) -> None:
        """Render the inline approval prompt using the shared status grammar."""
        self._prepare_block_output()
        self.flush_pending_details()
        self._console.print(
            self._apply_style(
                f"{self._symbols['warning']} Approval required.",
                self._theme.warning_style,
            )
        )

    def render_preview(self, title: str, body: str) -> None:
        """Render a preview block without changing task semantics."""
        self._prepare_block_output()
        self.flush_pending_details()
        self._console.print(
            Panel(
                self._preview_renderable(body),
                title=title,
                border_style=self._theme.panel_border_style,
            )
        )

    def render_markdown_message(self, message: str) -> None:
        """Render plain text or markdown-rich message content."""
        self._prepare_block_output()
        self.flush_pending_details()
        if _looks_like_markdown(message):
            self._console.print(Markdown(message))
            return
        self._console.print(message)

    def start_thinking_indicator(self, *, label: str = "Thinking") -> None:
        """Prepare thinking indicator rendering for the next streaming turn."""
        if not self._thinking_enabled:
            return
        cleaned = label.strip()
        self._thinking_label = cleaned or "Thinking"

    def render_thinking_indicator(self, frame: str) -> None:
        """Render one transient thinking frame on a single terminal line."""
        if not self._thinking_enabled or self._primary_started:
            return
        payload = f"\r\033[2K{frame} {self._thinking_label}..."
        self._console.print(payload, end="", style=self._theme.dim_style or None, highlight=False)
        self._thinking_visible = True

    def stop_thinking_indicator(self) -> None:
        """Clear any active thinking indicator line."""
        if not self._thinking_visible:
            return
        self._console.print("\r\033[2K", end="", highlight=False)
        self._thinking_visible = False

    def render_agent_event(self, event: AgentEvent) -> None:
        """Render a structured agent event."""
        if isinstance(event, TaskRouted):
            self.render_status(f"Agent route: {_humanize_route(event.route)}.")
            return
        if isinstance(event, PhaseChanged):
            if event.phase in {
                "planning",
                "waiting_approval",
                "retrying",
                "replanning",
                "recovering",
            }:
                self.render_status(event.summary)
            elif event.phase == "failed":
                self.render_error(event.summary)
            elif event.phase in {"stopped", "timed_out"}:
                self.render_warning(event.summary)
            elif event.phase == "executing" and not event.step_index:
                self.render_status(event.summary)
            return
        if isinstance(event, PlanGenerated):
            self._prepare_block_output()
            self.flush_pending_details()
            self._render_plan(event.plan, "Plan")
            return
        if isinstance(event, PlanUpdated):
            self.render_status(event.changes)
            self._prepare_block_output()
            self.flush_pending_details()
            self._render_plan(event.plan, "Plan")
            return
        if isinstance(event, StepStarted):
            self.render_status(f"Step {event.step.index} started: {event.step.description}")
            return
        if isinstance(event, ToolCallRequested):
            marker = (
                self._symbols["warning"] if event.requires_approval else self._symbols["success"]
            )
            color = "yellow" if event.requires_approval else "green"
            suffix = " (HIGH RISK)" if event.risk_level == "high" else ""
            self._prepare_block_output()
            self.flush_pending_details()
            self._console.print(
                f"[{color}]{marker} {event.tool_name}: "
                f"{self._tool_summary(event.arguments)}{suffix}[/{color}]"
            )
            for warning in event.warnings:
                self.render_secondary(f"Warning: {warning}")
            if event.risk_reason:
                self.render_secondary(f"Risk: {event.risk_reason}")
            if event.rollback_summary:
                self.render_secondary(event.rollback_summary)
            return
        if isinstance(event, ToolCallResult):
            if event.result.status == "success":
                self.render_success(event.result.summary)
                if event.result.files_changed and event.rollback_entries:
                    self.render_status(
                        f"Undo available: {event.rollback_entries} change(s). Use /agent undo."
                    )
                    self.flush_pending_details()
            elif event.result.status == "denied":
                self.render_warning(event.result.summary)
            else:
                self.render_error(event.result.summary)
            return
        if isinstance(event, GuardianReviewStarted):
            self.render_status(f"Guardian reviewing: {event.action_summary or event.tool_name}.")
            return
        if isinstance(event, GuardianReviewCompleted):
            if event.approved:
                self.render_status(
                    "Guardian approved "
                    f"{event.tool_name} ({event.risk_level} {event.risk_score}/100)."
                )
            else:
                self.render_warning(
                    "Guardian denied "
                    f"{event.tool_name} ({event.risk_level} {event.risk_score}/100)."
                )
            if event.rationale:
                self.render_secondary(f"Guardian rationale: {event.rationale}")
            if event.failure:
                self.render_secondary(f"Guardian failure: {event.failure}")
            return
        if isinstance(event, ReasoningOutput):
            self.render_secondary(event.text)
            return
        if isinstance(event, TaskComplete):
            self.render_success("Task completed.")
            if event.summary.strip():
                self.render_markdown_message(event.summary)
            return
        if isinstance(event, TaskStopped):
            self.render_warning(event.reason)
            return
        if isinstance(event, TaskTimedOut):
            self.render_warning(event.reason)
            return
        if isinstance(event, TaskFailed):
            self.render_error(event.reason)

    def _render_plan(self, plan, title: str) -> None:
        lines = []
        for step in plan.steps:
            marker = self._symbols.get(step.status, self._symbols["pending"])
            line = self._fit_single_line(f"{step.index}. {marker} {step.description}", reserve=6)
            if step.result and step.status in {"completed", "failed"}:
                result_line = self._fit_single_line(step.result, reserve=9)
                line = f"{line}\n   {result_line}"
            lines.append(line)
        body = "\n".join(lines) if lines else "(no steps)"
        self._console.print(
            Panel(
                Text(body),
                title=title,
                border_style=self._theme.panel_border_style,
            )
        )

    def _tool_summary(self, arguments: dict) -> str:
        if not arguments:
            return "(no arguments)"
        parts = [f"{key}={value!r}" for key, value in arguments.items()]
        return ", ".join(parts[:3])

    def _append_secondary(self, detail: str) -> None:
        """Append a dimmed secondary entry while keeping only a rolling window."""
        panel_width = self._available_width(reserve=6)
        for line in detail.splitlines() or [detail]:
            cleaned = line.strip()
            if cleaned:
                if panel_width is not None:
                    cleaned = _truncate_with_ellipsis(cleaned, panel_width)
                self._secondary_entries.append(cleaned)
        self._adjust_status_pacing()

    def _flush_live_secondary(self) -> None:
        """Flush pending secondary detail on a throttled cadence while streaming."""
        if len(self._secondary_entries) <= self._rendered_secondary_count:
            return
        now = time.monotonic()
        if (now - self._last_secondary_flush_at) < _LIVE_DETAILS_FLUSH_INTERVAL_SEC:
            return
        self._prepare_block_output()
        self.flush_pending_details()

    def _available_width(self, *, reserve: int = 0) -> int | None:
        width = _console_width(self._console)
        if width is None:
            return None
        return max(width - max(reserve, 0), _MIN_LINE_WIDTH)

    def _fit_single_line(self, text: str, *, reserve: int = 0) -> str:
        width = self._available_width(reserve=reserve)
        if width is None:
            return text
        return _truncate_with_ellipsis(text, width)

    def flush_agent_event_tail(self) -> None:
        """Emit batched status lines and queued secondary after an agent event pass."""
        self._prepare_block_output()
        self.flush_pending_details()

    @staticmethod
    def _dedupe_consecutive_status(messages: list[str]) -> list[str]:
        out: list[str] = []
        for msg in messages:
            if not out or out[-1] != msg:
                out.append(msg)
        return out

    def _prepare_block_output(self) -> None:
        """Finish any inline primary output before rendering a block element."""
        self.stop_thinking_indicator()
        if self._primary_started:
            self._console.print()
            self._primary_started = False

    def _render_final_text(self, text: str) -> None:
        """Render streaming final text, syntax-highlighting fenced code blocks."""
        if not text:
            return

        content = self._stream_markdown_tail + text
        self._stream_markdown_tail = ""

        while content:
            if not self._stream_code_fence_open:
                fence_index = content.find("```")
                if fence_index == -1:
                    plain, tail = _split_trailing_backticks(content, max_tail=2)
                    self._render_inline_text(plain)
                    self._stream_markdown_tail = tail
                    return

                prefix = content[:fence_index]
                self._render_inline_text(prefix)
                content = content[fence_index + 3 :]

                newline_index = content.find("\n")
                if newline_index == -1:
                    self._stream_markdown_tail = "```" + content
                    return

                language_line = content[:newline_index].strip()
                self._stream_code_language = language_line.split()[0] if language_line else "text"
                self._stream_code_fence_open = True
                self._stream_code_buffer = ""
                content = content[newline_index + 1 :]
                continue

            fence_index = content.find("```")
            if fence_index == -1:
                code_segment, tail = _split_trailing_backticks(content, max_tail=2)
                self._stream_code_buffer += code_segment
                self._stream_markdown_tail = tail
                return

            self._stream_code_buffer += content[:fence_index]
            self._render_code_block(self._stream_code_buffer, self._stream_code_language)
            self._stream_code_buffer = ""
            self._stream_code_fence_open = False
            self._stream_code_language = "text"
            content = content[fence_index + 3 :]
            if content.startswith("\n"):
                content = content[1:]

    def _render_inline_text(self, text: str) -> None:
        """Render inline stream text without changing markdown/code state."""
        if not text:
            return
        self._console.print(text, end="", highlight=False)
        self._primary_started = True

    def _render_code_block(self, code: str, language: str) -> None:
        """Render one fenced code block using rich syntax highlighting."""
        if not code.strip():
            return
        self._prepare_block_output()
        self._console.print(
            Syntax(
                code.rstrip("\n"),
                language or "text",
                line_numbers=False,
                word_wrap=True,
            )
        )

    def _flush_stream_render_state(self) -> None:
        """Flush deferred markdown/code stream state at stream completion."""
        if self._stream_code_fence_open:
            self._stream_code_buffer += self._stream_markdown_tail
            self._stream_markdown_tail = ""
            if self._stream_code_buffer:
                self._render_code_block(self._stream_code_buffer, self._stream_code_language)
            self._stream_code_buffer = ""
            self._stream_code_fence_open = False
            self._stream_code_language = "text"

        if self._stream_markdown_tail:
            self._render_inline_text(self._stream_markdown_tail)
            self._stream_markdown_tail = ""

    def _preview_renderable(self, body: str):
        """Select a rich renderable for preview bodies."""
        if _looks_like_markdown(body):
            return Markdown(body)
        return Text(body)

    @staticmethod
    def _apply_style(message: str, style: str) -> str:
        if not style or style == "default":
            return message
        return f"[{style}]{message}[/{style}]"

    def _adjust_status_pacing(self) -> None:
        """Adapt status batching to backlog with simple high/low-water hysteresis."""
        pending_secondary = len(self._secondary_entries) - self._rendered_secondary_count
        backlog = max(pending_secondary, 0) + len(self._status_batch)
        if not self._catchup_mode and backlog >= _CATCHUP_BACKLOG_HIGH_WATER:
            self._catchup_mode = True
            self._status_batch_limit = _CATCHUP_STATUS_BATCH_LIMIT
            return
        if self._catchup_mode and backlog <= _CATCHUP_BACKLOG_LOW_WATER:
            self._catchup_mode = False
            self._status_batch_limit = _DEFAULT_STATUS_BATCH_LIMIT

    def _format_chunk_payload(self, chunk: StreamChunk) -> str:
        """Render payload-only chunks into human-readable detail lines."""
        payload = chunk.payload or chunk.tool_call_data or {}
        if not isinstance(payload, dict):
            return ""
        if chunk.kind == "tool_call":
            function = payload.get("function", payload)
            if isinstance(function, dict):
                tool_name = function.get("name", payload.get("name", "tool"))
                return f"Tool call: {tool_name}"
        source = payload.get("source")
        if isinstance(source, str) and source:
            return f"{source}: {chunk.text}".strip(": ")
        return ""


def _safe_symbol(console: Console, preferred: str, fallback: str) -> str:
    """Choose a glyph that the current console encoding can represent."""
    encoding = _console_encoding(console)
    if encoding is None:
        return preferred
    try:
        preferred.encode(encoding)
    except (LookupError, UnicodeEncodeError):
        return fallback
    return preferred


def _console_encoding(console: Console) -> str | None:
    """Best-effort lookup of the console output encoding."""
    file = getattr(console, "file", None)
    encoding = getattr(file, "encoding", None)
    if isinstance(encoding, str) and encoding:
        return encoding
    encoding = getattr(console, "encoding", None)
    if isinstance(encoding, str) and encoding:
        return encoding
    return None


def _console_width(console: Console) -> int | None:
    """Best-effort lookup of terminal width for narrow-layout safeguards."""
    width = getattr(console, "width", None)
    if isinstance(width, int) and width > 0:
        return width
    size = getattr(console, "size", None)
    if size is not None:
        columns = getattr(size, "width", None)
        if isinstance(columns, int) and columns > 0:
            return columns
    return None


def _truncate_with_ellipsis(text: str, max_width: int) -> str:
    """Truncate one line to a fixed width, preserving readability."""
    if max_width <= 0:
        return ""
    if len(text) <= max_width:
        return text
    if max_width <= 2:
        return text[:max_width]
    return f"{text[: max_width - 1]}…"


def _split_trailing_backticks(text: str, *, max_tail: int) -> tuple[str, str]:
    """Hold trailing 1-2 backticks so split fences can be detected across chunks."""
    if not text:
        return "", ""
    count = 0
    for char in reversed(text):
        if char != "`" or count >= max_tail:
            break
        count += 1
    if count == 0:
        return text, ""
    return text[:-count], text[-count:]


def _looks_like_markdown(text: str) -> bool:
    """Best-effort markdown signal detection for rich rendering paths."""
    stripped = text.strip()
    if not stripped:
        return False
    markers = (
        "```",
        "# ",
        "## ",
        "### ",
        "- ",
        "* ",
        "1. ",
        "> ",
        "| ",
    )
    if any(marker in stripped for marker in markers):
        return True
    return "`" in stripped and stripped.count("`") >= 2


def _humanize_route(route: str) -> str:
    mapping = {
        "direct_answer": "direct answer",
        "single_step_task": "single-step task",
        "multi_step_task": "multi-step task",
    }
    return mapping.get(route, route.replace("_", " "))
