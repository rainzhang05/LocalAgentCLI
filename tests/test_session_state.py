"""Tests for localagentcli.session.state."""

from __future__ import annotations

from datetime import datetime, timedelta

from localagentcli.session.state import Message, Session


class TestMessage:
    """Tests for the Message dataclass."""

    def test_create_message(self):
        msg = Message(role="user", content="hello", timestamp=datetime.now())
        assert msg.role == "user"
        assert msg.content == "hello"
        assert msg.is_summary is False

    def test_to_dict(self):
        ts = datetime(2025, 1, 15, 10, 30, 0)
        msg = Message(role="assistant", content="hi", timestamp=ts, metadata={"key": "val"})
        d = msg.to_dict()
        assert d["role"] == "assistant"
        assert d["content"] == "hi"
        assert d["timestamp"] == "2025-01-15T10:30:00"
        assert d["metadata"] == {"key": "val"}
        assert d["is_summary"] is False

    def test_from_dict(self):
        data = {
            "role": "user",
            "content": "test",
            "timestamp": "2025-01-15T10:30:00",
            "metadata": {},
            "is_summary": True,
        }
        msg = Message.from_dict(data)
        assert msg.role == "user"
        assert msg.content == "test"
        assert msg.is_summary is True

    def test_from_dict_missing_optional(self):
        data = {
            "role": "user",
            "content": "test",
            "timestamp": "2025-01-15T10:30:00",
        }
        msg = Message.from_dict(data)
        assert msg.metadata == {}
        assert msg.is_summary is False

    def test_roundtrip(self):
        ts = datetime(2025, 1, 15, 10, 30, 0)
        original = Message(
            role="system",
            content="context",
            timestamp=ts,
            metadata={"tokens": 100},
            is_summary=True,
        )
        restored = Message.from_dict(original.to_dict())
        assert restored.role == original.role
        assert restored.content == original.content
        assert restored.timestamp == original.timestamp
        assert restored.metadata == original.metadata
        assert restored.is_summary == original.is_summary


class TestSession:
    """Tests for the Session dataclass."""

    def _make_session(self, **kwargs) -> Session:
        defaults = dict(
            id="test-id",
            name=None,
            mode="agent",
            model="test-model",
            provider="",
            workspace=".",
            created_at=datetime(2025, 1, 15, 10, 0, 0),
            updated_at=datetime(2025, 1, 15, 10, 0, 0),
        )
        defaults.update(kwargs)
        return Session(**defaults)

    def test_create_session(self):
        s = self._make_session()
        assert s.id == "test-id"
        assert s.mode == "agent"
        assert s.history == []

    def test_is_modified_false_when_fresh(self):
        s = self._make_session()
        assert s.is_modified is False

    def test_is_modified_true_with_history(self):
        s = self._make_session()
        s.history.append(Message(role="user", content="hi", timestamp=datetime.now()))
        assert s.is_modified is True

    def test_is_modified_true_when_updated(self):
        s = self._make_session(
            updated_at=datetime(2025, 1, 15, 11, 0, 0),
        )
        assert s.is_modified is True

    def test_to_dict(self):
        s = self._make_session(name="test-session")
        d = s.to_dict()
        assert d["id"] == "test-id"
        assert d["name"] == "test-session"
        assert d["mode"] == "agent"
        assert d["history"] == []
        assert d["created_at"] == "2025-01-15T10:00:00"

    def test_from_dict(self):
        data = {
            "id": "abc",
            "name": "loaded",
            "mode": "chat",
            "model": "llama",
            "provider": "openai",
            "workspace": "/tmp",
            "history": [],
            "tasks": [],
            "pinned_instructions": ["always use types"],
            "config_overrides": {"temperature": 0.5},
            "created_at": "2025-01-15T10:00:00",
            "updated_at": "2025-01-15T11:00:00",
            "metadata": {"key": "val"},
        }
        s = Session.from_dict(data)
        assert s.id == "abc"
        assert s.name == "loaded"
        assert s.mode == "chat"
        assert s.pinned_instructions == ["always use types"]

    def test_from_dict_missing_optional(self):
        data = {
            "id": "abc",
            "mode": "chat",
            "created_at": "2025-01-15T10:00:00",
            "updated_at": "2025-01-15T10:00:00",
        }
        s = Session.from_dict(data)
        assert s.model == ""
        assert s.workspace == "."
        assert s.history == []

    def test_roundtrip(self):
        ts = datetime(2025, 1, 15, 10, 0, 0)
        msg = Message(role="user", content="hello", timestamp=ts)
        original = self._make_session(
            name="roundtrip",
            history=[msg],
            pinned_instructions=["pin1"],
            config_overrides={"k": "v"},
            metadata={"summary": "test"},
        )
        restored = Session.from_dict(original.to_dict())
        assert restored.id == original.id
        assert restored.name == original.name
        assert len(restored.history) == 1
        assert restored.history[0].content == "hello"
        assert restored.pinned_instructions == ["pin1"]
        assert restored.config_overrides == {"k": "v"}
