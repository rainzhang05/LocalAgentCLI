"""Tests for /mode command handlers."""

from __future__ import annotations

from localagentcli.commands.mode import register as register_mode
from localagentcli.commands.router import CommandRouter
from localagentcli.models.registry import ModelEntry, ModelRegistry
from localagentcli.providers.base import RemoteModelInfo
from localagentcli.providers.keys import KeyManager
from localagentcli.providers.registry import ProviderEntry, ProviderRegistry


def _make_router(config, session_manager, storage):
    router = CommandRouter()
    model_registry = ModelRegistry(storage.registry_path)
    key_manager = KeyManager(storage.secrets_dir)
    key_manager._keyring_available = False
    provider_registry = ProviderRegistry(config, key_manager)
    register_mode(router, session_manager, model_registry, provider_registry)
    return router, model_registry, provider_registry


class TestModeCommands:
    def test_mode_chat_switches_session(self, config, session_manager, storage):
        router, _, _ = _make_router(config, session_manager, storage)
        session_manager.current.mode = "agent"

        result = router.dispatch("mode chat")

        assert result.success
        assert session_manager.current.mode == "chat"

    def test_mode_agent_allows_empty_target(self, config, session_manager, storage):
        router, _, _ = _make_router(config, session_manager, storage)
        session_manager.current.model = ""
        session_manager.current.provider = ""

        result = router.dispatch("mode agent")

        assert result.success
        assert "Configure a tool-capable model or provider" in result.message
        assert session_manager.current.mode == "agent"

    def test_mode_agent_rejects_local_model_without_tools(self, config, session_manager, storage):
        router, model_registry, _ = _make_router(config, session_manager, storage)
        model_registry.register(
            ModelEntry(
                name="local-model",
                version="v1",
                format="gguf",
                path=str(storage.models_dir / "local-model" / "v1"),
                capabilities={"tool_use": False, "reasoning": False, "streaming": True},
            )
        )
        session_manager.current.model = "local-model@v1"
        session_manager.current.provider = ""

        result = router.dispatch("mode agent")

        assert not result.success
        assert "tool use: no [verified]" in result.message

    def test_mode_agent_allows_tool_capable_provider(self, config, session_manager, storage):
        router, _, provider_registry = _make_router(config, session_manager, storage)
        provider_registry.add(
            ProviderEntry(
                name="openai",
                type="openai",
                base_url="https://api.openai.com/v1",
                default_model="gpt-4o",
            ),
            api_key="test-key",
        )
        session_manager.current.provider = "openai"
        session_manager.current.model = "gpt-4o"
        provider_registry.create_provider = lambda _name: type(  # type: ignore[method-assign]
            "FakeProvider",
            (),
            {
                "set_active_model": staticmethod(lambda _model: None),
                "list_models": staticmethod(
                    lambda: [
                        RemoteModelInfo(
                            id="gpt-4o",
                            name="GPT-4o",
                            capabilities={"tool_use": True, "reasoning": False, "streaming": True},
                            capability_provenance={
                                "tool_use": {
                                    "tier": "inferred",
                                    "reason": (
                                        "Capabilities are inferred from the provider model id."
                                    ),
                                },
                                "reasoning": {
                                    "tier": "inferred",
                                    "reason": (
                                        "Capabilities are inferred from the provider model id."
                                    ),
                                },
                                "streaming": {
                                    "tier": "inferred",
                                    "reason": (
                                        "Capabilities are inferred from the provider model id."
                                    ),
                                },
                            },
                            selection_state="api_discovered",
                        )
                    ]
                ),
                "capabilities": staticmethod(
                    lambda: {"tool_use": True, "reasoning": False, "streaming": True}
                ),
                "close": staticmethod(lambda: None),
            },
        )()

        result = router.dispatch("mode agent")

        assert result.success
        assert session_manager.current.mode == "agent"

    def test_mode_agent_rejects_legacy_fallback_provider(self, config, session_manager, storage):
        router, _, provider_registry = _make_router(config, session_manager, storage)
        provider_registry.add(
            ProviderEntry(
                name="openai",
                type="openai",
                base_url="https://api.openai.com/v1",
                default_model="gpt-4o",
            ),
            api_key="test-key",
        )
        session_manager.current.provider = "openai"
        session_manager.current.model = "gpt-4o"

        result = router.dispatch("mode agent")

        assert not result.success
        assert "legacy fallback" in result.message
        assert "API-discovered model" in result.message
