"""Tests for long-horizon memory extraction and merge helpers."""

from __future__ import annotations

from datetime import datetime

from localagentcli.session.memory import (
    LONG_HORIZON_MEMORY_KEY,
    extract_session_memory_entries,
    merge_long_horizon_memory,
)
from localagentcli.session.state import Message, Session


def _make_session() -> Session:
    now = datetime(2025, 1, 15, 10, 0, 0)
    return Session(
        id="memory-session",
        name=None,
        mode="agent",
        model="",
        provider="",
        workspace=".",
        created_at=now,
        updated_at=now,
    )


def test_extract_session_memory_entries_prefers_summaries():
    session = _make_session()
    session.history.append(
        Message(
            role="system",
            content="Summary: user wants strict typing.",
            timestamp=datetime.now(),
            is_summary=True,
        )
    )

    entries = extract_session_memory_entries(session)

    assert len(entries) == 1
    assert entries[0]["kind"] == "summary"
    assert "strict typing" in entries[0]["content"]


def test_merge_long_horizon_memory_deduplicates_content():
    session = _make_session()
    session.metadata[LONG_HORIZON_MEMORY_KEY] = [
        {
            "kind": "summary",
            "content": "Remember project uses pytest.",
            "source_timestamp": "",
            "updated_at": "",
        }
    ]

    merge_long_horizon_memory(
        session,
        [
            {
                "kind": "summary",
                "content": "Remember project uses pytest.",
                "source_timestamp": "",
                "updated_at": "",
            },
            {
                "kind": "insight",
                "content": "Workspace has strict linting.",
                "source_timestamp": "",
                "updated_at": "",
            },
        ],
    )

    memories = session.metadata[LONG_HORIZON_MEMORY_KEY]
    assert len(memories) == 2
    assert memories[0]["content"] == "Remember project uses pytest."
    assert memories[1]["content"] == "Workspace has strict linting."
