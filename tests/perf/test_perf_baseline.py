"""Deterministic hot-path timing; opt-in via RUN_PERF=1 (does not run in default CI)."""

from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path

import pytest

from localagentcli.session.instructions import build_conversation_model_messages
from localagentcli.session.state import Message, Session

pytestmark = [
    pytest.mark.perf,
    pytest.mark.skipif(
        os.environ.get("RUN_PERF") != "1",
        reason="Set RUN_PERF=1 to run opt-in performance baselines",
    ),
]


def _session_with_history(workspace: Path) -> Session:
    now = datetime(2025, 1, 15, 10, 0, 0)
    session = Session(
        id="perf-session",
        name=None,
        mode="agent",
        model="test-model",
        provider="test",
        workspace=str(workspace),
        created_at=now,
        updated_at=now,
    )
    session.metadata["workspace_instruction"] = "Bench workspace instruction."
    session.pinned_instructions.append("Pinned instruction for perf.")
    for i in range(24):
        session.history.append(
            Message(
                role="user" if i % 2 == 0 else "assistant",
                content=f"msg-{i}",
                timestamp=now,
            )
        )
    return session


def test_build_conversation_model_messages_hot_path(tmp_path: Path) -> None:
    """Exercise message construction repeatedly; loose ceiling catches pathological regressions."""
    session = _session_with_history(tmp_path)
    iterations = 400
    t0 = time.perf_counter()
    for _ in range(iterations):
        msgs = build_conversation_model_messages(session)
        assert len(msgs) >= 1
    elapsed = time.perf_counter() - t0
    # Generous bound: fails only on accidental quadratic blowups, not normal CI variance.
    assert elapsed < 60.0, (
        f"build_conversation_model_messages took {elapsed:.3f}s for {iterations} runs"
    )
