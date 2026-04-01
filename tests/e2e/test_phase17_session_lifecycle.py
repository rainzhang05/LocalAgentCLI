"""Phase 17 slice 1: full-story session lifecycle (exec, agent, tool, save, reload).

These tests use a deterministic local ModelBackend stub so CI does not call live APIs.
The subprocess leg verifies that a session saved after an agent turn can be loaded in a
fresh process and inspected via the interactive shell (session file + /status).
"""

from __future__ import annotations

import json
import textwrap
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

from localagentcli.__main__ import _bootstrap_application, _run_exec_async
from localagentcli.models.abstraction import ModelAbstractionLayer
from localagentcli.models.backends.base import (
    ModelBackend,
    ModelMessage,
    StreamChunk,
    collect_generation_result,
)
from localagentcli.models.model_info import ModelInfo
from localagentcli.providers.base import RemoteProvider
from localagentcli.runtime.core import SessionExecutionRuntime
from tests.cli.test_packaging_cli import _run_cli, _write_config

_ORIGINAL_ASTREAM = ModelAbstractionLayer.astream_generate


async def _e2e_fast_astream(
    self: ModelAbstractionLayer,
    messages: list,
    **kwargs: object,
) -> AsyncIterator[StreamChunk]:
    """Avoid threaded local streaming in E2E tests (faster, deterministic under pytest-asyncio)."""
    if isinstance(self._backend, RemoteProvider):
        async for chunk in _ORIGINAL_ASTREAM(self, messages, **kwargs):
            yield chunk
        return
    for chunk in self._backend.stream_generate(messages, **kwargs):
        yield chunk


class Phase17E2EBackend(ModelBackend):
    """Minimal backend: first round emits a read-only file_read tool call; second round text."""

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
            id="phase17-e2e",
            name="Phase17 E2E Stub",
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
                    "id": "call_e2e_1",
                    "type": "function",
                    "function": {
                        "name": "file_read",
                        "arguments": '{"path":"notes.txt"}',
                    },
                },
            )
            yield StreamChunk(kind="done", is_done=True)
        else:
            yield StreamChunk(text="Read notes.txt: stub summary line.", kind="final_text")
            yield StreamChunk(kind="done", is_done=True)


def _config_template(workspace: str) -> str:
    return (
        textwrap.dedent(
            f"""
        [general]
        default_mode = "agent"
        workspace = "{workspace.replace(chr(92), "/")}"
        logging_level = "normal"

        [model]
        active_model = ""

        [provider]
        active_provider = ""

        [safety]
        approval_mode = "balanced"

        [generation]
        temperature = 0.7
        max_tokens = 4096
        top_p = 1.0

        [timeouts]
        shell_command = 120
        model_response = 300
        inactivity = 600

        [providers]
        """
        ).strip()
        + "\n"
    )


def _write_e2e_config(home: Path, workspace: Path) -> Path:
    config_dir = home / ".localagent"
    config_dir.mkdir(parents=True, exist_ok=True)
    path = config_dir / "config.toml"
    ws = str(workspace.resolve()).replace("\\", "/")
    path.write_text(_config_template(ws), encoding="utf-8")
    return path


@pytest.mark.asyncio
async def test_e2e_exec_agent_tool_save_and_subprocess_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Headless exec runs agent+file_read (stub), saves session; new process loads it."""
    home = tmp_path / "home"
    home.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "notes.txt").write_text("alpha\nbeta\n", encoding="utf-8")

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    _write_e2e_config(home, workspace)

    backend = Phase17E2EBackend()

    def _fake_resolve(self: SessionExecutionRuntime) -> ModelAbstractionLayer:
        return ModelAbstractionLayer(backend)

    session_name = "phase17_e2e_saved"
    storage, config, _first = _bootstrap_application()
    with (
        patch.object(SessionExecutionRuntime, "resolve_active_model", _fake_resolve),
        patch.object(ModelAbstractionLayer, "astream_generate", _e2e_fast_astream),
    ):
        rc = await _run_exec_async(
            "Inspect notes.txt and report one line.",
            config,
            storage,
            mode="agent",
            json_mode=False,
            approval_policy="auto",
            session_name=None,
            fork_name=None,
            save_session=session_name,
        )

    assert rc == 0

    session_path = home / ".localagent" / "sessions" / f"{session_name}.json"
    assert session_path.exists()
    payload = json.loads(session_path.read_text(encoding="utf-8"))
    hist = payload.get("history") or []
    roles = [m.get("role") for m in hist]
    assert "user" in roles
    assert "assistant" in roles

    _write_config(home, mode="agent")
    second = _run_cli(home, f"/session load {session_name}\n/status\n/exit\n")
    assert second.returncode == 0
    assert f"Session '{session_name}' loaded." in second.stdout
    assert "Mode:" in second.stdout


class Phase17ChatOnlyBackend(ModelBackend):
    """Single text reply for chat-mode exec (no tool rounds)."""

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
            id="phase17-chat-e2e",
            name="Phase17 Chat E2E Stub",
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
        yield StreamChunk(text="Hello from stub.", kind="final_text")
        yield StreamChunk(kind="done", is_done=True)


@pytest.mark.asyncio
async def test_e2e_exec_chat_turn_stub_roundtrip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Chat-mode headless exec completes with a text-only stub (second execution surface)."""
    home = tmp_path / "home2"
    home.mkdir()
    workspace = tmp_path / "workspace2"
    workspace.mkdir()

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    _write_e2e_config(home, workspace)

    backend = Phase17ChatOnlyBackend()

    def _fake_resolve(self: SessionExecutionRuntime) -> ModelAbstractionLayer:
        return ModelAbstractionLayer(backend)

    storage, config, _first = _bootstrap_application()
    with (
        patch.object(SessionExecutionRuntime, "resolve_active_model", _fake_resolve),
        patch.object(ModelAbstractionLayer, "astream_generate", _e2e_fast_astream),
    ):
        rc = await _run_exec_async(
            "Hello.",
            config,
            storage,
            mode="chat",
            json_mode=False,
            approval_policy="deny",
            session_name=None,
            fork_name=None,
            save_session=None,
        )

    assert rc == 0
