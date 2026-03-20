"""Tests for StreamRenderer — streaming model output to terminal."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from rich.panel import Panel

from localagentcli.agents.events import (
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
from localagentcli.agents.planner import PlanStep, TaskPlan
from localagentcli.models.backends.base import StreamChunk
from localagentcli.shell.streaming import StreamRenderer
from localagentcli.tools.base import ToolResult


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
        console.print.assert_not_called()
        assert list(renderer._secondary_entries) == ["thinking..."]

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
        assert list(renderer._secondary_entries) == ["Tool call: test"]

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
        assert list(renderer._secondary_entries) == ["think"]
        assert renderer._buffer == ""

    def test_primary_notification_renders_activity(self):
        console = MagicMock()
        renderer = StreamRenderer(console)
        renderer.render_chunk(
            StreamChunk(
                text="[WARNING] Near memory limit", kind="notification", importance="primary"
            )
        )

        console.print.assert_called_once_with("ℹ [WARNING] Near memory limit")
        assert list(renderer._secondary_entries) == []


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

    def test_renders_secondary_panel_before_text(self):
        console = MagicMock()
        renderer = StreamRenderer(console)

        renderer.render_stream(
            iter(
                [
                    StreamChunk(text="thinking...", is_reasoning=True),
                    StreamChunk(text="Hello"),
                    StreamChunk(is_done=True),
                ]
            )
        )

        panel_arg = console.print.call_args_list[0].args[0]
        assert isinstance(panel_arg, Panel)
        assert "thinking..." in panel_arg.renderable
        assert panel_arg.title == "Details"

    def test_renders_late_secondary_detail_once_at_finalize(self):
        console = MagicMock()
        renderer = StreamRenderer(console)

        renderer.render_stream(
            iter(
                [
                    StreamChunk(text="thinking...", is_reasoning=True),
                    StreamChunk(text="Hello"),
                    StreamChunk(
                        text="runtime warning",
                        kind="notification",
                        importance="secondary",
                    ),
                    StreamChunk(text=" world"),
                    StreamChunk(is_done=True),
                ]
            )
        )

        first_panel = console.print.call_args_list[0].args[0]
        second_panel = console.print.call_args_list[4].args[0]
        assert isinstance(first_panel, Panel)
        assert first_panel.title == "Details"
        assert "thinking..." in first_panel.renderable
        assert isinstance(second_panel, Panel)
        assert second_panel.title == "Details"
        assert "runtime warning" in second_panel.renderable

    def test_does_not_duplicate_already_rendered_secondary_detail(self):
        console = MagicMock()
        renderer = StreamRenderer(console)

        renderer.render_stream(
            iter(
                [
                    StreamChunk(text="thinking...", is_reasoning=True),
                    StreamChunk(text="Hello"),
                    StreamChunk(is_done=True),
                ]
            )
        )

        panels = [
            call.args[0]
            for call in console.print.call_args_list
            if call.args and isinstance(call.args[0], Panel)
        ]
        assert len(panels) == 1


class TestStreamRendererRenderError:
    def test_render_error(self):
        console = MagicMock()
        renderer = StreamRenderer(console)
        renderer.render_error("Something went wrong")
        console.print.assert_called_once_with("[red]✗ Something went wrong[/red]")

    def test_render_error_falls_back_to_ascii_when_console_encoding_is_limited(self):
        console = MagicMock()
        console.file = SimpleNamespace(encoding="cp1252")
        renderer = StreamRenderer(console)

        renderer.render_error("Something went wrong")

        console.print.assert_called_once_with("[red]x Something went wrong[/red]")


class TestStreamRendererActivity:
    def test_render_activity(self):
        console = MagicMock()
        renderer = StreamRenderer(console)
        renderer.render_activity("Context compacted")
        console.print.assert_called_once_with("ℹ Context compacted")

    def test_render_success_falls_back_to_ascii_when_console_encoding_is_limited(self):
        console = MagicMock()
        console.file = SimpleNamespace(encoding="cp1252")
        renderer = StreamRenderer(console)

        renderer.render_success("Saved.")

        console.print.assert_called_once_with("[green]OK Saved.[/green]")


class TestStreamRendererAgentEvents:
    def test_task_routed_renders_status(self):
        console = MagicMock()
        renderer = StreamRenderer(console)

        renderer.render_agent_event(TaskRouted(route="multi_step_task", reason="complex"))

        assert "Agent route: multi-step task." in console.print.call_args.args[0]

    def test_phase_changed_renders_status(self):
        console = MagicMock()
        renderer = StreamRenderer(console)

        renderer.render_agent_event(
            PhaseChanged(phase="replanning", summary="Replanning after failures.")
        )

        assert "Replanning after failures." in console.print.call_args.args[0]

    def test_plan_generated_renders_panel(self):
        console = MagicMock()
        renderer = StreamRenderer(console)
        plan = TaskPlan(task="task", steps=[PlanStep(index=1, description="Inspect files")])

        renderer.render_agent_event(PlanGenerated(plan=plan))

        panel_arg = console.print.call_args.args[0]
        assert isinstance(panel_arg, Panel)
        assert panel_arg.title == "Plan"

    def test_plan_updated_renders_activity_and_plan(self):
        console = MagicMock()
        renderer = StreamRenderer(console)
        plan = TaskPlan(task="task", steps=[PlanStep(index=1, description="Inspect files")])

        renderer.render_agent_event(PlanUpdated(plan=plan, changes="Replanned"))

        first_call = console.print.call_args_list[0].args[0]
        second_call = console.print.call_args_list[1].args[0]
        assert "Replanned" in first_call
        assert isinstance(second_call, Panel)

    def test_step_started_renders_activity(self):
        console = MagicMock()
        renderer = StreamRenderer(console)

        renderer.render_agent_event(StepStarted(step=PlanStep(index=2, description="Run tests")))

        assert "Starting step 2" in console.print.call_args.args[0]

    def test_tool_request_renders_warnings(self):
        console = MagicMock()
        renderer = StreamRenderer(console)
        event = ToolCallRequested(
            tool_name="shell_execute",
            arguments={"command": "rm -rf ."},
            requires_approval=True,
            risk_level="high",
            warnings=["Dangerous command"],
        )

        renderer.render_agent_event(event)
        renderer.flush_pending_details()

        assert "HIGH RISK" in console.print.call_args_list[0].args[0]
        warning_panel = console.print.call_args_list[1].args[0]
        assert isinstance(warning_panel, Panel)
        assert "Dangerous command" in warning_panel.renderable

    def test_tool_result_renders_failure(self):
        console = MagicMock()
        renderer = StreamRenderer(console)
        result = ToolResult.error("Tool failed", "failed")

        renderer.render_agent_event(ToolCallResult(tool_name="tool", result=result))

        assert "Tool failed" in console.print.call_args.args[0]

    def test_tool_result_renders_undo_affordance_for_file_changes(self):
        console = MagicMock()
        renderer = StreamRenderer(console)
        result = ToolResult.success("Patched app.py", files_changed=["app.py"])

        renderer.render_agent_event(
            ToolCallResult(tool_name="patch_apply", result=result, rollback_entries=2)
        )

        assert "Patched app.py" in console.print.call_args_list[0].args[0]
        assert (
            "Undo available: 2 change(s). Use /agent undo."
            in console.print.call_args_list[1].args[0]
        )

    def test_reasoning_output_uses_details_lane(self):
        console = MagicMock()
        renderer = StreamRenderer(console)

        renderer.render_agent_event(ReasoningOutput(text="Because it helps"))
        renderer.render_agent_event(StepStarted(step=PlanStep(index=2, description="Run tests")))

        panel_arg = console.print.call_args_list[0].args[0]
        assert isinstance(panel_arg, Panel)
        assert panel_arg.title == "Details"
        assert "Because it helps" in panel_arg.renderable

    def test_task_complete_renders_activity_and_summary(self):
        console = MagicMock()
        renderer = StreamRenderer(console)
        plan = TaskPlan(task="task")

        renderer.render_agent_event(TaskComplete(summary="Finished", plan=plan))

        assert "Task complete." in console.print.call_args_list[0].args[0]
        assert console.print.call_args_list[1].args[0] == "Finished"

    def test_task_failed_renders_error(self):
        console = MagicMock()
        renderer = StreamRenderer(console)
        plan = TaskPlan(task="task")

        renderer.render_agent_event(TaskFailed(reason="bad news", plan=plan))

        assert "bad news" in console.print.call_args.args[0]

    def test_task_stopped_renders_warning(self):
        console = MagicMock()
        renderer = StreamRenderer(console)

        renderer.render_agent_event(TaskStopped(reason="Task stopped by user."))

        assert "Task stopped by user." in console.print.call_args.args[0]

    def test_task_timed_out_renders_warning(self):
        console = MagicMock()
        renderer = StreamRenderer(console)
        plan = TaskPlan(task="task")

        renderer.render_agent_event(
            TaskTimedOut(reason="Agent task timed out due to inactivity.", plan=plan)
        )

        assert "timed out due to inactivity" in console.print.call_args.args[0]

    def test_tool_summary_limits_output(self):
        renderer = StreamRenderer(MagicMock())
        summary = renderer._tool_summary({"a": 1, "b": 2, "c": 3, "d": 4})

        assert summary == "a=1, b=2, c=3"
