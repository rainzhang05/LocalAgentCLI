"""StreamRenderer — renders streaming model output to the terminal."""

from __future__ import annotations

from collections import deque
from typing import Iterator

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from localagentcli.agents.events import (
    AgentEvent,
    PlanGenerated,
    PlanUpdated,
    ReasoningOutput,
    StepStarted,
    TaskComplete,
    TaskFailed,
    ToolCallRequested,
    ToolCallResult,
)
from localagentcli.models.backends.base import StreamChunk


class StreamRenderer:
    """Render streaming output, reasoning, and activity updates in real time."""

    def __init__(self, console: Console):
        self._console = console
        self._buffer = ""
        self._secondary_entries: deque[str] = deque(maxlen=8)
        self._rendered_secondary_count = 0
        self._primary_started = False
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
        has_pending_details = len(self._secondary_entries) > self._rendered_secondary_count
        if self._primary_started:
            self._console.print()
        self.flush_pending_details()
        if has_pending_details or not self._primary_started:
            self._console.print()
        self._primary_started = False

    def render_error(self, error: str) -> None:
        """Render a streaming error."""
        self._prepare_block_output()
        self.flush_pending_details()
        self._console.print(f"[red]{self._symbols['error']} {error}[/red]")

    def render_status(self, message: str) -> None:
        """Render a neutral status line."""
        self._prepare_block_output()
        self.flush_pending_details()
        self._console.print(f"{self._symbols['status']} {message}")

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
        """Flush any unrendered secondary detail entries."""
        if len(self._secondary_entries) <= self._rendered_secondary_count:
            return
        pending = list(self._secondary_entries)[self._rendered_secondary_count :]
        if not pending:
            return
        self._console.print(
            Panel(
                "\n".join(pending),
                title="Details",
                border_style="dim",
            )
        )
        self._rendered_secondary_count = len(self._secondary_entries)

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
        if isinstance(event, PlanGenerated):
            self.flush_pending_details()
            self._render_plan(event.plan, "Plan")
            return
        if isinstance(event, PlanUpdated):
            self.render_status(event.changes)
            self._render_plan(event.plan, "Plan")
            return
        if isinstance(event, StepStarted):
            self.render_status(f"Starting step {event.step.index}: {event.step.description}")
            return
        if isinstance(event, ToolCallRequested):
            marker = (
                self._symbols["warning"] if event.requires_approval else self._symbols["success"]
            )
            color = "yellow" if event.requires_approval else "green"
            suffix = " (HIGH RISK)" if event.risk_level == "high" else ""
            self.flush_pending_details()
            self._console.print(
                f"[{color}]{marker} {event.tool_name}: "
                f"{self._tool_summary(event.arguments)}{suffix}[/{color}]"
            )
            for warning in event.warnings:
                self.render_secondary(f"Warning: {warning}")
            return
        if isinstance(event, ToolCallResult):
            if event.result.status == "success":
                self.render_success(event.result.summary)
            elif event.result.status == "denied":
                self.render_warning(event.result.summary)
            else:
                self.render_error(event.result.summary)
            return
        if isinstance(event, ReasoningOutput):
            self.render_secondary(event.text)
            return
        if isinstance(event, TaskComplete):
            self.render_success("Task complete.")
            if event.summary.strip():
                self.flush_pending_details()
                self._console.print(event.summary)
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

    def _prepare_block_output(self) -> None:
        """Finish any inline primary output before rendering a block element."""
        if self._primary_started:
            self._console.print()
            self._primary_started = False

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
