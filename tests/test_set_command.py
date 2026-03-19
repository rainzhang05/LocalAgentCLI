"""Tests for the unified /set command."""

from __future__ import annotations

from collections import deque

from rich.console import Console

from localagentcli.commands.router import CommandRouter
from localagentcli.commands.set_cmd import SetHandler
from localagentcli.commands.set_cmd import register as register_set
from localagentcli.models.detector import HardwareDetector
from localagentcli.models.registry import ModelEntry, ModelRegistry
from localagentcli.providers.base import RemoteModelInfo
from localagentcli.providers.keys import KeyManager
from localagentcli.providers.registry import ProviderEntry, ProviderRegistry
from localagentcli.shell.prompt import SelectionOption


def _selector_from_values(*values: str):
    queue = deque(values)

    def selector(_message: str, options: list[SelectionOption], default: str | None):
        _ = default
        value = queue.popleft()
        return next(option for option in options if option.value == value)

    return selector


def test_set_activates_local_model(config, session_manager, storage, monkeypatch):
    monkeypatch.setattr("localagentcli.commands.set_cmd.supports_interactive_prompt", lambda: True)
    registry = ModelRegistry(storage.registry_path)
    registry.register(
        ModelEntry(
            name="demo",
            version="v1",
            format="gguf",
            path=str(storage.models_dir / "demo" / "v1"),
            size_bytes=1024,
        )
    )
    handler = SetHandler(
        registry,
        ProviderRegistry(config, KeyManager(storage.secrets_dir)),
        HardwareDetector(),
        session_manager,
        Console(quiet=True),
        selector=_selector_from_values("local", "demo@v1"),
    )

    result = handler.execute([])

    assert result.success
    assert session_manager.current.model == "demo@v1"
    assert session_manager.current.provider == ""


def test_set_activates_provider_model(config, session_manager, storage, monkeypatch):
    monkeypatch.setattr("localagentcli.commands.set_cmd.supports_interactive_prompt", lambda: True)
    key_manager = KeyManager(storage.secrets_dir)
    key_manager._keyring_available = False
    provider_registry = ProviderRegistry(config, key_manager)
    provider_registry.add(
        ProviderEntry(
            name="openai",
            type="openai",
            base_url="https://api.openai.com/v1",
            default_model="gpt-4o",
        ),
        "test-key",
    )
    router = CommandRouter()
    register_set(
        router,
        ModelRegistry(storage.registry_path),
        provider_registry,
        HardwareDetector(),
        session_manager,
        Console(quiet=True),
    )
    handler = router.get_commands()["set"]
    assert isinstance(handler, SetHandler)

    handler._selector = _selector_from_values("provider", "openai", "gpt-4o")  # type: ignore[attr-defined]
    handler._provider_registry.create_provider = lambda _name: type(  # type: ignore[method-assign]
        "FakeProvider",
        (),
        {
            "list_models": staticmethod(
                lambda: [
                    RemoteModelInfo(
                        id="gpt-4o",
                        name="GPT-4o",
                        capabilities={"tool_use": True, "streaming": True},
                    )
                ]
            )
        },
    )()

    result = handler.execute([])

    assert result.success
    assert session_manager.current.provider == "openai"
    assert session_manager.current.model == "gpt-4o"


def test_set_requires_tty_without_interactive_prompt(
    config,
    session_manager,
    storage,
    monkeypatch,
):
    handler = SetHandler(
        ModelRegistry(storage.registry_path),
        ProviderRegistry(config, KeyManager(storage.secrets_dir)),
        HardwareDetector(),
        session_manager,
        Console(quiet=True),
    )
    monkeypatch.setattr("localagentcli.commands.set_cmd.supports_interactive_prompt", lambda: False)

    result = handler.execute([])

    assert result.success
    assert "requires a terminal TTY" in result.message
