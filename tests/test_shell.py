"""Tests for localagentcli.shell (prompt and ui)."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

from prompt_toolkit.utils import Event
from rich.panel import Panel
from rich.text import Text

from localagentcli.agents.events import ToolCallRequested
from localagentcli.commands.router import CommandResult, CommandRouter
from localagentcli.models.backends.base import StreamChunk
from localagentcli.models.registry import ModelEntry, ModelRegistry
from localagentcli.runtime import ApprovalDecisionOp, RuntimeEvent
from localagentcli.shell import prompt as prompt_module
from localagentcli.shell.notifications import ShellNotification
from localagentcli.shell.prompt import (
    ACTION_PROMPT_TOOLBAR,
    COMMAND_MENU_HEIGHT,
    SECRET_PROMPT_TOOLBAR,
    TEXT_PROMPT_TOOLBAR,
    CommandCompleter,
    SelectionOption,
    _has_command_matches,
    _has_selection_matches,
    _refresh_command_completion,
    _refresh_selection_completion,
    _wire_live_completion_menu,
    confirm_choice,
    create_prompt_session,
    get_prompt_history_strings,
    prompt_action,
    prompt_secret,
    prompt_text,
)
from localagentcli.shell.ui import ShellUI


class TestCommandCompleter:
    """Tests for tab completion."""

    def test_completes_slash_commands(self):
        router = CommandRouter()
        from tests.test_command_router import StubHandler

        router.register("help", StubHandler())
        router.register("exit", StubHandler())

        completer = CommandCompleter(router)
        doc = MagicMock()
        doc.text_before_cursor = "/he"
        completions = list(completer.get_completions(doc, None))
        texts = [c.text for c in completions]
        assert "/help" in texts

    def test_no_completions_without_slash(self):
        router = CommandRouter()
        completer = CommandCompleter(router)
        doc = MagicMock()
        doc.text_before_cursor = "he"
        completions = list(completer.get_completions(doc, None))
        assert completions == []

    def test_all_commands_for_slash_only(self):
        router = CommandRouter()
        from tests.test_command_router import StubHandler

        router.register("help", StubHandler())
        router.register("exit", StubHandler())
        router.register("session", StubHandler(), visible_in_menu=False)

        completer = CommandCompleter(router)
        doc = MagicMock()
        doc.text_before_cursor = "/"
        completions = list(completer.get_completions(doc, None))
        assert len(completions) == 2
        assert {completion.text for completion in completions} == {"/help", "/exit"}

    def test_includes_command_help_as_completion_metadata(self):
        router = CommandRouter()
        from tests.test_command_router import StubHandler

        router.register("help", StubHandler())

        completer = CommandCompleter(router)
        doc = MagicMock()
        doc.text_before_cursor = "/h"
        completion = next(iter(completer.get_completions(doc, None)))

        assert completion.display_meta_text == "Stub: ok"

    def test_command_match_helper_tracks_shorter_prefixes(self):
        router = CommandRouter()
        from tests.test_command_router import StubHandler

        router.register("help", StubHandler())
        router.register("exit", StubHandler())

        assert _has_command_matches(router, "/he") is True
        assert _has_command_matches(router, "/h") is True
        assert _has_command_matches(router, "/hex") is False

    def test_refresh_command_completion_cancels_only_without_matches(self):
        router = CommandRouter()
        from tests.test_command_router import StubHandler

        router.register("help", StubHandler())
        buffer = MagicMock()
        buffer.document.text_before_cursor = "/he"

        _refresh_command_completion(buffer, router)

        buffer.start_completion.assert_called_once()
        buffer.cancel_completion.assert_not_called()

        buffer.start_completion.reset_mock()
        buffer.document.text_before_cursor = "/hex"
        _refresh_command_completion(buffer, router)
        buffer.cancel_completion.assert_called_once()

    def test_selection_match_helper_tracks_shorter_prefixes(self):
        options = [
            SelectionOption(
                value="openai",
                label="OpenAI",
                aliases=("remote",),
            ),
            SelectionOption(
                value="anthropic",
                label="Anthropic",
                aliases=("claude",),
            ),
        ]

        assert _has_selection_matches(options, "op") is True
        assert _has_selection_matches(options, "o") is True
        assert _has_selection_matches(options, "") is True
        assert _has_selection_matches(options, "zzz") is False

    def test_refresh_selection_completion_cancels_only_without_matches(self):
        options = [
            SelectionOption(value="openai", label="OpenAI"),
            SelectionOption(value="anthropic", label="Anthropic"),
        ]
        buffer = MagicMock()
        buffer.document.text_before_cursor = "open"

        _refresh_selection_completion(buffer, options)

        buffer.start_completion.assert_called_once()
        buffer.cancel_completion.assert_not_called()

        buffer.start_completion.reset_mock()
        buffer.document.text_before_cursor = "zzz"
        _refresh_selection_completion(buffer, options)
        buffer.cancel_completion.assert_called_once()


class TestCreatePromptSession:
    """Tests for prompt session creation."""

    def test_creates_session(self, storage):
        router = CommandRouter()
        history_file = storage.cache_dir / "test_history"
        session = create_prompt_session(router, history_file)
        assert session is not None

    def test_creates_parent_directory(self, storage):
        router = CommandRouter()
        history_file = storage.cache_dir / "subdir" / "history"
        create_prompt_session(router, history_file)
        assert history_file.parent.exists()

    def test_restores_history_strings(self):
        router = CommandRouter()
        session = create_prompt_session(router, ["/status", "hello"])
        assert get_prompt_history_strings(session) == ["/status", "hello"]

    @patch("localagentcli.shell.prompt._supports_interactive_prompt", return_value=True)
    @patch("localagentcli.shell.prompt.PromptSession")
    def test_enables_live_command_menu(self, mock_prompt_session, _mock_supports):
        router = CommandRouter()

        create_prompt_session(router, ["/help"])

        kwargs = mock_prompt_session.call_args.kwargs
        assert kwargs["complete_while_typing"] is True
        assert kwargs["reserve_space_for_menu"] == COMMAND_MENU_HEIGHT
        assert kwargs["key_bindings"] is not None
        assert kwargs["style"] is prompt_module._PROMPT_STYLE

    @patch("localagentcli.shell.prompt._supports_interactive_prompt", return_value=True)
    @patch("localagentcli.shell.prompt.get_terminal_size")
    @patch("localagentcli.shell.prompt.PromptSession")
    def test_narrow_terminal_reduces_command_menu_height(
        self,
        mock_prompt_session,
        mock_terminal_size,
        _mock_supports,
    ):
        router = CommandRouter()
        mock_terminal_size.return_value = os.terminal_size((60, 24))

        create_prompt_session(router, ["/help"])

        kwargs = mock_prompt_session.call_args.kwargs
        assert kwargs["reserve_space_for_menu"] < COMMAND_MENU_HEIGHT

    @patch("localagentcli.shell.prompt._supports_interactive_prompt", return_value=True)
    @patch("localagentcli.shell.prompt.PromptSession")
    def test_wires_dynamic_toolbar_provider(self, mock_prompt_session, _mock_supports):
        router = CommandRouter()

        create_prompt_session(router, ["/help"], toolbar_provider=lambda: "toolbar")

        toolbar = mock_prompt_session.call_args.kwargs["bottom_toolbar"]
        assert callable(toolbar)
        assert toolbar() == "toolbar"

    @patch("localagentcli.shell.prompt._supports_interactive_prompt", return_value=False)
    @patch("localagentcli.shell.prompt.sys.stdin.readline", return_value="/status\n")
    def test_falls_back_without_interactive_terminal(self, _mock_readline, _mock_supports):
        router = CommandRouter()

        session = create_prompt_session(router, ["/help"])
        value = session.prompt("> ")

        assert value == "/status"
        assert get_prompt_history_strings(session) == ["/help", "/status"]


class TestCompletionMenuDebounce:
    """Completion refresh is debounced when a running prompt_toolkit app is available."""

    def test_sync_refresh_when_no_app(self):
        refresher = MagicMock()
        session = MagicMock()
        buffer = MagicMock()
        buffer.on_text_changed = Event(buffer)
        session.default_buffer = buffer

        with patch.object(prompt_module, "get_app_or_none", return_value=None):
            _wire_live_completion_menu(session, refresher)
            buffer.on_text_changed.fire()
            buffer.on_text_changed.fire()

        assert refresher.call_args_list == [
            call(session.default_buffer),
            call(session.default_buffer),
        ]

    def test_burst_text_changes_yield_one_refresher_after_timer(self):
        refresher = MagicMock()

        class _FakeHandle:
            def __init__(self, loop: "_FakeLoop", callback: object) -> None:
                self._loop = loop
                self._callback = callback

            def cancel(self) -> None:
                if self._loop.pending is self:
                    self._loop.pending = None

            def run(self) -> None:
                self._callback()

        class _FakeLoop:
            def __init__(self) -> None:
                self.pending: _FakeHandle | None = None

            def is_closed(self) -> bool:
                return False

            def call_later(self, delay, callback):
                if self.pending is not None:
                    self.pending.cancel()
                self.pending = _FakeHandle(self, callback)
                return self.pending

        fake_app = SimpleNamespace(loop=_FakeLoop())
        session = MagicMock()
        buffer = MagicMock()
        buffer.on_text_changed = Event(buffer)
        session.default_buffer = buffer

        with patch.object(prompt_module, "get_app_or_none", return_value=fake_app):
            _wire_live_completion_menu(session, refresher)
            for _ in range(5):
                buffer.on_text_changed.fire()

        refresher.assert_not_called()
        assert fake_app.loop.pending is not None
        fake_app.loop.pending.run()
        refresher.assert_called_once_with(session.default_buffer)


class TestPromptHelpers:
    """Tests for the shared prompt helper contract."""

    def test_prompt_style_uses_turquoise_text_without_menu_background(self):
        rules = dict(prompt_module._PROMPT_STYLE.style_rules)

        assert rules["completion-menu"] == "bg:default"
        assert "bg:default" in rules["completion-menu.completion"]
        assert "fg:#40E0D0" in rules["completion-menu.completion.current"]
        assert "bg:default" in rules["completion-menu.completion.current"]

    @patch("localagentcli.shell.prompt.supports_interactive_prompt", return_value=True)
    @patch("localagentcli.shell.prompt.PromptSession")
    def test_prompt_text_uses_toolbar_and_default(
        self,
        mock_prompt_session,
        _mock_supports,
    ):
        session = MagicMock()
        session.prompt.return_value = "workspace"
        mock_prompt_session.return_value = session

        value = prompt_text("Workspace directory", default=".")

        assert value == "workspace"
        assert mock_prompt_session.call_args.kwargs["bottom_toolbar"] == TEXT_PROMPT_TOOLBAR
        assert mock_prompt_session.call_args.kwargs["style"] is prompt_module._PROMPT_STYLE
        session.prompt.assert_called_once_with(
            "Workspace directory: ",
            default=".",
            is_password=False,
        )

    @patch("localagentcli.shell.prompt.supports_interactive_prompt", return_value=True)
    @patch("localagentcli.shell.prompt.PromptSession")
    def test_prompt_secret_hides_input(
        self,
        mock_prompt_session,
        _mock_supports,
    ):
        session = MagicMock()
        session.prompt.return_value = "secret"
        mock_prompt_session.return_value = session

        value = prompt_secret("API key")

        assert value == "secret"
        assert mock_prompt_session.call_args.kwargs["bottom_toolbar"] == SECRET_PROMPT_TOOLBAR
        assert mock_prompt_session.call_args.kwargs["style"] is prompt_module._PROMPT_STYLE
        session.prompt.assert_called_once_with(
            "API key: ",
            default="",
            is_password=True,
        )

    @patch("localagentcli.shell.prompt.supports_interactive_prompt", return_value=False)
    @patch("localagentcli.shell.prompt.sys.stdin.readline", return_value="\n")
    def test_prompt_text_falls_back_and_uses_default(self, _mock_readline, _mock_supports):
        value = prompt_text("Workspace directory", default=".")

        assert value == "."

    @patch("localagentcli.shell.prompt.supports_interactive_prompt", return_value=True)
    @patch("localagentcli.shell.prompt.PromptSession")
    def test_prompt_text_returns_none_when_cancelled(
        self,
        mock_prompt_session,
        _mock_supports,
    ):
        session = MagicMock()
        session.prompt.side_effect = KeyboardInterrupt
        mock_prompt_session.return_value = session

        assert prompt_text("Workspace directory", default=".") is None

    @patch("localagentcli.shell.prompt.supports_interactive_prompt", return_value=True)
    @patch("localagentcli.shell.prompt._prompt_supports_in_thread", return_value=True)
    @patch("localagentcli.shell.prompt.PromptSession")
    def test_select_option_uses_in_thread_inside_running_loop(
        self,
        mock_prompt_session,
        _mock_supports_in_thread,
        _mock_supports,
    ):
        session = MagicMock()
        buffer = MagicMock()
        buffer.on_text_changed = Event(buffer)
        session.default_buffer = buffer
        session.prompt.return_value = "openai"
        mock_prompt_session.return_value = session

        async def _invoke():
            return prompt_module.select_option(
                "Choose provider",
                [SelectionOption(value="openai", label="OpenAI")],
            )

        selection = asyncio.run(_invoke())

        assert selection is not None
        assert selection.value == "openai"
        assert mock_prompt_session.call_args.kwargs["style"] is prompt_module._PROMPT_STYLE
        assert session.prompt.call_args.kwargs["in_thread"] is True

    @patch("localagentcli.shell.prompt.supports_interactive_prompt", return_value=True)
    @patch("localagentcli.shell.prompt._prompt_supports_in_thread", return_value=True)
    @patch("localagentcli.shell.prompt.PromptSession")
    def test_prompt_text_uses_in_thread_inside_running_loop(
        self,
        mock_prompt_session,
        _mock_supports_in_thread,
        _mock_supports,
    ):
        session = MagicMock()
        session.prompt.return_value = "workspace"
        mock_prompt_session.return_value = session

        async def _invoke():
            return prompt_text("Workspace directory", default=".")

        value = asyncio.run(_invoke())

        assert value == "workspace"
        assert session.prompt.call_args.kwargs["in_thread"] is True

    @patch("localagentcli.shell.prompt.select_option")
    def test_prompt_action_uses_action_toolbar(self, mock_select):
        mock_select.return_value = SelectionOption(value="approve", label="Approve")

        value = prompt_action(
            "Choose action",
            [SelectionOption(value="approve", label="Approve")],
            default="approve",
        )

        assert value is not None
        mock_select.assert_called_once_with(
            "Choose action",
            [SelectionOption(value="approve", label="Approve")],
            default="approve",
            bottom_toolbar=ACTION_PROMPT_TOOLBAR,
        )

    @patch("localagentcli.shell.prompt.prompt_action")
    def test_confirm_choice_maps_yes_no_and_cancel(self, mock_prompt_action):
        mock_prompt_action.side_effect = [
            SelectionOption(value="yes", label="Yes"),
            SelectionOption(value="no", label="No"),
            None,
        ]

        assert confirm_choice("Continue?") is True
        assert confirm_choice("Continue?") is False
        assert confirm_choice("Continue?") is None

    @patch("localagentcli.shell.prompt.supports_interactive_prompt", return_value=True)
    @patch("localagentcli.shell.prompt.get_terminal_size")
    @patch("localagentcli.shell.prompt.PromptSession")
    def test_narrow_terminal_reduces_selection_menu_height(
        self,
        mock_prompt_session,
        mock_terminal_size,
        _mock_supports,
    ):
        mock_terminal_size.return_value = os.terminal_size((58, 24))
        session = MagicMock()
        session.prompt.side_effect = KeyboardInterrupt
        mock_prompt_session.return_value = session

        prompt_module.select_option(
            "Choose",
            [SelectionOption(value="a", label="alpha")],
        )

        kwargs = mock_prompt_session.call_args.kwargs
        assert kwargs["reserve_space_for_menu"] < prompt_module.CHOICE_MENU_HEIGHT


class TestShellUIInit:
    """Tests for ShellUI construction."""

    def test_creates_successfully(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        assert ui._session_manager.current is not None
        assert ui._router is not None

    def test_first_run_flag(self, config, storage):
        ui = ShellUI(config=config, storage=storage, first_run=True)
        assert ui._first_run is True

    def test_sync_workspace_instruction_detects_agents_file(
        self,
        config,
        storage,
        tmp_path: Path,
    ):
        repo_root = tmp_path / "repo"
        workspace = repo_root / "src"
        workspace.mkdir(parents=True)
        (repo_root / ".git").mkdir()
        agents_path = repo_root / "AGENTS.md"
        agents_path.write_text("Use repository defaults.", encoding="utf-8")

        ui = ShellUI(config=config, storage=storage)
        ui._session_manager.current.workspace = str(workspace)

        ui._sync_workspace_instruction()

        assert ui._session_manager.current.metadata["workspace_instruction"] == (
            "Use repository defaults."
        )
        assert ui._session_manager.current.metadata["workspace_instruction_path"] == str(
            agents_path
        )

    def test_registers_all_commands(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        commands = ui._router.get_commands()
        assert "help" in commands
        assert "exit" in commands
        assert "status" in commands
        assert "config" in commands
        assert "setup" in commands
        assert "hf-token" in commands
        assert "session save" in commands
        assert "session load" in commands
        assert "providers list" in commands
        assert "providers add" in commands
        assert "providers remove" in commands
        assert "providers use" in commands
        assert "providers test" in commands
        assert "set" in commands
        assert "set default" in commands
        assert "mode chat" in commands
        assert "mode agent" in commands

    def test_reuses_agent_controller_for_same_target(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        fake_backend = MagicMock()
        model = MagicMock()
        model.backend = fake_backend

        first = ui._get_or_create_agent_controller(model)
        second = ui._get_or_create_agent_controller(model)

        assert first is second


class TestShellUIRenderResult:
    """Tests for command result rendering."""

    def test_render_success(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        ui._console = MagicMock()
        result = CommandResult.ok("All good")
        ui._render_command_result(result)
        ui._console.print.assert_called_once_with("All good")

    def test_render_exit_suppressed(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        ui._console = MagicMock()
        result = CommandResult.ok("exit", data={"action": "exit"})
        ui._render_command_result(result)
        ui._console.print.assert_not_called()

    def test_render_error(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        ui._console = MagicMock()
        ui._stream_renderer = MagicMock()
        result = CommandResult.error("Something failed")
        ui._render_command_result(result)
        ui._stream_renderer.render_error.assert_called_once_with("Something failed")

    def test_render_success_presentation_uses_stream_renderer(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        ui._console = MagicMock()
        ui._stream_renderer = MagicMock()

        ui._render_command_result(CommandResult.ok("Saved.", presentation="success"))

        ui._stream_renderer.render_success.assert_called_once_with("Saved.")

    def test_render_body_prints_after_presented_message(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        ui._console = MagicMock()
        ui._stream_renderer = MagicMock()

        ui._render_command_result(
            CommandResult.ok(
                "Configured.",
                presentation="status",
                body="Details follow.",
            )
        )

        ui._stream_renderer.render_status.assert_called_once_with("Configured.")
        ui._console.print.assert_called_once_with("Details follow.")


class TestShellUIStatusToolbar:
    """Tests for prompt-time status rendering."""

    def test_prompt_toolbar_shows_mode_target_and_workspace(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        toolbar = ui._prompt_toolbar_text()
        assert "mode: agent" in toolbar
        assert "target: (none)" in toolbar
        assert "workspace:" in toolbar

    def test_prompt_toolbar_includes_agent_state_and_undo_count(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        ui._session_manager.current.metadata["agent_task_state"] = {
            "route": "multi_step_task",
            "phase": "waiting_approval",
            "pending_tool": "patch_apply",
            "rollback_count": 2,
        }

        toolbar = ui._prompt_toolbar_text()

        assert "agent: multi-step task/waiting approval/patch_apply" in toolbar
        assert "undo: 2" in toolbar

    def test_prompt_toolbar_shows_retrying_state_with_count(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        ui._session_manager.current.metadata["agent_task_state"] = {
            "route": "multi_step_task",
            "phase": "retrying",
            "retry_count": 2,
            "wait_reason": "retrying after recent failure",
        }

        toolbar = ui._prompt_toolbar_text()

        assert "agent: multi-step task/retrying/retry 2" in toolbar

    def test_active_target_label_for_provider(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        ui._session_manager.current.provider = "openai"
        ui._session_manager.current.model = "gpt-4.1"

        assert ui._active_target_label() == "openai (gpt-4.1)"

    def test_active_target_label_shows_local_format_from_registry(
        self, config, storage, tmp_path: Path
    ):
        ui = ShellUI(config=config, storage=storage)
        model_dir = tmp_path / "model"
        model_dir.mkdir()
        ui._model_registry.register(
            ModelEntry(
                name="demo",
                version="v1",
                format="gguf",
                path=str(model_dir),
                metadata={},
            )
        )
        ui._session_manager.current.model = "demo@v1"

        assert ui._active_target_label() == "demo@v1 (gguf)"

    def test_prompt_toolbar_repeated_calls_skip_model_detection(
        self, config, storage, tmp_path: Path
    ):
        ui = ShellUI(config=config, storage=storage)
        model_dir = tmp_path / "model"
        model_dir.mkdir()
        ui._model_registry.register(
            ModelEntry(
                name="demo",
                version="v1",
                format="gguf",
                path=str(model_dir),
                metadata={},
            )
        )
        ui._session_manager.current.model = "demo@v1"
        ui._model_detector.detect = MagicMock()

        for _ in range(25):
            ui._prompt_toolbar_text()

        ui._model_detector.detect.assert_not_called()

    def test_display_welcome_uses_unified_text_style(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        ui._console = MagicMock()

        ui._display_welcome()

        banner = ui._console.print.call_args_list[1][0][0]
        assert isinstance(banner, Panel)
        assert str(banner.title) == "LocalAgent CLI v0.1.0"
        assert "Mode:" in str(banner.renderable)

    def test_display_welcome_falls_back_when_startup_banner_disabled(self, config, storage):
        config.set("shell.startup_banner", False)
        ui = ShellUI(config=config, storage=storage)
        ui._console = MagicMock()

        ui._display_welcome()

        title = ui._console.print.call_args_list[1][0][0]
        assert isinstance(title, Text)
        assert title.plain == "LocalAgent CLI v0.1.0"

    def test_context_limit_uses_registered_model_metadata(self, config, storage, tmp_path: Path):
        ui = ShellUI(config=config, storage=storage)
        model_dir = tmp_path / "model"
        model_dir.mkdir()
        ui._model_registry.register(
            ModelEntry(
                name="demo",
                version="v1",
                format="gguf",
                path=str(model_dir),
                metadata={"context_length": 32768},
            )
        )
        ui._session_manager.current.model = "demo@v1"

        assert ui._context_limit() == 32768


class TestShellUIHandleExit:
    """Tests for exit handling."""

    def test_exit_unmodified_session(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        ui._console = MagicMock()
        ui._runtime.aclose = AsyncMock()
        asyncio.run(ui._ahandle_exit_async())
        # Should print goodbye without asking to save
        calls = [str(c) for c in ui._console.print.call_args_list]
        assert any("Goodbye" in c for c in calls)

    @patch("localagentcli.shell.ui.confirm_choice", return_value=False)
    def test_exit_modified_session_decline_save(self, mock_confirm, config, storage):
        ui = ShellUI(config=config, storage=storage)
        ui._console = MagicMock()
        ui._runtime.aclose = AsyncMock()
        from datetime import datetime

        from localagentcli.session.state import Message

        ui._session_manager.current.history.append(
            Message(role="user", content="test", timestamp=datetime.now())
        )
        asyncio.run(ui._ahandle_exit_async())
        mock_confirm.assert_called_once()

    @patch("localagentcli.shell.ui.confirm_choice", return_value=True)
    def test_exit_modified_session_accept_save(self, mock_confirm, config, storage):
        ui = ShellUI(config=config, storage=storage)
        ui._console = MagicMock()
        ui._runtime.aclose = AsyncMock()
        from datetime import datetime

        from localagentcli.session.state import Message

        ui._session_manager.current.history.append(
            Message(role="user", content="test", timestamp=datetime.now())
        )
        asyncio.run(ui._ahandle_exit_async())
        # Session should have been saved
        sessions = ui._session_manager.list_sessions()
        assert len(sessions) == 1


class TestShellUIRun:
    """Tests for the main input loop."""

    def test_exit_command_breaks_loop(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        ui._console = MagicMock()
        ui._prompt_session = MagicMock()
        ui._prompt_session.prompt_async = AsyncMock(return_value="/exit")
        ui.run()
        # Should have called prompt at least once
        ui._prompt_session.prompt_async.assert_awaited()

    def test_empty_input_continues(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        ui._console = MagicMock()
        ui._prompt_session = MagicMock()
        ui._prompt_session.prompt_async = AsyncMock(side_effect=["", "  ", "/exit"])
        ui.run()
        assert ui._prompt_session.prompt_async.await_count == 3

    def test_plain_text_shows_no_model(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        ui._console = MagicMock()
        ui._prompt_session = MagicMock()
        ui._prompt_session.prompt_async = AsyncMock(side_effect=["hello world", "/exit"])
        ui.run()
        calls = [str(c) for c in ui._console.print.call_args_list]
        assert any("No model connected" in c for c in calls)

    def test_agent_mode_plain_text_uses_agent_controller(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        ui._session_manager.current.mode = "agent"
        ui._stream_renderer = MagicMock()
        controller = MagicMock()
        runtime = MagicMock()
        runtime.submit = MagicMock()

        async def _agent_events():
            yield RuntimeEvent(
                type="agent_event",
                submission_id="sub-1",
                data="agent-event",
            )

        runtime.aiter_events = MagicMock(return_value=_agent_events())
        runtime.active_agent_controller = controller
        ui._runtime = runtime

        asyncio.run(ui._ahandle_plain_text("do something"))

        runtime.submit.assert_called_once()
        ui._stream_renderer.render_agent_event.assert_called_once_with("agent-event")

    def test_rebuild_prompt_session_uses_session_history(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        ui._session_manager.current.metadata["input_history"] = ["/status", "hello"]
        ui._rebuild_prompt_session()
        assert get_prompt_history_strings(ui._prompt_session) == ["/status", "hello"]

    def test_keyboard_interrupt_continues(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        ui._console = MagicMock()
        ui._prompt_session = MagicMock()
        ui._prompt_session.prompt_async = AsyncMock(side_effect=[KeyboardInterrupt(), "/exit"])
        ui.run()
        assert ui._prompt_session.prompt_async.await_count == 2

    def test_double_keyboard_interrupt_exits(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        ui._console = MagicMock()
        ui._prompt_session = MagicMock()
        _two_interrupts = [KeyboardInterrupt(), KeyboardInterrupt()]
        ui._prompt_session.prompt_async = AsyncMock(side_effect=_two_interrupts)
        ui.run()

        assert ui._prompt_session.prompt_async.await_count == 2
        calls = [call.args[0] for call in ui._console.print.call_args_list if call.args]
        assert any("Press Ctrl+C again" in str(call) for call in calls)
        assert any("Goodbye" in str(call) for call in calls)

    @patch("localagentcli.shell.ui.confirm_choice")
    def test_double_keyboard_interrupt_exits_without_save_prompt(
        self, mock_confirm, config, storage
    ):
        ui = ShellUI(config=config, storage=storage)
        ui._console = MagicMock()
        ui._prompt_session = MagicMock()
        _two_interrupts = [KeyboardInterrupt(), KeyboardInterrupt()]
        ui._prompt_session.prompt_async = AsyncMock(side_effect=_two_interrupts)
        from datetime import datetime

        from localagentcli.session.state import Message

        ui._session_manager.current.history.append(
            Message(role="user", content="changed", timestamp=datetime.now())
        )

        ui.run()

        mock_confirm.assert_not_called()

    def test_eof_exits(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        ui._console = MagicMock()
        ui._prompt_session = MagicMock()
        ui._prompt_session.prompt_async = AsyncMock(side_effect=EOFError())
        ui.run()

    def test_command_dispatch(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        ui._console = MagicMock()
        ui._prompt_session = MagicMock()
        ui._prompt_session.prompt_async = AsyncMock(side_effect=["/status", "/exit"])
        ui.run()
        calls = [str(c) for c in ui._console.print.call_args_list]
        assert any("Mode:" in c for c in calls)

    def test_command_exception_renders_error_and_continues(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        ui._console = MagicMock()
        ui._stream_renderer = MagicMock()
        ui._prompt_session = MagicMock()
        ui._prompt_session.prompt_async = AsyncMock(side_effect=["/status", "/exit"])

        with patch.object(ui._router, "dispatch") as mock_dispatch:
            mock_dispatch.side_effect = [
                RuntimeError("boom"),
                CommandResult.ok("exit", data={"action": "exit"}),
            ]
            ui.run()

        ui._stream_renderer.render_error.assert_any_call("Command failed: boom")
        assert ui._prompt_session.prompt_async.await_count == 2

    def test_first_run_setup(self, config, storage):
        ui = ShellUI(config=config, storage=storage, first_run=True)
        ui._console = MagicMock()
        ui._prompt_session = MagicMock()
        ui._prompt_session.prompt.side_effect = ["/exit"]
        # Mock the setup wizard to avoid interactive prompts
        with patch.object(ui._router, "dispatch") as mock_dispatch:
            mock_dispatch.return_value = CommandResult.ok("Setup complete.")
            ui._run_first_time_setup()
            mock_dispatch.assert_called_with("setup")

    def test_run_renders_default_target_repair_warning_on_startup(
        self, config, storage, tmp_path: Path
    ):
        config.set("provider.active_provider", "")
        config.set("model.active_model", "missing@v1")
        model_dir = tmp_path / "model"
        model_dir.mkdir()
        ModelRegistry(storage.registry_path).register(
            ModelEntry(
                name="fallback",
                version="v1",
                format="gguf",
                path=str(model_dir),
            )
        )

        ui = ShellUI(config=config, storage=storage)
        ui._console = MagicMock()
        ui._stream_renderer = MagicMock()
        ui._prompt_session = MagicMock()
        ui._prompt_session.prompt_async = AsyncMock(return_value="/exit")

        ui.run()

        ui._stream_renderer.render_warning.assert_any_call(
            "Default target repaired: missing@v1 was unavailable, so LocalAgentCLI "
            "switched to fallback@v1."
        )

    def test_session_change_resets_runtime_state(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        ui._console = MagicMock()
        ui._prompt_session = MagicMock()
        ui._prompt_session.prompt_async = AsyncMock(side_effect=["/session load demo", "/exit"])
        ui._execution_runtime = MagicMock()
        old_runtime = MagicMock()
        old_runtime.aclose = AsyncMock()
        ui._runtime = old_runtime
        ui._agent_controller = MagicMock()
        ui._rebuild_prompt_session = MagicMock()
        ui._build_session_runtime = MagicMock(return_value=MagicMock())
        ui._sync_workspace_instruction = MagicMock()
        ui._render_default_target_warning = MagicMock()
        ui._ahandle_exit_async = AsyncMock()

        with (
            patch.object(ui._router, "dispatch") as mock_dispatch,
            patch("localagentcli.shell.ui.SessionExecutionRuntime", return_value=MagicMock()),
        ):
            mock_dispatch.side_effect = [
                CommandResult.ok("loaded", data={"action": "session_changed"}),
                CommandResult.ok("exit", data={"action": "exit"}),
            ]
            ui.run()

        old_runtime.aclose.assert_awaited_once()
        ui._build_session_runtime.assert_called_once()
        assert ui._agent_controller is None
        ui._rebuild_prompt_session.assert_called_once()
        assert ui._sync_workspace_instruction.call_count >= 2
        assert ui._render_default_target_warning.call_count >= 1


class TestShellUIModelResolution:
    def test_resolve_active_model_uses_provider_backend(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        resolved = MagicMock()
        ui._execution_runtime.resolve_active_model = MagicMock(return_value=resolved)

        model = ui._resolve_active_model()

        assert model is resolved
        ui._execution_runtime.resolve_active_model.assert_called_once()

    def test_resolve_active_model_reports_local_load_failure(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        ui._execution_runtime.resolve_active_model = MagicMock(return_value=None)

        model = ui._resolve_active_model()

        assert model is None
        ui._execution_runtime.resolve_active_model.assert_called_once()

    def test_ensure_backend_dependencies_installs_missing_packages(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        ui._console = MagicMock()

        with (
            patch(
                "localagentcli.runtime.core.check_backend_dependencies",
                return_value=(False, ["llama_cpp"]),
            ),
            patch("localagentcli.shell.ui.confirm_choice", return_value=True) as mock_confirm,
            patch(
                "localagentcli.runtime.core.install_backend_dependencies",
                return_value=(True, "installed"),
            ) as mock_install,
        ):
            ui._stream_renderer = MagicMock()
            result = ui._ensure_backend_dependencies("gguf")

        assert result is True
        mock_confirm.assert_called_once()
        mock_install.assert_called_once_with("gguf")
        ui._stream_renderer.render_notification.assert_has_calls(
            [
                call(
                    ShellNotification(
                        level="status",
                        message="Installing GGUF backend dependencies...",
                    )
                ),
                call(
                    ShellNotification(
                        level="success",
                        message="GGUF backend dependencies installed.",
                    )
                ),
            ]
        )

    def test_ensure_backend_dependencies_handles_cancelled_prompt(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        ui._console = MagicMock()

        with (
            patch(
                "localagentcli.runtime.core.check_backend_dependencies",
                return_value=(False, ["llama_cpp"]),
            ),
            patch(
                "localagentcli.shell.ui.confirm_choice",
                return_value=None,
            ),
        ):
            ui._stream_renderer = MagicMock()
            result = ui._ensure_backend_dependencies("gguf")

        assert result is False
        ui._stream_renderer.render_notification.assert_called_once_with(
            ShellNotification(level="warning", message="GGUF backend loading cancelled.")
        )

    def test_generation_options_include_selected_provider_model(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        ui._session_manager.current.provider = "openai"
        ui._session_manager.current.model = "gpt-5.4-nano"

        options = ui._generation_options()

        assert options["model"] == "gpt-5.4-nano"

    @patch("localagentcli.models.detector.platform")
    def test_refresh_model_entry_repairs_stale_registry_format_on_non_macos(
        self,
        mock_platform,
        config,
        storage,
        tmp_path: Path,
    ):
        mock_platform.system.return_value = "Linux"
        mock_platform.machine.return_value = "x86_64"
        ui = ShellUI(config=config, storage=storage)
        model_dir = tmp_path / "model"
        model_dir.mkdir()
        (model_dir / "model.safetensors").write_bytes(b"\x00" * 100)
        (model_dir / "config.json").write_text(
            json.dumps({"model_type": "gemma3", "quantization": {"group_size": 64, "bits": 4}})
        )
        ui._model_registry.register(
            ModelEntry(
                name="demo",
                version="v1",
                format="safetensors",
                path=str(model_dir),
                metadata={"backend": "safetensors"},
            )
        )

        entry = ui._refresh_model_entry("demo", "v1")

        assert entry is not None
        assert entry.format == "mlx"
        assert entry.metadata["backend"] == "mlx"


class TestShellUIHelpers:
    def test_prompt_for_tool_approval_flushes_details_before_prompt(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        ui._stream_renderer = MagicMock()
        event = ToolCallRequested(
            tool_name="patch_apply",
            arguments={"path": "file.py", "old_text": "old", "new_text": "new"},
            requires_approval=True,
        )

        with patch(
            "localagentcli.shell.ui.prompt_action",
            return_value=SelectionOption(value="approve", label="Approve"),
        ):
            result = ui._prompt_for_tool_approval(event)

        assert result == "approve"
        assert ui._stream_renderer.method_calls[:2] == [
            call.flush_pending_details(),
            call.render_approval_prompt(),
        ]

    def test_prompt_for_tool_approval_preview_flow_for_patch_apply(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        ui._stream_renderer = MagicMock()
        event = ToolCallRequested(
            tool_name="patch_apply",
            arguments={"path": "file.py", "old_text": "old", "new_text": "new"},
            requires_approval=True,
        )

        with patch(
            "localagentcli.shell.ui.prompt_action",
            side_effect=[
                SelectionOption(value="details", label="View details"),
                SelectionOption(value="approve", label="Approve"),
            ],
        ):
            _ = ui._prompt_for_tool_approval(event)

        preview_call = ui._stream_renderer.render_preview.call_args
        assert preview_call.args[0] == "patch_apply preview"
        assert "Replace" in preview_call.args[1]
        assert "With" in preview_call.args[1]

    def test_prompt_for_tool_approval_preview_flow_for_file_write(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        ui._stream_renderer = MagicMock()
        event = ToolCallRequested(
            tool_name="file_write",
            arguments={"path": "file.py", "content": "a" * 600},
            requires_approval=True,
        )

        with patch(
            "localagentcli.shell.ui.prompt_action",
            side_effect=[
                SelectionOption(value="details", label="View details"),
                SelectionOption(value="approve", label="Approve"),
            ],
        ):
            result = ui._prompt_for_tool_approval(event)

        assert result == "approve"
        preview_call = ui._stream_renderer.render_preview.call_args
        assert preview_call.args[0] == "file_write preview"
        assert "file.py" in preview_call.args[1]
        assert "Content preview (truncated):" in preview_call.args[1]
        assert "```python" in preview_call.args[1]
        assert "..." in preview_call.args[1]

    def test_format_tool_preview_for_patch_apply(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        event = ToolCallRequested(
            tool_name="patch_apply",
            arguments={"path": "file.py", "old_text": "old", "new_text": "new"},
            requires_approval=True,
        )

        preview = ui._format_tool_preview(event)

        assert "Action: patch existing file" in preview
        assert "Replace" in preview
        assert "With" in preview
        assert "Unified diff:" in preview
        assert "```diff" in preview

    def test_format_tool_preview_for_patch_apply_truncates_large_blocks(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        event = ToolCallRequested(
            tool_name="patch_apply",
            arguments={
                "path": "file.py",
                "old_text": "a" * 700,
                "new_text": "b" * 700,
            },
            requires_approval=True,
        )

        preview = ui._format_tool_preview(event)

        assert "Replace (truncated):" in preview
        assert "With (truncated):" in preview
        assert "Unified diff" in preview
        assert "```diff" in preview
        assert "..." in preview

    def test_format_tool_preview_truncates_file_write(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        event = ToolCallRequested(
            tool_name="file_write",
            arguments={"path": "file.py", "content": "a" * 600},
            requires_approval=True,
        )

        preview = ui._format_tool_preview(event)

        assert "Content preview (truncated):" in preview
        assert "```python" in preview
        assert "..." in preview

    def test_format_tool_preview_for_shell_execute_includes_command_and_cwd(
        self,
        config,
        storage,
    ):
        ui = ShellUI(config=config, storage=storage)
        event = ToolCallRequested(
            tool_name="shell_execute",
            arguments={"command": "pytest -q", "working_dir": "src"},
            requires_approval=True,
            risk_level="high",
            risk_reason="Command matches a high-risk pattern: pytest",
            rollback_summary="Rollback is not available for this action.",
        )

        preview = ui._format_tool_preview(event)

        assert "Command:" in preview
        assert "pytest -q" in preview
        assert "Working directory: src" in preview
        assert "Rollback is not available" in preview

    def test_format_tool_preview_for_shell_execute_truncates_long_command(
        self,
        config,
        storage,
    ):
        ui = ShellUI(config=config, storage=storage)
        event = ToolCallRequested(
            tool_name="shell_execute",
            arguments={"command": "x" * 700, "working_dir": "src"},
            requires_approval=True,
        )

        preview = ui._format_tool_preview(event)

        assert "Command (truncated):" in preview
        assert preview.split("Command (truncated):\n", 1)[1].startswith("x")
        assert "Working directory: src" in preview

    def test_truncate_preview_text_reports_truncation(self, config, storage):
        ui = ShellUI(config=config, storage=storage)

        preview, truncated = ui._truncate_preview_text("a" * 20, limit=10)

        assert truncated is True
        assert preview == ("a" * 10) + "..."

    def test_format_tool_preview_for_git_commit_includes_message_and_files(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        event = ToolCallRequested(
            tool_name="git_commit",
            arguments={"message": "feat: add agent undo", "files": ["app.py", "tests.py"]},
            requires_approval=True,
        )

        preview = ui._format_tool_preview(event)

        assert "Commit message: feat: add agent undo" in preview
        assert "Files: app.py, tests.py" in preview

    def test_handle_agent_resume_approves_with_autonomy(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        controller = MagicMock()
        ui._agent_controller = controller
        runtime = MagicMock()
        runtime.active_submission_id = "sub-1"
        runtime.submit = MagicMock()
        ui._runtime = runtime
        ui._adrain_runtime_events = AsyncMock()

        asyncio.run(
            ui._handle_agent_resume(
                CommandResult.ok(
                    "approved",
                    data={"decision": "approve", "autonomous": True},
                )
            )
        )

        submitted = runtime.submit.call_args.args[0]
        assert isinstance(submitted, ApprovalDecisionOp)
        assert submitted.decision == "approve"
        assert submitted.autonomous is True
        ui._adrain_runtime_events.assert_awaited_once()

    def test_stop_agent_task_with_confirmation_decline(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        controller = MagicMock()
        controller.has_active_task = True
        ui._agent_controller = controller
        ui._stream_renderer = MagicMock()

        with patch("localagentcli.shell.ui.confirm_choice", return_value=False):
            result = ui._stop_agent_task_with_confirmation()

        assert result is False
        controller.stop.assert_not_called()

    def test_stop_agent_task_with_confirmation_accepts_and_warns(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        controller = MagicMock()
        controller.has_active_task = True
        ui._agent_controller = controller
        ui._stream_renderer = MagicMock()

        with patch("localagentcli.shell.ui.confirm_choice", return_value=True):
            result = ui._stop_agent_task_with_confirmation()

        assert result is True
        controller.stop.assert_called_once()
        ui._stream_renderer.render_warning.assert_called_once_with("Agent task stopped.")

    def test_workspace_root_resolves_current_workspace(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        ui._session_manager.current.workspace = "."

        assert ui._workspace_root() == Path(".").resolve()

    def test_handle_plain_text_renders_warning_on_chat_interrupt(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        model = MagicMock()
        ui._session_manager.current.mode = "chat"
        ui._resolve_active_model = MagicMock(return_value=model)
        ui._stream_renderer = MagicMock()
        ui._runtime.submit = MagicMock()

        async def _empty_ainterrupt():
            if False:
                yield  # pragma: no cover

        ui._runtime.ainterrupt = MagicMock(return_value=_empty_ainterrupt())

        async def _boom():
            raise KeyboardInterrupt()

        ui._adrain_runtime_events = MagicMock(side_effect=_boom)

        asyncio.run(ui._ahandle_plain_text("hello"))

        model.cancel.assert_called_once()
        ui._stream_renderer.render_warning.assert_called_once_with("Generation interrupted.")

    def test_handle_plain_text_renders_warning_on_agent_interrupt(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        model = MagicMock()
        controller = MagicMock()
        ui._session_manager.current.mode = "agent"
        ui._resolve_active_model = MagicMock(return_value=model)
        ui._runtime.submit = MagicMock()

        async def _empty_ainterrupt_agent():
            if False:
                yield  # pragma: no cover

        ui._runtime.ainterrupt = MagicMock(return_value=_empty_ainterrupt_agent())

        async def _boom_agent():
            raise KeyboardInterrupt()

        ui._adrain_runtime_events = MagicMock(side_effect=_boom_agent)
        ui._agent_controller = controller
        ui._stream_renderer = MagicMock()

        asyncio.run(ui._ahandle_plain_text("do something"))

        model.cancel.assert_called_once()
        controller.stop.assert_called_once()
        ui._stream_renderer.render_warning.assert_called_once_with("Agent task interrupted.")


class TestShellUIRuntimeEventRendering:
    def test_turn_completed_direct_answer_renders_success_without_duplicate_body(
        self,
        config,
        storage,
    ):
        ui = ShellUI(config=config, storage=storage)
        ui._stream_renderer = MagicMock()
        ui._console = MagicMock()

        event = RuntimeEvent(
            type="turn_completed",
            submission_id="sub-1",
            data={"mode": "agent", "route": "direct_answer", "final_text": "fresh answer"},
            message="fresh answer",
        )

        asyncio.run(ui._ahandle_runtime_event(event))

        ui._stream_renderer.render_success.assert_called_once_with("Task completed.")
        ui._stream_renderer.flush_pending_details.assert_called_once()
        ui._console.print.assert_not_called()

    def test_turn_completed_planned_agent_renders_summary_body(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        ui._stream_renderer = MagicMock()
        ui._console = MagicMock()

        event = RuntimeEvent(
            type="turn_completed",
            submission_id="sub-1",
            data={"mode": "agent", "route": "multi_step_task", "summary": "All done."},
            message="All done.",
        )

        asyncio.run(ui._ahandle_runtime_event(event))

        ui._stream_renderer.render_success.assert_called_once_with("Task completed.")
        ui._stream_renderer.flush_pending_details.assert_called_once()
        ui._stream_renderer.render_markdown_message.assert_called_once_with("All done.")
        ui._console.print.assert_not_called()

    def test_adrain_runtime_events_finalizes_renderer_between_turns(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        ui._stream_renderer = MagicMock()

        async def _events():
            yield RuntimeEvent(
                type="stream_chunk",
                submission_id="sub-1",
                data=StreamChunk(text="partial", kind="final_text"),
            )
            yield RuntimeEvent(
                type="turn_completed",
                submission_id="sub-1",
                data={"mode": "chat", "final_text": "partial"},
            )

        ui._runtime = MagicMock()
        ui._runtime.aiter_events = MagicMock(return_value=_events())

        asyncio.run(ui._adrain_runtime_events())

        ui._stream_renderer.finalize.assert_called_once()

    def test_adrain_runtime_events_starts_and_stops_thinking_indicator(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        ui._stream_renderer = MagicMock()

        async def _events():
            yield RuntimeEvent(
                type="turn_completed",
                submission_id="sub-1",
                data={"mode": "chat", "final_text": "ok"},
            )

        ui._runtime = MagicMock()
        ui._runtime.aiter_events = MagicMock(return_value=_events())

        asyncio.run(ui._adrain_runtime_events())

        ui._stream_renderer.start_thinking_indicator.assert_called_once()
        ui._stream_renderer.stop_thinking_indicator.assert_called_once()
