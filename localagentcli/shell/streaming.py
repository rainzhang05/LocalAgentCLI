"""StreamRenderer — renders streaming model output to the terminal."""

from __future__ import annotations

from typing import Iterator

from rich.console import Console

from localagentcli.models.backends.base import StreamChunk


class StreamRenderer:
    """Renders StreamChunk objects to the terminal in real time.

    Phase 2: minimal inline text printing.
    Phase 4 will add markdown rendering, reasoning panels, etc.
    """

    def __init__(self, console: Console):
        self._console = console
        self._buffer = ""
        self._reasoning_buffer = ""

    def render_stream(self, chunks: Iterator[StreamChunk]) -> str:
        """Render all chunks to the terminal and return the full response text."""
        self._buffer = ""
        self._reasoning_buffer = ""
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
            self._console.print(f"[dim]{chunk.text}[/dim]", end="")
            self._reasoning_buffer += chunk.text
            return
        self._console.print(chunk.text, end="", highlight=False)
        self._buffer += chunk.text

    def _finalize(self) -> None:
        """Called when streaming is complete."""
        self._console.print()

    def render_error(self, error: str) -> None:
        """Render a streaming error."""
        self._console.print(f"\n[red]Error: {error}[/red]")
