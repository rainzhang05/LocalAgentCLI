"""StreamRenderer — renders streaming model output to the terminal."""

from __future__ import annotations

from collections import deque
from typing import Iterator

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from localagentcli.agents.events import (
    AgentEvent,
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

# Coalesce consecutive neutral status lines before emitting (reduces panel reflow).
_DEFAULT_STATUS_BATCH_LIMIT = 12
_CATCHUP_STATUS_BATCH_LIMIT = 4
_CATCHUP_BACKLOG_HIGH_WATER = 10
_CATCHUP_BACKLOG_LOW_WATER = 4


class StreamRenderer:
    """Render streaming output, reasoning, and activity updates in real time."""

    def __init__(self, console: Console, *, persistent_details_lane: bool = False):
        self._console = console
        self._persistent_details_lane = persistent_details_lane
        self._buffer = ""
        self._secondary_entries: deque[str] = deque(maxlen=8)
        self._rendered_secondary_count = 0
        self._primary_started = False
        self._status_batch: list[str] = []
        self._catchup_mode = False
        self._status_batch_limit = _DEFAULT_STATUS_BATCH_LIMIT
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
        self._status_batch.clear()
        self._catchup_mode = False
        self._status_batch_limit = _DEFAULT_STATUS_BATCH_LIMIT
        for chunk in chunks:
            self.render_chunk(chunk)
        return self._buffer

    def render_chunk(self, chunk: StreamChunk) -> None:
        """Render a single streaming chunk."""
        if chunk.is_done:
            self._finalize()
            return
        if chunk.kind == "final_text":
            if not self._primary_started:
                self.flush_pending_details()
            self._console.print(chunk.text, end="", highlight=False)
            self._buffer += chunk.text
            self._primary_started = True
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
            return

    def _finalize(self) -> None:
        """Called when streaming is complete."""
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
        self._console.print(f"[red]{self._symbols['error']} {error}[/red]")

    def render_status(self, message: str) -> None:
        """Render a neutral status line (batched until the next flush boundary)."""
        self._status_batch.append(message)
        self._adjust_status_pacing()
        if len(self._status_batch) >= self._status_batch_limit:
            self._prepare_block_output()
            self.flush_pending_details()

    def render_success(self, message: str) -> None:
        """Render a success status line."""
        self._prepare_block_output()
        self.flush_pending_details()
        self._console.print(f"[green]{self._symbols['success']} {message}[/green]")

    def render_warning(self, message: str) -> None:
        """Render a warning status line."""
        self._prepare_block_output()
        self.flush_pending_details()
        self._console.print(f"[yellow]{self._symbols['warning']} {message}[/yellow]")

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
        if pending_secondary or show_persistent_secondary:
            if self._persistent_details_lane:
                lane_lines = list(self._secondary_entries)
            else:
                lane_lines = list(self._secondary_entries)[self._rendered_secondary_count :]
            if lane_lines:
                self._console.print(
                    Panel(
                        "\n".join(lane_lines),
                        title="Details",
                        border_style="dim",
                    )
                )
                if pending_secondary:
                    self._rendered_secondary_count = len(self._secondary_entries)
        if pending_status:
            lines = self._dedupe_consecutive_status(self._status_batch)
            body = "\n".join(f"{self._symbols['status']} {line}" for line in lines)
            self._console.print(body)
            self._status_batch.clear()
            self._adjust_status_pacing()

    def render_approval_prompt(self) -> None:
        """Render the inline approval prompt using the shared status grammar."""
        self._prepare_block_output()
        self.flush_pending_details()
        self._console.print(f"[yellow]{self._symbols['warning']} Approval required.[/yellow]")

    def render_preview(self, title: str, body: str) -> None:
        """Render a preview block without changing task semantics."""
        self._prepare_block_output()
        self.flush_pending_details()
        self._console.print(Panel(body, title=title, border_style="yellow"))

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
        if isinstance(event, ReasoningOutput):
            self.render_secondary(event.text)
            return
        if isinstance(event, TaskComplete):
            self.render_success("Task completed.")
            if event.summary.strip():
                self.flush_pending_details()
                self._console.print(event.summary)
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
            line = f"{step.index}. {marker} {step.description}"
            if step.result and step.status in {"completed", "failed"}:
                line = f"{line}\n   {step.result}"
            lines.append(line)
        body = "\n".join(lines) if lines else "(no steps)"
        self._console.print(Panel(Text(body), title=title, border_style="cyan"))

    def _tool_summary(self, arguments: dict) -> str:
        if not arguments:
            return "(no arguments)"
        parts = [f"{key}={value!r}" for key, value in arguments.items()]
        return ", ".join(parts[:3])

    def _append_secondary(self, detail: str) -> None:
        """Append a dimmed secondary entry while keeping only a rolling window."""
        for line in detail.splitlines() or [detail]:
            cleaned = line.strip()
            if cleaned:
                self._secondary_entries.append(cleaned)
        self._adjust_status_pacing()

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
        if self._primary_started:
            self._console.print()
            self._primary_started = False

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


def _humanize_route(route: str) -> str:
    mapping = {
        "direct_answer": "direct answer",
        "single_step_task": "single-step task",
        "multi_step_task": "multi-step task",
    }
    return mapping.get(route, route.replace("_", " "))
