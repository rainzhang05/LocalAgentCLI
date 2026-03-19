"""Tests for localagentcli.agents.chat."""

from __future__ import annotations

from datetime import datetime

from localagentcli.agents.chat import ChatController
from localagentcli.models.backends.base import GenerationResult, StreamChunk
from localagentcli.session.state import Message, Session


class FakeModel:
    """Minimal fake model for chat controller tests."""

    def __init__(self, summary_text: str = "summary") -> None:
        self.summary_text = summary_text
        self.generate_calls: list[tuple[list, dict]] = []
        self.stream_calls: list[tuple[list, dict]] = []

    def generate(self, messages: list, **kwargs):
        self.generate_calls.append((messages, kwargs))
        return GenerationResult(text=self.summary_text)

    def stream_generate(self, messages: list, **kwargs):
        self.stream_calls.append((messages, kwargs))
        yield StreamChunk(text="thinking", is_reasoning=True)
        yield StreamChunk(text="Hello")
        yield StreamChunk(text=" world")
        yield StreamChunk(is_done=True)


def _make_session(**kwargs) -> Session:
    defaults = dict(
        id="session-1",
        name=None,
        mode="chat",
        model="provider-model",
        provider="provider",
        workspace=".",
        created_at=datetime(2025, 1, 15, 10, 0, 0),
        updated_at=datetime(2025, 1, 15, 10, 0, 0),
    )
    defaults.update(kwargs)
    return Session(**defaults)


class TestChatController:
    def test_handle_input_appends_history_and_uses_generation_config(self):
        model = FakeModel()
        session = _make_session()
        controller = ChatController(
            model=model,
            session=session,
            generation_config={"temperature": 0.2, "max_tokens": 200},
        )

        chunks = list(controller.handle_input("Hi there"))

        assert [chunk.text for chunk in chunks if chunk.text] == ["thinking", "Hello", " world"]
        assert session.history[0].role == "user"
        assert session.history[0].content == "Hi there"
        assert session.history[1].role == "assistant"
        assert session.history[1].content == "Hello world"
        assert session.history[1].metadata["reasoning"] == "thinking"
        assert len(session.history[1].metadata["chunks"]) == 3
        assert model.stream_calls[0][1]["temperature"] == 0.2
        assert model.stream_calls[0][1]["max_tokens"] == 200

    def test_handle_input_emits_compaction_message(self):
        model = FakeModel(summary_text="Older summary")
        history = [
            Message(role="user", content=f"message {index} " * 40, timestamp=datetime.now())
            for index in range(11)
        ]
        session = _make_session(history=history, pinned_instructions=["Keep answers concise."])
        controller = ChatController(model=model, session=session, context_limit=200)

        list(controller.handle_input("latest turn"))

        assert controller.last_compaction_count > 0
        assert controller.last_compaction_message is not None
        assert session.history[0].is_summary is True
        assert session.metadata["compaction_count"] == 1

    def test_pin_and_unpin_instruction(self):
        controller = ChatController(model=FakeModel(), session=_make_session())

        controller.pin_instruction("Always answer in bullets.")
        assert controller._session.pinned_instructions == ["Always answer in bullets."]

        controller.unpin_instruction(0)
        assert controller._session.pinned_instructions == []

    def test_handle_input_includes_workspace_agents_instruction(self):
        model = FakeModel()
        session = _make_session(
            pinned_instructions=["Keep answers concise."],
            metadata={"workspace_instruction": "Follow AGENTS.md exactly."},
        )
        controller = ChatController(model=model, session=session)

        list(controller.handle_input("Hi there"))

        system_message = model.stream_calls[0][0][0]
        assert system_message.role == "system"
        assert "Follow AGENTS.md exactly." in system_message.content
        assert "Keep answers concise." in system_message.content
