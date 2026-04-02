"""Tests for the Phase 5 agent controller and loop."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from localagentcli.agents.controller import AgentController
from localagentcli.agents.events import (
    GuardianReviewCompleted,
    GuardianReviewStarted,
    PhaseChanged,
    PlanGenerated,
    PlanUpdated,
    StepStarted,
    TaskComplete,
    TaskFailed,
    TaskRouted,
    ToolCallRequested,
    ToolCallResult,
)
from localagentcli.agents.planner import TaskPlan
from localagentcli.models.backends.base import GenerationResult, StreamChunk
from localagentcli.models.model_info import ModelInfo
from localagentcli.session.instructions import build_conversation_model_messages
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

    def model_info(self) -> ModelInfo:
        return ModelInfo(
            id="fake-model",
            name="Fake Model",
            capabilities={"tool_use": True, "reasoning": False, "streaming": False},
        )


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
    def test_profile_uses_model_default_max_tokens(self, tmp_path: Path):
        model = FakeAgentModel([])
        controller = AgentController(
            model=model,
            session=_make_session(tmp_path),
            tool_registry=create_default_tool_registry(tmp_path),
        )

        profile = controller._profile("step")

        assert profile["max_tokens"] == model.model_info().default_max_tokens
        assert profile["temperature"] == 0.2

    def test_profile_applies_phase_caps(self, tmp_path: Path):
        model = FakeAgentModel([])
        controller = AgentController(
            model=model,
            session=_make_session(tmp_path),
            tool_registry=create_default_tool_registry(tmp_path),
            generation_config={"temperature": 0.9, "max_tokens": 9000, "top_p": 0.8},
        )

        triage = controller._profile("triage")
        planning = controller._profile("planning")

        assert triage == {"temperature": 0.1, "max_tokens": 512, "top_p": 0.8}
        assert planning == {"temperature": 0.1, "max_tokens": 2048, "top_p": 0.8}

    def test_completes_multistep_task_with_read_only_tool(self, tmp_path: Path):
        (tmp_path / "notes.txt").write_text("alpha\nbeta\n", encoding="utf-8")
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
                                "arguments": '{"path":"notes.txt"}',
                            },
                        }
                    ],
                ),
                GenerationResult(text="Read notes and reported the findings."),
            ]
        )
        controller = AgentController(
            model=model,
            session=_make_session(tmp_path),
            tool_registry=create_default_tool_registry(tmp_path),
        )

        events = list(controller.handle_task("Inspect the notes file and report findings"))

        assert isinstance(events[0], TaskRouted)
        assert any(
            isinstance(event, PhaseChanged) and event.phase == "planning" for event in events
        )
        assert any(isinstance(event, PlanGenerated) for event in events)
        assert sum(isinstance(event, StepStarted) for event in events) == 1
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
            "1. Inspect the notes file and report findings: Read notes and reported the findings."
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
                GenerationResult(text='{"steps":[{"description":"Skip file write and explain"}]}'),
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
        assert any(
            isinstance(event, PhaseChanged) and event.phase == "recovering"
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
                yield StreamChunk(
                    kind="done",
                    is_done=True,
                    usage={"prompt_tokens": 6, "completion_tokens": 4},
                )

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
        assert controller._session.history[-1].metadata["usage"] == {
            "prompt_tokens": 6,
            "completion_tokens": 4,
            "total_tokens": 10,
        }
        assert controller.task_state["phase"] == "completed"
        assert controller.task_state["usage_total_tokens"] == 10

    def test_build_conversation_model_messages_includes_workspace_agents_instruction(
        self, tmp_path: Path
    ):
        model = FakeAgentModel([GenerationResult(text='{"steps":[{"description":"noop"}]}')])
        session = _make_session(tmp_path)
        session.metadata["workspace_instruction"] = "Follow AGENTS.md exactly."
        session.pinned_instructions.append("Keep edits minimal.")
        controller = AgentController(
            model=model,
            session=session,
            tool_registry=create_default_tool_registry(tmp_path),
        )

        messages = build_conversation_model_messages(controller._session)

        assert messages[0].role == "system"
        assert "Follow AGENTS.md exactly." in messages[0].content
        assert "Keep edits minimal." in messages[0].content

    def test_stop_marks_task_as_stopped(self, tmp_path: Path):
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
                )
            ]
        )
        controller = AgentController(
            model=model,
            session=_make_session(tmp_path),
            tool_registry=create_default_tool_registry(tmp_path),
        )

        list(controller.handle_task("Create an output file"))
        controller.stop("Agent task interrupted.")

        assert controller.has_active_task is False
        assert controller.task_state["phase"] == "stopped"
        assert controller._session.history[-1].metadata["agent_task"] == "stopped"

    def test_autonomous_approval_mode_persists_across_completed_tasks(self, tmp_path: Path):
        (tmp_path / "notes.txt").write_text("alpha\n", encoding="utf-8")
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
                                "arguments": '{"path":"notes.txt"}',
                            },
                        }
                    ],
                ),
                GenerationResult(text="Read notes and reported findings."),
            ]
        )
        controller = AgentController(
            model=model,
            session=_make_session(tmp_path),
            tool_registry=create_default_tool_registry(tmp_path),
        )
        controller.set_autonomous()

        list(controller.handle_task("Inspect the notes file and report findings"))

        assert controller.approval_mode == "autonomous"

    def test_task_failed_event_persists_failure_type_in_task_state(self, tmp_path: Path):
        model = FakeAgentModel([])
        controller = AgentController(
            model=model,
            session=_make_session(tmp_path),
            tool_registry=create_default_tool_registry(tmp_path),
        )

        failed_event = TaskFailed(
            reason="Model credentials invalid.",
            plan=TaskPlan(task="demo", steps=[]),
            failure_type="model_terminal",
        )
        controller._record_event(failed_event)

        assert controller.task_state["phase"] == "failed"
        assert controller.task_state["last_error_type"] == "model_terminal"

    def test_guardian_review_events_persist_reviewer_metadata(self, tmp_path: Path):
        model = FakeAgentModel([])
        controller = AgentController(
            model=model,
            session=_make_session(tmp_path),
            tool_registry=create_default_tool_registry(tmp_path),
            approvals_reviewer="guardian_subagent",
        )

        controller._record_event(
            GuardianReviewStarted(
                tool_name="file_write",
                action_summary="file_write path=out.txt",
            )
        )
        assert controller.task_state["phase"] == "waiting_approval"
        assert controller.task_state["pending_tool"] == "file_write"

        controller._record_event(
            GuardianReviewCompleted(
                tool_name="file_write",
                approved=False,
                risk_level="high",
                risk_score=95,
                rationale="Potential destructive write.",
                evidence=[{"fact": "mutating file write", "source": "request"}],
                failure="",
            )
        )

        assert controller.task_state["approvals_reviewer"] == "guardian_subagent"
        assert controller.task_state["guardian_last_decision"] == "denied"
        assert controller.task_state["guardian_last_risk_score"] == 95
        assert controller.task_state["last_error_type"] == "guardian_denied"
