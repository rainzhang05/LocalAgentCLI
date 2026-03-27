"""Tests for session usage normalization and budgeting helpers."""

from __future__ import annotations

from datetime import datetime

from localagentcli.models.backends.base import StreamChunk
from localagentcli.session.state import Session
from localagentcli.session.usage import (
    USAGE_BUDGET_KEY,
    latest_usage_counts,
    normalize_usage_counts,
    update_session_usage_budget,
    usage_budget_snapshot,
    usage_from_stream_chunks,
)


def _session() -> Session:
    now = datetime(2026, 3, 26, 10, 0, 0)
    return Session(
        id="s1",
        name=None,
        mode="chat",
        model="m",
        provider="p",
        workspace=".",
        created_at=now,
        updated_at=now,
    )


def test_normalize_usage_counts_openai_shape():
    normalized = normalize_usage_counts(
        {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
        }
    )

    assert normalized == {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
    }


def test_normalize_usage_counts_anthropic_shape():
    normalized = normalize_usage_counts(
        {
            "input_tokens": 7,
            "output_tokens": 3,
        }
    )

    assert normalized == {
        "prompt_tokens": 7,
        "completion_tokens": 3,
        "total_tokens": 10,
    }


def test_usage_from_stream_chunks_merges_done_usage():
    chunks = [
        StreamChunk(text="hello", kind="final_text"),
        StreamChunk(kind="done", is_done=True, usage={"prompt_tokens": 12, "completion_tokens": 4}),
    ]

    usage = usage_from_stream_chunks(chunks)

    assert usage == {
        "prompt_tokens": 12,
        "completion_tokens": 4,
        "total_tokens": 16,
    }


def test_update_session_usage_budget_accumulates_and_exposes_snapshot():
    session = _session()

    first = update_session_usage_budget(
        session,
        {"prompt_tokens": 20, "completion_tokens": 8},
        source="chat_stream",
    )
    second = update_session_usage_budget(
        session,
        {"input_tokens": 5, "output_tokens": 2},
        source="agent_step",
    )

    assert first == {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28}
    assert second == {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}

    budget = session.metadata[USAGE_BUDGET_KEY]
    assert budget["turns_with_usage"] == 2
    assert budget["cumulative"] == {
        "prompt_tokens": 25,
        "completion_tokens": 10,
        "total_tokens": 35,
    }

    assert latest_usage_counts(session.metadata) == {
        "prompt_tokens": 5,
        "completion_tokens": 2,
        "total_tokens": 7,
    }
    assert usage_budget_snapshot(session.metadata) == {
        "latest_prompt_tokens": 5,
        "latest_completion_tokens": 2,
        "latest_total_tokens": 7,
    }
