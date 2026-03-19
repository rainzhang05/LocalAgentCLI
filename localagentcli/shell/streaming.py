"""StreamRenderer — renders streaming model output to the terminal."""

from __future__ import annotations

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
        self._reasoning_buffer = ""
        self._reasoning_rendered = False

    def render_stream(self, chunks: Iterator[StreamChunk]) -> str:
        """Render all chunks to the terminal and return the full response text."""
        self._buffer = ""
        self._reasoning_buffer = ""
        self._reasoning_rendered = False
        for chunk in chunks:
            self.render_chunk(chunk)
        return self._buffer

    def render_chunk(self, chunk: StreamChunk) -> None:
        """Render a single streaming chunk."""
        if chunk.is_done:
            self._finalize()
            return
        if chunk.is_tool_call:
            return
        if chunk.is_reasoning:
            self._reasoning_buffer += chunk.text
            return
        self._render_reasoning_panel()
        self._console.print(chunk.text, end="", highlight=False)
        self._buffer += chunk.text

    def _finalize(self) -> None:
        """Called when streaming is complete."""
        self._render_reasoning_panel()
        self._console.print()

    def render_error(self, error: str) -> None:
        """Render a streaming error."""
        self._console.print(f"\n[red]Error: {error}[/red]")

    def render_activity(self, message: str) -> None:
        """Render an inline activity log entry."""
        self._console.print(f"[blue]ℹ {message}[/blue]")

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
            self._console.print(
                f"[{color}]{marker} {event.tool_name}: "
                f"{self._tool_summary(event.arguments)}[/{color}]"
            )
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

    def _render_reasoning_panel(self) -> None:
        """Render the buffered reasoning once, above the response."""
        if self._reasoning_rendered or not self._reasoning_buffer.strip():
            return
        self._console.print(
            Panel(
                self._reasoning_buffer.strip(),
                title="Reasoning",
                border_style="dim",
            )
        )
        self._reasoning_rendered = True
