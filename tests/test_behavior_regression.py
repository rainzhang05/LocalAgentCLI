"""Behavior-level runtime regression tests (Phase 17 slice 2 + follow-ons).

These complement unit tests and E2E exec tests by asserting stable invariants across
approval policy, session persistence, fork lineage, and headless `exec --json` output
without live providers.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from localagentcli.__main__ import _bootstrap_application, _run_exec_async
from localagentcli.agents.controller import AgentController
from localagentcli.agents.events import PhaseChanged, TaskComplete
from localagentcli.models.abstraction import ModelAbstractionLayer
from localagentcli.models.backends.base import (
    ModelBackend,
    ModelMessage,
    StreamChunk,
    collect_generation_result,
)
from localagentcli.models.model_info import ModelInfo
from localagentcli.runtime.core import SessionExecutionRuntime
from localagentcli.safety.approval import ApprovalManager
from localagentcli.safety.boundary import WorkspaceBoundary
from localagentcli.safety.layer import SafetyLayer
from localagentcli.safety.rollback import RollbackManager
from localagentcli.session.manager import SessionManager
from localagentcli.session.state import Message, Session
from localagentcli.tools import create_default_tool_registry
from tests.e2e.test_phase17_session_lifecycle import (
    Phase17E2EBackend,
    _e2e_fast_astream,
    _write_e2e_config,
)


class _WriteToolFirstBackend(ModelBackend):
    """First model round requests file_write; second round completes with plain text."""

    def __init__(self) -> None:
        self._round = 0

    def load(self, model_path: Path, **kwargs: object) -> None:
        return

    def unload(self) -> None:
        return

    def memory_usage(self) -> int:
        return 0

    def capabilities(self) -> dict:
        return {"tool_use": True, "reasoning": False, "streaming": True}

    def model_info(self) -> ModelInfo:
        return ModelInfo(
            id="phase17-behavior",
            name="Behavior Regression Stub",
            capabilities={"tool_use": True, "reasoning": False, "streaming": True},
        )

    def supports_tools(self) -> bool:
        return True

    def supports_reasoning(self) -> bool:
        return False

    def supports_streaming(self) -> bool:
        return True

    def generate(self, messages: list[ModelMessage], **kwargs: object) -> object:
        return collect_generation_result(self.stream_generate(messages, **kwargs))

    def stream_generate(
        self, messages: list[ModelMessage], **kwargs: object
    ) -> Iterator[StreamChunk]:
        self._round += 1
        if self._round == 1:
            yield StreamChunk(
                kind="tool_call",
                tool_call_data={
                    "id": "call_write_1",
                    "type": "function",
                    "function": {
                        "name": "file_write",
                        "arguments": '{"path":"out.txt","content":"blocked"}',
                    },
                },
            )
            yield StreamChunk(kind="done", is_done=True)
        else:
            yield StreamChunk(text="Completed after write gate.", kind="final_text")
            yield StreamChunk(kind="done", is_done=True)


@pytest.mark.asyncio
async def test_headless_exec_deny_policy_completes_after_write_tool_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deny policy: mutating tool request is denied; turn still finishes."""
    home = tmp_path / "home"
    home.mkdir()
    workspace = tmp_path / "ws"
    workspace.mkdir()

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    _write_e2e_config(home, workspace)

    backend = _WriteToolFirstBackend()

    def _fake_resolve(self: SessionExecutionRuntime) -> ModelAbstractionLayer:
        return ModelAbstractionLayer(backend)

    storage, config, _first = _bootstrap_application()
    with (
        patch.object(SessionExecutionRuntime, "resolve_active_model", _fake_resolve),
        patch.object(ModelAbstractionLayer, "astream_generate", _e2e_fast_astream),
    ):
        rc = await _run_exec_async(
            "Create out.txt with content blocked.",
            config,
            storage,
            mode="agent",
            json_mode=False,
            approval_policy="deny",
            session_name=None,
            fork_name=None,
            save_session=None,
        )

    assert rc == 0
    assert not (workspace / "out.txt").exists()


def test_fork_lineage_preserved_in_saved_session_json(storage, config, tmp_path: Path) -> None:
    """Forked sessions persist fork_parent_id and fork_parent_name for resume/diff semantics."""
    manager = SessionManager(storage.sessions_dir, config)
    manager.new_session()
    manager.current.workspace = str(tmp_path / "ws")
    manager.current.history.append(
        Message(role="user", content="parent seed", timestamp=manager.current.created_at)
    )
    manager.save_session("br-parent")

    forked = manager.fork_session("br-parent", "br-child")
    assert forked.metadata.get("fork_parent_name") == "br-parent"
    assert forked.metadata.get("fork_parent_id")

    manager.save_session("br-child")

    path = storage.sessions_dir / "br-child.json"
    assert path.exists()
    payload = json.loads(path.read_text(encoding="utf-8"))
    meta = payload.get("metadata") or {}
    assert meta.get("fork_parent_name") == "br-parent"
    assert meta.get("fork_parent_id")


def test_recovery_policy_replan_eligibility_stable() -> None:
    """Documented replan gates stay aligned with recovery classification (regression signal)."""
    from localagentcli.agents.recovery import FailureClass, should_replan_after_failure

    assert should_replan_after_failure(FailureClass.TOOL_DENIED) is True
    assert should_replan_after_failure(FailureClass.MODEL_TRANSIENT) is False


@pytest.mark.asyncio
async def test_headless_exec_auto_policy_allows_file_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Auto policy: headless exec approves mutating tools and writes the file."""
    home = tmp_path / "home_auto"
    home.mkdir()
    workspace = tmp_path / "ws_auto"
    workspace.mkdir()

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    _write_e2e_config(home, workspace)

    backend = _WriteToolFirstBackend()

    def _fake_resolve(self: SessionExecutionRuntime) -> ModelAbstractionLayer:
        return ModelAbstractionLayer(backend)

    storage, config, _first = _bootstrap_application()
    with (
        patch.object(SessionExecutionRuntime, "resolve_active_model", _fake_resolve),
        patch.object(ModelAbstractionLayer, "astream_generate", _e2e_fast_astream),
    ):
        rc = await _run_exec_async(
            "Create out.txt with content blocked.",
            config,
            storage,
            mode="agent",
            json_mode=False,
            approval_policy="auto",
            session_name=None,
            fork_name=None,
            save_session=None,
        )

    assert rc == 0
    assert (workspace / "out.txt").read_text(encoding="utf-8") == "blocked"


@pytest.mark.asyncio
async def test_headless_exec_json_mode_emits_parseable_runtime_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """JSON exec prints one JSON object per line on stdout; each matches RuntimeEvent.to_dict()."""
    home = tmp_path / "home_json"
    home.mkdir()
    workspace = tmp_path / "ws_json"
    workspace.mkdir()

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    _write_e2e_config(home, workspace)

    backend = _WriteToolFirstBackend()

    def _fake_resolve(self: SessionExecutionRuntime) -> ModelAbstractionLayer:
        return ModelAbstractionLayer(backend)

    json_lines: list[str] = []

    class _CaptureConsole:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self._stderr = bool(kwargs.get("stderr"))

        def print(self, *args: object, **kwargs: object) -> None:
            if not args:
                return
            text = str(args[0])
            if not self._stderr:
                json_lines.append(text)

    storage, config, _first = _bootstrap_application()
    with (
        patch("localagentcli.__main__.Console", _CaptureConsole),
        patch.object(SessionExecutionRuntime, "resolve_active_model", _fake_resolve),
        patch.object(ModelAbstractionLayer, "astream_generate", _e2e_fast_astream),
    ):
        rc = await _run_exec_async(
            "Create out.txt with content blocked.",
            config,
            storage,
            mode="agent",
            json_mode=True,
            approval_policy="auto",
            session_name=None,
            fork_name=None,
            save_session=None,
        )

    assert rc == 0
    assert json_lines, "expected JSON lines on stdout console in json_mode"
    parsed: list[dict] = []
    for line in json_lines:
        obj = json.loads(line)
        assert isinstance(obj, dict)
        parsed.append(obj)
        assert "type" in obj and "submission_id" in obj and "timestamp" in obj
    assert any(e.get("type") == "turn_completed" for e in parsed)


@pytest.mark.asyncio
async def test_headless_exec_json_mode_deny_policy_emits_parseable_runtime_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deny policy + JSON exec: NDJSON events remain valid; mutating tool does not run."""
    home = tmp_path / "home_json_deny"
    home.mkdir()
    workspace = tmp_path / "ws_json_deny"
    workspace.mkdir()

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    _write_e2e_config(home, workspace)

    backend = _WriteToolFirstBackend()

    def _fake_resolve(self: SessionExecutionRuntime) -> ModelAbstractionLayer:
        return ModelAbstractionLayer(backend)

    json_lines: list[str] = []

    class _CaptureConsole:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self._stderr = bool(kwargs.get("stderr"))

        def print(self, *args: object, **kwargs: object) -> None:
            if not args:
                return
            text = str(args[0])
            if not self._stderr:
                json_lines.append(text)

    storage, config, _first = _bootstrap_application()
    with (
        patch("localagentcli.__main__.Console", _CaptureConsole),
        patch.object(SessionExecutionRuntime, "resolve_active_model", _fake_resolve),
        patch.object(ModelAbstractionLayer, "astream_generate", _e2e_fast_astream),
    ):
        rc = await _run_exec_async(
            "Create out.txt with content blocked.",
            config,
            storage,
            mode="agent",
            json_mode=True,
            approval_policy="deny",
            session_name=None,
            fork_name=None,
            save_session=None,
        )

    assert rc == 0
    assert not (workspace / "out.txt").exists()
    assert json_lines, "expected JSON lines on stdout console in json_mode"
    parsed: list[dict] = []
    for line in json_lines:
        obj = json.loads(line)
        assert isinstance(obj, dict)
        parsed.append(obj)
        assert "type" in obj and "submission_id" in obj and "timestamp" in obj
    assert any(e.get("type") == "turn_completed" for e in parsed)


@pytest.mark.asyncio
async def test_headless_exec_json_mode_save_session_persists_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """JSON exec with --save-session persists user+assistant history."""
    home = tmp_path / "home_json_save"
    home.mkdir()
    workspace = tmp_path / "ws_json_save"
    workspace.mkdir()
    (workspace / "notes.txt").write_text("alpha\nbeta\n", encoding="utf-8")

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    _write_e2e_config(home, workspace)

    backend = Phase17E2EBackend()

    def _fake_resolve(self: SessionExecutionRuntime) -> ModelAbstractionLayer:
        return ModelAbstractionLayer(backend)

    json_lines: list[str] = []

    class _CaptureConsole:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self._stderr = bool(kwargs.get("stderr"))

        def print(self, *args: object, **kwargs: object) -> None:
            if not args:
                return
            text = str(args[0])
            if not self._stderr:
                json_lines.append(text)

    session_name = "br_json_save"
    storage, config, _first = _bootstrap_application()
    with (
        patch("localagentcli.__main__.Console", _CaptureConsole),
        patch.object(SessionExecutionRuntime, "resolve_active_model", _fake_resolve),
        patch.object(ModelAbstractionLayer, "astream_generate", _e2e_fast_astream),
    ):
        rc = await _run_exec_async(
            "Inspect notes.txt briefly.",
            config,
            storage,
            mode="agent",
            json_mode=True,
            approval_policy="auto",
            session_name=None,
            fork_name=None,
            save_session=session_name,
        )

    assert rc == 0
    session_path = storage.sessions_dir / f"{session_name}.json"
    assert session_path.exists()
    payload = json.loads(session_path.read_text(encoding="utf-8"))
    hist = payload.get("history") or []
    roles = [m.get("role") for m in hist]
    assert "user" in roles
    assert "assistant" in roles

    assert json_lines, "expected JSON lines on stdout console in json_mode"
    for line in json_lines:
        obj = json.loads(line)
        assert isinstance(obj, dict)
        assert "type" in obj and "submission_id" in obj and "timestamp" in obj


@pytest.mark.asyncio
async def test_headless_exec_fork_save_persists_fork_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exec with --fork and --save-session writes fork lineage into the saved session JSON."""
    home = tmp_path / "home_fork"
    home.mkdir()
    workspace = tmp_path / "ws_fork"
    workspace.mkdir()
    (workspace / "notes.txt").write_text("seed\n", encoding="utf-8")

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    _write_e2e_config(home, workspace)

    storage, config, _first = _bootstrap_application()
    mgr = SessionManager(storage.sessions_dir, config)
    mgr.new_session()
    mgr.current.workspace = str(workspace.resolve())
    mgr.current.history.append(
        Message(role="user", content="parent turn", timestamp=mgr.current.created_at)
    )
    mgr.save_session("br-parent")

    backend = Phase17E2EBackend()

    def _fake_resolve(self: SessionExecutionRuntime) -> ModelAbstractionLayer:
        return ModelAbstractionLayer(backend)

    with (
        patch.object(SessionExecutionRuntime, "resolve_active_model", _fake_resolve),
        patch.object(ModelAbstractionLayer, "astream_generate", _e2e_fast_astream),
    ):
        rc = await _run_exec_async(
            "Inspect notes.txt briefly.",
            config,
            storage,
            mode="agent",
            json_mode=False,
            approval_policy="auto",
            session_name=None,
            fork_name="br-parent",
            save_session="br-child",
        )

    assert rc == 0
    child_path = storage.sessions_dir / "br-child.json"
    assert child_path.exists()
    payload = json.loads(child_path.read_text(encoding="utf-8"))
    meta = payload.get("metadata") or {}
    assert meta.get("fork_parent_name") == "br-parent"
    assert meta.get("fork_parent_id")


class _TransientRetryBackend(ModelBackend):
    """First agenerate stream ends with a transient usage error; second returns plain text."""

    def __init__(self) -> None:
        self._stream_calls = 0

    def load(self, model_path: Path, **kwargs: object) -> None:
        return

    def unload(self) -> None:
        return

    def memory_usage(self) -> int:
        return 0

    def capabilities(self) -> dict:
        return {"tool_use": True, "reasoning": False, "streaming": True}

    def model_info(self) -> ModelInfo:
        return ModelInfo(
            id="phase17-transient-retry",
            name="Transient Retry Stub",
            capabilities={"tool_use": True, "reasoning": False, "streaming": True},
            default_max_tokens=2048,
        )

    def supports_tools(self) -> bool:
        return True

    def supports_reasoning(self) -> bool:
        return False

    def supports_streaming(self) -> bool:
        return True

    def generate(self, messages: list[ModelMessage], **kwargs: object) -> object:
        return collect_generation_result(self.stream_generate(messages, **kwargs))

    def stream_generate(
        self, messages: list[ModelMessage], **kwargs: object
    ) -> Iterator[StreamChunk]:
        self._stream_calls += 1
        if self._stream_calls == 1:
            yield StreamChunk(
                kind="done",
                is_done=True,
                usage={"error": "Rate limit exceeded, retry later"},
            )
        else:
            yield StreamChunk(text="Recovered after transient failure.", kind="final_text")
            yield StreamChunk(kind="done", is_done=True)


def _session_agent_headless(tmp_path: Path) -> Session:
    now = datetime.now()
    return Session(
        id="sess-async-retry",
        name=None,
        mode="agent",
        model="",
        provider="",
        workspace=str(tmp_path.resolve()),
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_async_agent_transient_model_error_retries_then_completes(tmp_path: Path) -> None:
    """Async agent loop emits retrying then completes after a transient model error (arun path)."""
    approval = ApprovalManager()
    safety = SafetyLayer(
        approval,
        WorkspaceBoundary(tmp_path.resolve()),
        RollbackManager("sess-async-retry", tmp_path / ".cache"),
    )
    backend = _TransientRetryBackend()
    model = ModelAbstractionLayer(backend)
    controller = AgentController(
        model=model,
        session=_session_agent_headless(tmp_path),
        tool_registry=create_default_tool_registry(tmp_path),
        approval=approval,
        safety=safety,
    )
    # Thread-bridged local astream hangs under pytest-asyncio; mirror e2e fast path.
    with patch.object(ModelAbstractionLayer, "astream_generate", _e2e_fast_astream):
        dispatch = await controller.adispatch_input("Inspect notes.txt.")
        assert dispatch.events is not None
        events: list = []
        async for event in dispatch.events:
            events.append(event)

    assert any(isinstance(e, PhaseChanged) and e.phase == "retrying" for e in events), (
        "expected a retrying phase after transient model failure"
    )
    assert isinstance(events[-1], TaskComplete)
