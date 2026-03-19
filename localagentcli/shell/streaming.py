"""StreamRenderer — renders streaming model output to the terminal."""

from __future__ import annotations

from typing import Iterator

from rich.console import Console
from rich.panel import Panel

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
