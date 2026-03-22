"""SessionRuntime submission protocol edge cases."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from localagentcli.runtime.protocol import ApprovalDecisionOp, InterruptOp, UserTurnOp
from localagentcli.runtime.session_runtime import _SUBMISSION_CAPACITY, SessionRuntime


def test_submit_interrupt_without_active_submission_raises():
    rt = SessionRuntime(MagicMock())
    with pytest.raises(RuntimeError, match="No active submission"):
        rt.submit(InterruptOp())


def test_submit_approval_without_active_submission_raises():
    rt = SessionRuntime(MagicMock())
    with pytest.raises(RuntimeError, match="No active submission"):
        rt.submit(ApprovalDecisionOp("approve"))


def test_submit_queue_capacity_raises():
    ex = MagicMock()
    sm = MagicMock()
    sm.current.mode = "chat"
    sm.current.history = []
    ex._services.session_manager = sm
    ex.resolve_active_model = MagicMock(return_value=None)
    ex.arun_chat_turn = MagicMock(return_value=None)

    rt = SessionRuntime(ex)
    for _ in range(_SUBMISSION_CAPACITY):
        rt.submit(UserTurnOp(prompt="x", mode="chat"))
    with pytest.raises(RuntimeError, match="full"):
        rt.submit(UserTurnOp(prompt="y", mode="chat"))


def test_iter_events_emits_deprecation_warning():
    rt = SessionRuntime(MagicMock())
    with pytest.warns(DeprecationWarning, match="deprecated"):
        iterator = rt.iter_events()
    assert iterator is not None
