"""Integration-style tests for context compaction across execution paths."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from localagentcli.agents.controller import AgentController
from localagentcli.agents.events import (
    PhaseChanged,
    PlanGenerated,
    StepStarted,
    TaskComplete,
    TaskRouted,
    ToolCallRequested,
    ToolCallResult,
)
from localagentcli.models.backends.base import GenerationResult, StreamChunk
from localagentcli.models.model_info import ModelInfo
from localagentcli.session.state import Message, Session
from localagentcli.tools import create_default_tool_registry


class _FakeMultiStepModel:
    """Deterministic stub for multi-step agent tests (no sibling test imports)."""

    def __init__(self, responses: list[GenerationResult]) -> None:
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
            id="fake-multi",
            name="Fake Multi",
            capabilities={"tool_use": True, "reasoning": False, "streaming": False},
        )


def _session(workspace: Path, history: list[Message]) -> Session:
    now = datetime(2025, 1, 15, 10, 0, 0)
    return Session(
        id="session-1",
        name=None,
        mode="agent",
        model="provider-model",
        provider="provider",
        workspace=str(workspace),
        history=list(history),
        created_at=now,
        updated_at=now,
    )


class _CompactionDirectAnswerModel:
    """Supports compaction summarization via generate and direct-answer streaming."""

    def __init__(self) -> None:
        self.generate_calls: list[tuple[list, dict]] = []

    def generate(self, messages: list, **kwargs):
        self.generate_calls.append((messages, kwargs))
        return GenerationResult(text="Compacted history summary")

    def stream_generate(self, messages: list, **kwargs):
        yield StreamChunk(text="Short answer.", kind="final_text")
        yield StreamChunk(kind="done", is_done=True)

    def supports_tools(self) -> bool:
        return False

    def model_info(self) -> ModelInfo:
        return ModelInfo(
            id="fake-direct",
            name="Fake Direct",
            capabilities={"tool_use": False, "reasoning": False, "streaming": True},
        )


def test_agent_dispatch_compacts_large_history_before_direct_answer(tmp_path: Path):
    """Agent mode compacts like chat when history exceeds the effective threshold."""
    history = [
        Message(role="user", content=f"message {index} " * 40, timestamp=datetime.now())
        for index in range(11)
    ]
    session = _session(tmp_path, history)
    model = _CompactionDirectAnswerModel()
    controller = AgentController(
        model=model,
        session=session,
        tool_registry=create_default_tool_registry(tmp_path),
        context_limit=200,
    )

    dispatch = controller.dispatch_input("What is GitHub?")
    assert dispatch.stream is not None
    list(dispatch.stream)

    assert controller.last_compaction_count > 0
    assert session.history[0].is_summary is True
    assert session.history[0].content == "Compacted history summary"
    assert int(session.metadata.get("compaction_count", 0)) >= 1


def test_agent_handle_task_compacts_large_history_before_multi_step(tmp_path: Path):
    """Multi-step agent path compacts before planning when history is over budget."""
    (tmp_path / "notes.txt").write_text("alpha\nbeta\n", encoding="utf-8")
    history = [
        Message(role="user", content=f"message {index} " * 40, timestamp=datetime.now())
        for index in range(11)
    ]
    session = _session(tmp_path, history)
    model = _FakeMultiStepModel(
        [
            # Compaction summarization runs before triage/planning.
            GenerationResult(text="Compacted history summary"),
            GenerationResult(
                text=('{"steps":[{"description":"Read notes"},{"description":"Report findings"}]}')
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
        session=session,
        tool_registry=create_default_tool_registry(tmp_path),
        context_limit=200,
    )

    events = list(controller.handle_task("Implement reading the notes file and report findings"))

    assert controller.last_compaction_count > 0
    assert session.history[0].is_summary is True
    assert session.history[0].content == "Compacted history summary"
    assert isinstance(events[0], TaskRouted)
    assert any(isinstance(event, PhaseChanged) and event.phase == "planning" for event in events)
    assert any(isinstance(event, PlanGenerated) for event in events)
    assert sum(isinstance(event, StepStarted) for event in events) == 2
    assert any(
        isinstance(event, ToolCallRequested) and not event.requires_approval for event in events
    )
    assert any(
        isinstance(event, ToolCallResult) and event.result.status == "success" for event in events
    )
    assert isinstance(events[-1], TaskComplete)
