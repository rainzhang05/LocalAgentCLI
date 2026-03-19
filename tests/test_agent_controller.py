"""Tests for the Phase 5 agent controller and loop."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from localagentcli.agents.controller import AgentController
from localagentcli.agents.events import (
    PlanGenerated,
    PlanUpdated,
    StepStarted,
    TaskComplete,
    ToolCallRequested,
    ToolCallResult,
)
from localagentcli.models.backends.base import GenerationResult, StreamChunk
from localagentcli.session.state import Session
from localagentcli.tools import create_default_tool_registry


class FakeAgentModel:
    """Deterministic model stub for controller tests."""

    def __init__(self, responses: list[GenerationResult]):
        self._responses = list(responses)
        self.calls: list[tuple[list, dict]] = []

    def generate(self, messages: list, **kwargs):
        self.calls.append((messages, kwargs))
        return self._responses.pop(0)

    def stream_generate(self, messages: list, **kwargs):
        raise AssertionError("stream_generate should not be used for these tests")

    def supports_tools(self) -> bool:
        return True


def _make_session(workspace: Path) -> Session:
    now = datetime(2025, 1, 15, 10, 0, 0)
    return Session(
        id="session-1",
        name=None,
        mode="agent",
        model="provider-model",
        provider="provider",
        workspace=str(workspace),
        created_at=now,
        updated_at=now,
    )


class TestAgentController:
    def test_completes_multistep_task_with_read_only_tool(self, tmp_path: Path):
        (tmp_path / "notes.txt").write_text("alpha\nbeta\n", encoding="utf-8")
        model = FakeAgentModel(
            [
                GenerationResult(
                    text=(
                        '{"steps":[{"description":"Read notes"},{"description":"Report findings"}]}'
                    )
                ),
                GenerationResult(
                    text="",
                    tool_calls=[
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "file_read",
                                "arguments": '{"path":"notes.txt"}',
                            },
                        }
                    ],
                ),
                GenerationResult(text="Read notes successfully."),
                GenerationResult(text="Reported the contents to the user."),
            ]
        )
        controller = AgentController(
            model=model,
            session=_make_session(tmp_path),
            tool_registry=create_default_tool_registry(tmp_path),
        )

        events = list(controller.handle_task("Inspect the notes file and report findings"))

        assert isinstance(events[0], PlanGenerated)
        assert sum(isinstance(event, StepStarted) for event in events) == 2
        assert any(
            isinstance(event, ToolCallRequested) and not event.requires_approval for event in events
        )
        assert any(
            isinstance(event, ToolCallResult) and event.result.status == "success"
            for event in events
        )
        assert isinstance(events[-1], TaskComplete)
        assert controller._session.tasks[0].status == "completed"
        assert controller._session.history[-1].content == (
            "1. Read notes: Read notes successfully.\n"
            "2. Report findings: Reported the contents to the user."
        )

    def test_denied_write_action_resumes_and_completes(self, tmp_path: Path):
        model = FakeAgentModel(
            [
                GenerationResult(
                    text="",
                    tool_calls=[
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "file_write",
                                "arguments": '{"path":"output.txt","content":"hello"}',
                            },
                        }
                    ],
                ),
                GenerationResult(text="Skipped the write after approval was denied."),
            ]
        )
        controller = AgentController(
            model=model,
            session=_make_session(tmp_path),
            tool_registry=create_default_tool_registry(tmp_path),
        )

        initial_events = list(controller.handle_task("Create an output file"))

        assert isinstance(initial_events[-1], ToolCallRequested)
        assert initial_events[-1].requires_approval is True
        assert controller.has_pending_approval is True

        followup_events = list(controller.deny_action())

        assert any(
            isinstance(event, ToolCallResult) and event.result.status == "denied"
            for event in followup_events
        )
        assert any(isinstance(event, PlanUpdated) for event in followup_events)
        assert isinstance(followup_events[-1], TaskComplete)
        assert not (tmp_path / "output.txt").exists()

    def test_high_risk_read_still_prompts_in_autonomous_mode(self, tmp_path: Path):
        (tmp_path / ".env").write_text("API_KEY=secret\n", encoding="utf-8")
        model = FakeAgentModel(
            [
                GenerationResult(
                    text="",
                    tool_calls=[
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "file_read",
                                "arguments": '{"path":".env"}',
                            },
                        }
                    ],
                ),
                GenerationResult(text="Read the file after explicit approval."),
            ]
        )
        controller = AgentController(
            model=model,
            session=_make_session(tmp_path),
            tool_registry=create_default_tool_registry(tmp_path),
        )
        controller.set_autonomous()

        events = list(controller.handle_task("Inspect the env file"))

        request = events[-1]
        assert isinstance(request, ToolCallRequested)
        assert request.requires_approval is True
        assert request.risk_level == "high"

        followup_events = list(controller.approve_action())

        assert any(
            isinstance(event, ToolCallResult) and event.result.status == "success"
            for event in followup_events
        )
        assert isinstance(followup_events[-1], TaskComplete)

    def test_dispatches_trivial_prompt_to_direct_answer_fast_path(self, tmp_path: Path):
        class DirectAnswerModel(FakeAgentModel):
            def __init__(self):
                super().__init__([])

            def stream_generate(self, messages: list, **kwargs):
                yield StreamChunk(text="thinking", kind="reasoning", is_reasoning=True)
                yield StreamChunk(
                    text="GitHub is a code hosting platform.",
                    kind="final_text",
                )
                yield StreamChunk(kind="done", is_done=True)

            def supports_tools(self) -> bool:
                return False

        controller = AgentController(
            model=DirectAnswerModel(),
            session=_make_session(tmp_path),
            tool_registry=create_default_tool_registry(tmp_path),
        )

        dispatch = controller.dispatch_input("What is GitHub?")

        assert dispatch.events is None
        assert dispatch.stream is not None
        texts = [chunk.text for chunk in dispatch.stream if chunk.text]
        assert texts[-1] == "GitHub is a code hosting platform."
        assert controller._session.history[-1].metadata["fast_path"] is True

    def test_build_context_messages_includes_workspace_agents_instruction(self, tmp_path: Path):
        model = FakeAgentModel([GenerationResult(text='{"steps":[{"description":"noop"}]}')])
        session = _make_session(tmp_path)
        session.metadata["workspace_instruction"] = "Follow AGENTS.md exactly."
        session.pinned_instructions.append("Keep edits minimal.")
        controller = AgentController(
            model=model,
            session=session,
            tool_registry=create_default_tool_registry(tmp_path),
        )

        messages = controller._build_context_messages()

        assert messages[0].role == "system"
        assert "Follow AGENTS.md exactly." in messages[0].content
        assert "Keep edits minimal." in messages[0].content
