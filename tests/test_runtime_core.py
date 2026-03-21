"""Tests for the shared runtime core and execution surfaces."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from rich.console import Console

from localagentcli.models.abstraction import ModelAbstractionLayer
from localagentcli.models.backends.base import StreamChunk
from localagentcli.runtime import RuntimeMessage, RuntimeServices, SessionExecutionRuntime


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
    def test_run_chat_turn_uses_shared_generation_options(self, config, storage):
        runtime, _emitted = _make_runtime(config, storage)
        backend = FakeBackend()
        runtime.resolve_active_model = MagicMock(return_value=ModelAbstractionLayer(backend))

        turn = runtime.run_chat_turn("hello there")
        chunks = list(turn.stream or [])

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

    def test_dispatch_agent_turn_returns_route_and_controller(self, config, storage):
        runtime, _emitted = _make_runtime(config, storage)
        runtime._services.session_manager.current.mode = "agent"
        model = MagicMock()
        controller = MagicMock()
        controller.has_active_task = False
        controller.last_compaction_count = 2
        controller.dispatch_input.return_value = SimpleNamespace(
            stream=iter(()),
            events=None,
            triage=SimpleNamespace(outcome="direct_answer"),
        )
        runtime.resolve_active_model = MagicMock(return_value=model)
        runtime.get_or_create_agent_controller = MagicMock(return_value=controller)

        turn = runtime.dispatch_agent_turn("answer directly")

        assert turn is not None
        assert turn.mode == "agent"
        assert turn.controller is controller
        assert turn.route == "direct_answer"
        assert turn.compaction_count == 2
        controller.dispatch_input.assert_called_once_with("answer directly")
