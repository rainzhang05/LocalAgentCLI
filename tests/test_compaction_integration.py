"""Integration-style tests for context compaction across execution paths."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from localagentcli.agents.controller import AgentController
from localagentcli.models.backends.base import GenerationResult, StreamChunk
from localagentcli.session.state import Message, Session
from localagentcli.tools import create_default_tool_registry


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
