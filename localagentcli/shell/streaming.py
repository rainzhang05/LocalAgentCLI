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
        self._secondary_rendered = False

    def render_stream(self, chunks: Iterator[StreamChunk]) -> str:
        """Render all chunks to the terminal and return the full response text."""
        self._buffer = ""
        self._secondary_entries.clear()
        self._secondary_rendered = False
        for chunk in chunks:
            self.render_chunk(chunk)
        return self._buffer

    def render_chunk(self, chunk: StreamChunk) -> None:
        """Render a single streaming chunk."""
        if chunk.is_done:
            self._finalize()
            return
        if chunk.kind == "final_text":
            self._render_secondary_panel()
            self._console.print(chunk.text, end="", highlight=False)
            self._buffer += chunk.text
            return
        if chunk.importance == "primary":
            detail = chunk.text or self._format_chunk_payload(chunk)
            if not detail:
                return
            if chunk.kind == "error":
                self.render_error(detail)
                return
            self.render_activity(detail)
            return
        if chunk.kind in {"reasoning", "tool_call", "notification", "error"}:
            detail = chunk.text or self._format_chunk_payload(chunk)
            if detail:
                self._append_secondary(detail)
            return

    def _finalize(self) -> None:
        """Called when streaming is complete."""
        self._render_secondary_panel()
        self._console.print()

    def render_error(self, error: str) -> None:
        """Render a streaming error."""
        self._console.print(f"\n[red]Error: {error}[/red]")

    def render_activity(self, message: str) -> None:
        """Render an inline activity log entry."""
        self._console.print(f"ℹ {message}")

    def render_agent_event(self, event: AgentEvent) -> None:
        """Render a structured agent event."""
        if isinstance(event, PlanGenerated):
            self._render_plan(event.plan, "Plan")
            return
        if isinstance(event, PlanUpdated):
            self.render_activity(event.changes)
            self._render_plan(event.plan, "Plan")
            return
        if isinstance(event, StepStarted):
            self.render_activity(f"Starting step {event.step.index}: {event.step.description}")
            return
        if isinstance(event, ToolCallRequested):
            marker = "⟳" if event.requires_approval else "✓"
            color = "yellow" if event.requires_approval else "green"
            suffix = " (HIGH RISK)" if event.risk_level == "high" else ""
            self._console.print(
                f"[{color}]{marker} {event.tool_name}: "
                f"{self._tool_summary(event.arguments)}{suffix}[/{color}]"
            )
            for warning in event.warnings:
                self._console.print(f"[yellow]  ! {warning}[/yellow]")
            return
        if isinstance(event, ToolCallResult):
            marker = "✓" if event.result.status == "success" else "✗"
            color = "green" if event.result.status == "success" else "red"
            self._console.print(f"[{color}]{marker} {event.result.summary}[/{color}]")
            return
        if isinstance(event, ReasoningOutput):
            self._console.print(
                Panel(
                    event.text,
                    title="Reasoning",
                    border_style="dim",
                )
            )
            return
        if isinstance(event, TaskComplete):
            self.render_activity("Task complete.")
            if event.summary.strip():
                self._console.print(event.summary)
            return
        if isinstance(event, TaskFailed):
            self.render_error(event.reason)

    def _render_plan(self, plan, title: str) -> None:
        lines = []
        markers = {
            "pending": "•",
            "in_progress": "→",
            "completed": "✓",
            "failed": "✗",
            "skipped": "○",
        }
        for step in plan.steps:
            marker = markers.get(step.status, "•")
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

    def _render_secondary_panel(self) -> None:
        """Render the latest secondary output once, above the main response."""
        if self._secondary_rendered or not self._secondary_entries:
            return
        self._console.print(
            Panel(
                "\n".join(self._secondary_entries),
                title="Details",
                border_style="dim",
            )
        )
        self._secondary_rendered = True
