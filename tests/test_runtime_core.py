"""Tests for the shared runtime core and execution surfaces."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from rich.console import Console

from localagentcli.models.abstraction import ModelAbstractionLayer
from localagentcli.models.backends.base import StreamChunk
from localagentcli.models.readiness import build_target_readiness
from localagentcli.runtime import (
    RuntimeMessage,
    RuntimeServices,
    SessionEventLog,
    SessionExecutionRuntime,
    SessionRuntime,
    UserTurnOp,
)
from localagentcli.tools.exec_process import LocalExecProcess


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

    def test_build_generation_options_includes_reasoning_effort_when_configured(
        self, config, storage
    ):
        config.set("generation.reasoning_effort", "high")
        runtime, _emitted = _make_runtime(config, storage)

        opts = runtime.build_generation_options()

        assert opts["reasoning_effort"] == "high"

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

    async def test_dispatch_agent_turn_refreshes_tools_when_feature_enabled(self, config, storage):
        config._config.setdefault("features", {})["mcp_tool_inventory_refresh"] = True
        runtime, _emitted = _make_runtime(config, storage)
        runtime._services.session_manager.current.mode = "agent"
        runtime._services.build_tool_router = MagicMock(return_value=MagicMock())

        model = MagicMock()
        controller = MagicMock()
        controller.has_active_task = False
        controller.last_compaction_count = 0

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

        turn = await runtime.adispatch_agent_turn("refresh tools")

        assert turn is not None
        controller.set_tool_registry.assert_called_once()

    async def test_async_agent_gate_reports_tradeoff_for_unknown_provider(self, config, storage):
        runtime, emitted = _make_runtime(config, storage)
        runtime._services.session_manager.current.provider = "openai"
        runtime._services.session_manager.current.model = "gpt-4o"
        runtime._active_provider = MagicMock()

        readiness = build_target_readiness(
            kind="provider",
            selection_state="unknown",
            capabilities={"tool_use": True, "reasoning": False, "streaming": True},
            capability_provenance={
                "tool_use": {"tier": "unknown", "reason": "Discovery missing."},
                "reasoning": {"tier": "unknown", "reason": "Discovery missing."},
                "streaming": {"tier": "unknown", "reason": "Discovery missing."},
            },
            guidance="Run /providers test.",
        )

        import localagentcli.runtime.core as runtime_core

        original = runtime_core.aresolve_remote_model_readiness
        runtime_core.aresolve_remote_model_readiness = AsyncMock(return_value=readiness)
        try:
            allowed = await runtime._async_ensure_agent_dispatch_allowed()
        finally:
            runtime_core.aresolve_remote_model_readiness = original

        assert allowed is False
        assert any("Readiness posture:" in message.text for message in emitted)
        assert any("Tradeoff:" in message.text for message in emitted)

    async def test_async_agent_gate_reports_tradeoff_for_untrusted_tool_use(self, config, storage):
        runtime, emitted = _make_runtime(config, storage)
        runtime._services.session_manager.current.provider = "openai"
        runtime._services.session_manager.current.model = "gpt-4o"
        runtime._active_provider = MagicMock()

        readiness = build_target_readiness(
            kind="provider",
            selection_state="api_discovered",
            capabilities={"tool_use": True, "reasoning": False, "streaming": True},
            capability_provenance={
                "tool_use": {"tier": "legacy_fallback", "reason": "Fallback only."},
                "reasoning": {"tier": "legacy_fallback", "reason": "Fallback only."},
                "streaming": {"tier": "legacy_fallback", "reason": "Fallback only."},
            },
            guidance="Run /providers test.",
        )

        import localagentcli.runtime.core as runtime_core

        original = runtime_core.aresolve_remote_model_readiness
        runtime_core.aresolve_remote_model_readiness = AsyncMock(return_value=readiness)
        try:
            allowed = await runtime._async_ensure_agent_dispatch_allowed()
        finally:
            runtime_core.aresolve_remote_model_readiness = original

        assert allowed is False
        assert any("tool use: yes [legacy_fallback]" in message.text for message in emitted)
        assert any("Tradeoff:" in message.text for message in emitted)

    def test_build_tool_router_uses_configured_os_sandbox_backend(
        self, config, storage, monkeypatch
    ):
        runtime, _emitted = _make_runtime(config, storage)
        config.set("safety.os_sandbox_backend", "off")
        config.set("safety.os_sandbox_container_image", "python:3.12-slim")
        config.set("safety.sandbox_network_access", "allow")
        config.set("safety.sandbox_writable_roots", "extra-one,extra-two")
        captured: dict[str, object] = {}

        class _Process:
            def run(self, _command: str, _cwd: str, _timeout: int):
                raise AssertionError("not expected to execute in this test")

        import localagentcli.runtime.core as runtime_core

        def _builder(
            *, policy, backend, container_image, container_cpu_limit, container_memory_limit
        ):
            captured["policy"] = policy
            captured["backend"] = backend
            captured["container_image"] = container_image
            captured["container_cpu_limit"] = container_cpu_limit
            captured["container_memory_limit"] = container_memory_limit
            return _Process()

        monkeypatch.setattr(runtime_core, "build_shell_exec_process", _builder)

        runtime._services.build_tool_router(runtime.workspace_root())

        assert captured["backend"] == "off"
        assert str(captured["policy"].posture.value) == "workspace-write"
        assert captured["policy"].network_access is True
        writable_roots = {path.name for path in captured["policy"].writable_roots}
        assert {"extra-one", "extra-two"}.issubset(writable_roots)
        assert captured["container_image"] == "python:3.12-slim"

    def test_build_tool_router_updates_mcp_exec_policy(self, config, storage):
        runtime, _emitted = _make_runtime(config, storage)
        fake_mcp = MagicMock()
        fake_mcp.build_dynamic_tool_specs.return_value = []
        runtime._services.mcp_manager = fake_mcp

        runtime._services.build_tool_router(runtime.workspace_root())

        fake_mcp.update_exec_policy.assert_called_once()
        _, kwargs = fake_mcp.update_exec_policy.call_args
        assert kwargs["os_sandbox_container_image"] == "python:3.12-slim"

    def test_build_tool_router_falls_back_to_local_exec_on_auto_sandbox_setup_error(
        self,
        config,
        storage,
        monkeypatch,
    ):
        runtime, _emitted = _make_runtime(config, storage)
        config.set("safety.os_sandbox_backend", "auto")

        import localagentcli.runtime.core as runtime_core

        def _raise(
            *,
            policy,
            backend,
            container_image,
            container_cpu_limit,
            container_memory_limit,
        ):
            raise RuntimeError("sandbox backend unavailable")

        monkeypatch.setattr(runtime_core, "build_shell_exec_process", _raise)

        router = runtime._services.build_tool_router(runtime.workspace_root())
        shell_tool = router.get_tool("shell_execute")

        assert shell_tool is not None
        assert isinstance(shell_tool._exec_process, LocalExecProcess)

    def test_build_tool_router_raises_on_explicit_sandbox_setup_error(
        self,
        config,
        storage,
        monkeypatch,
    ):
        runtime, _emitted = _make_runtime(config, storage)
        config.set("safety.os_sandbox_backend", "macos-seatbelt")

        import localagentcli.runtime.core as runtime_core

        def _raise(
            *,
            policy,
            backend,
            container_image,
            container_cpu_limit,
            container_memory_limit,
        ):
            raise RuntimeError("sandbox backend unavailable")

        monkeypatch.setattr(runtime_core, "build_shell_exec_process", _raise)

        with pytest.raises(RuntimeError, match="explicit OS sandbox backend"):
            runtime._services.build_tool_router(runtime.workspace_root())


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
