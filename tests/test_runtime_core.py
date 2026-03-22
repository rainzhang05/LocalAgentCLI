"""Tests for the shared runtime core and execution surfaces."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from rich.console import Console

from localagentcli.models.abstraction import ModelAbstractionLayer
from localagentcli.models.backends.base import StreamChunk
from localagentcli.runtime import (
    RuntimeMessage,
    RuntimeServices,
    SessionEventLog,
    SessionExecutionRuntime,
    SessionRuntime,
    UserTurnOp,
)


class FakeBackend:
    """Minimal backend stub for runtime-core tests."""

    def __init__(self) -> None:
        self.calls: list[tuple[list, dict]] = []

    def stream_generate(self, messages: list, **kwargs):
        self.calls.append((messages, kwargs))
        yield StreamChunk(text="Hello")
        yield StreamChunk(is_done=True)

    def supports_tools(self) -> bool:
        return True

    def supports_reasoning(self) -> bool:
        return False

    def supports_streaming(self) -> bool:
        return True

    def cancel(self) -> None:
        return None

    def load(self, _path) -> None:
        return None

    def unload(self) -> None:
        return None


def _make_runtime(config, storage):
    emitted: list[RuntimeMessage] = []
    services = RuntimeServices.create(config, storage, Console(record=True))
    runtime = SessionExecutionRuntime(
        services=services,
        emit=emitted.append,
        confirm_backend_install=lambda _backend, _label, _deps: False,
    )
    return runtime, emitted


class TestSessionExecutionRuntime:
    def test_build_generation_options_includes_request_timeout(self, config, storage):
        runtime, _emitted = _make_runtime(config, storage)
        opts = runtime.build_generation_options()
        assert "request_timeout" in opts
        assert isinstance(opts["request_timeout"], float)
        assert opts["request_timeout"] > 0

    async def test_run_chat_turn_uses_shared_generation_options(self, config, storage):
        runtime, _emitted = _make_runtime(config, storage)
        backend = FakeBackend()
        runtime.resolve_active_model = MagicMock(return_value=ModelAbstractionLayer(backend))

        turn = await runtime.arun_chat_turn("hello there")
        assert turn is not None
        chunks: list[StreamChunk] = []
        async for chunk in turn.stream or []:
            chunks.append(chunk)

        assert turn.mode == "chat"
        assert turn.compaction_count == 0
        assert [chunk.text for chunk in chunks if chunk.text] == ["Hello"]
        assert backend.calls[0][1]["temperature"] == 0.7
        assert backend.calls[0][1]["max_tokens"] == 4096
        assert runtime._services.session_manager.current.history[-1].content == "Hello"

    def test_reuses_agent_controller_for_same_target(self, config, storage):
        runtime, _emitted = _make_runtime(config, storage)
        model = MagicMock()
        model.backend = object()

        first = runtime.get_or_create_agent_controller(model)
        second = runtime.get_or_create_agent_controller(model)

        assert first is second

    def test_mode_change_invalidates_agent_controller_cache(self, config, storage):
        runtime, _emitted = _make_runtime(config, storage)
        model = MagicMock()
        model.backend = object()

        first = runtime.get_or_create_agent_controller(model)
        runtime._services.session_manager.current.mode = "chat"
        second = runtime.get_or_create_agent_controller(model)

        assert first is not second

    async def test_dispatch_agent_turn_returns_route_and_controller(self, config, storage):
        runtime, _emitted = _make_runtime(config, storage)
        runtime._services.session_manager.current.mode = "agent"
        model = MagicMock()
        controller = MagicMock()
        controller.has_active_task = False
        controller.last_compaction_count = 2

        async def _empty_stream():
            if False:
                yield  # pragma: no cover

        controller.adispatch_input = AsyncMock(
            return_value=SimpleNamespace(
                stream=_empty_stream(),
                events=None,
                triage=SimpleNamespace(outcome="direct_answer"),
            )
        )
        runtime.resolve_active_model = MagicMock(return_value=model)
        runtime.get_or_create_agent_controller = MagicMock(return_value=controller)

        turn = await runtime.adispatch_agent_turn("answer directly")

        assert turn is not None
        assert turn.mode == "agent"
        assert turn.controller is controller
        assert turn.route == "direct_answer"
        assert turn.compaction_count == 2
        controller.adispatch_input.assert_called_once_with("answer directly")


class TestSessionRuntime:
    async def test_event_log_records_submissions_and_events(self, config, storage):
        services = RuntimeServices.create(config, storage, Console(record=True))
        execution_runtime = SessionExecutionRuntime(
            services=services,
            emit=lambda _message: None,
            confirm_backend_install=lambda _backend, _label, _deps: False,
        )
        runtime = SessionRuntime(
            execution_runtime,
            event_log=SessionEventLog(
                storage.cache_dir / "runtime-events",
                services.session_manager.current.id,
            ),
        )
        backend = FakeBackend()
        execution_runtime.resolve_active_model = MagicMock(
            return_value=ModelAbstractionLayer(backend)
        )

        runtime.submit(UserTurnOp(prompt="hello", mode="chat"))
        async for _event in runtime.aiter_events():
            pass

        records = runtime._event_log.read_records()  # type: ignore[union-attr]
        assert records
        assert any(record["kind"] == "submission" for record in records)
        assert any(record["kind"] == "event" for record in records)
