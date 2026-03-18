"""Tests for StreamRenderer — streaming model output to terminal."""

from __future__ import annotations

from unittest.mock import MagicMock

from localagentcli.models.backends.base import StreamChunk
from localagentcli.shell.streaming import StreamRenderer


class TestStreamRendererRenderChunk:
    def test_text_chunk(self):
        console = MagicMock()
        renderer = StreamRenderer(console)
        renderer.render_chunk(StreamChunk(text="Hello"))
        console.print.assert_called_once_with("Hello", end="", highlight=False)

    def test_reasoning_chunk(self):
        console = MagicMock()
        renderer = StreamRenderer(console)
        renderer.render_chunk(StreamChunk(text="thinking...", is_reasoning=True))
        console.print.assert_called_once_with("[dim]thinking...[/dim]", end="")

    def test_done_chunk_prints_newline(self):
        console = MagicMock()
        renderer = StreamRenderer(console)
        renderer.render_chunk(StreamChunk(is_done=True))
        console.print.assert_called_once_with()

    def test_tool_call_chunk_ignored(self):
        console = MagicMock()
        renderer = StreamRenderer(console)
        renderer.render_chunk(StreamChunk(is_tool_call=True, tool_call_data={"name": "test"}))
        console.print.assert_not_called()

    def test_text_accumulates_in_buffer(self):
        console = MagicMock()
        renderer = StreamRenderer(console)
        renderer.render_chunk(StreamChunk(text="Hello "))
        renderer.render_chunk(StreamChunk(text="World"))
        assert renderer._buffer == "Hello World"

    def test_reasoning_accumulates_separately(self):
        console = MagicMock()
        renderer = StreamRenderer(console)
        renderer.render_chunk(StreamChunk(text="think", is_reasoning=True))
        assert renderer._reasoning_buffer == "think"
        assert renderer._buffer == ""


class TestStreamRendererRenderStream:
    def test_returns_full_text(self):
        console = MagicMock()
        renderer = StreamRenderer(console)
        chunks = [
            StreamChunk(text="Hello "),
            StreamChunk(text="World"),
            StreamChunk(is_done=True),
        ]
        result = renderer.render_stream(iter(chunks))
        assert result == "Hello World"

    def test_empty_stream(self):
        console = MagicMock()
        renderer = StreamRenderer(console)
        result = renderer.render_stream(iter([StreamChunk(is_done=True)]))
        assert result == ""

    def test_resets_buffer_on_new_stream(self):
        console = MagicMock()
        renderer = StreamRenderer(console)
        renderer._buffer = "old"
        renderer.render_stream(iter([StreamChunk(text="new"), StreamChunk(is_done=True)]))
        assert renderer._buffer == "new"


class TestStreamRendererRenderError:
    def test_render_error(self):
        console = MagicMock()
        renderer = StreamRenderer(console)
        renderer.render_error("Something went wrong")
        console.print.assert_called_once_with("\n[red]Error: Something went wrong[/red]")
