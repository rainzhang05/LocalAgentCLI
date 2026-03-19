"""Tests for localagentcli.shell (prompt and ui)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from localagentcli.commands.router import CommandResult, CommandRouter
from localagentcli.shell.prompt import (
    CommandCompleter,
    create_prompt_session,
    get_prompt_history_strings,
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

        completer = CommandCompleter(router)
        doc = MagicMock()
        doc.text_before_cursor = "/"
        completions = list(completer.get_completions(doc, None))
        assert len(completions) == 2


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


class TestShellUIInit:
    """Tests for ShellUI construction."""

    def test_creates_successfully(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        assert ui._session_manager.current is not None
        assert ui._router is not None

    def test_first_run_flag(self, config, storage):
        ui = ShellUI(config=config, storage=storage, first_run=True)
        assert ui._first_run is True

    def test_registers_all_commands(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        commands = ui._router.get_commands()
        assert "help" in commands
        assert "exit" in commands
        assert "status" in commands
        assert "config" in commands
        assert "setup" in commands
        assert "session save" in commands
        assert "session load" in commands
        assert "providers list" in commands
        assert "providers add" in commands
        assert "providers remove" in commands
        assert "providers use" in commands
        assert "providers test" in commands
        assert "mode chat" in commands
        assert "mode agent" in commands


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
        result = CommandResult.error("Something failed")
        ui._render_command_result(result)
        call_args = ui._console.print.call_args[0][0]
        assert "Something failed" in call_args


class TestShellUIStatusHeader:
    """Tests for status header display."""

    def test_displays_mode_and_model(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        ui._console = MagicMock()
        ui._display_status_header()
        call_args = ui._console.print.call_args[0][0]
        assert "mode: agent" in call_args
        assert "(none)" in call_args  # no model set


class TestShellUIHandleExit:
    """Tests for exit handling."""

    def test_exit_unmodified_session(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        ui._console = MagicMock()
        ui._handle_exit()
        # Should print goodbye without asking to save
        calls = [str(c) for c in ui._console.print.call_args_list]
        assert any("Goodbye" in c for c in calls)

    @patch("localagentcli.shell.ui.Confirm.ask", return_value=False)
    def test_exit_modified_session_decline_save(self, mock_confirm, config, storage):
        ui = ShellUI(config=config, storage=storage)
        ui._console = MagicMock()
        from datetime import datetime

        from localagentcli.session.state import Message

        ui._session_manager.current.history.append(
            Message(role="user", content="test", timestamp=datetime.now())
        )
        ui._handle_exit()
        mock_confirm.assert_called_once()

    @patch("localagentcli.shell.ui.Confirm.ask", return_value=True)
    def test_exit_modified_session_accept_save(self, mock_confirm, config, storage):
        ui = ShellUI(config=config, storage=storage)
        ui._console = MagicMock()
        from datetime import datetime

        from localagentcli.session.state import Message

        ui._session_manager.current.history.append(
            Message(role="user", content="test", timestamp=datetime.now())
        )
        ui._handle_exit()
        # Session should have been saved
        sessions = ui._session_manager.list_sessions()
        assert len(sessions) == 1


class TestShellUIRun:
    """Tests for the main input loop."""

    def test_exit_command_breaks_loop(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        ui._console = MagicMock()
        ui._prompt_session = MagicMock()
        ui._prompt_session.prompt.return_value = "/exit"
        ui.run()
        # Should have called prompt at least once
        ui._prompt_session.prompt.assert_called()

    def test_empty_input_continues(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        ui._console = MagicMock()
        ui._prompt_session = MagicMock()
        ui._prompt_session.prompt.side_effect = ["", "  ", "/exit"]
        ui.run()
        assert ui._prompt_session.prompt.call_count == 3

    def test_plain_text_shows_no_model(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        ui._console = MagicMock()
        ui._prompt_session = MagicMock()
        ui._prompt_session.prompt.side_effect = ["hello world", "/exit"]
        ui.run()
        calls = [str(c) for c in ui._console.print.call_args_list]
        assert any("No model connected" in c for c in calls)

    def test_agent_mode_plain_text_shows_phase_message(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        ui._session_manager.current.mode = "agent"
        ui._resolve_active_model = MagicMock(return_value=MagicMock())
        ui._stream_renderer = MagicMock()

        with patch("localagentcli.shell.ui.ChatController") as mock_controller_cls:
            controller = MagicMock()
            controller.handle_input.return_value = iter([])
            controller.last_compaction_count = 0
            mock_controller_cls.return_value = controller

            ui._handle_plain_text("do something")

        activity_text = ui._stream_renderer.render_activity.call_args_list[0][0][0]
        assert "Phase 5" in activity_text

    def test_rebuild_prompt_session_uses_session_history(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        ui._session_manager.current.metadata["input_history"] = ["/status", "hello"]
        ui._rebuild_prompt_session()
        assert get_prompt_history_strings(ui._prompt_session) == ["/status", "hello"]

    def test_keyboard_interrupt_continues(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        ui._console = MagicMock()
        ui._prompt_session = MagicMock()
        ui._prompt_session.prompt.side_effect = [KeyboardInterrupt(), "/exit"]
        ui.run()
        assert ui._prompt_session.prompt.call_count == 2

    def test_eof_exits(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        ui._console = MagicMock()
        ui._prompt_session = MagicMock()
        ui._prompt_session.prompt.side_effect = EOFError()
        ui.run()

    def test_command_dispatch(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        ui._console = MagicMock()
        ui._prompt_session = MagicMock()
        ui._prompt_session.prompt.side_effect = ["/status", "/exit"]
        ui.run()
        calls = [str(c) for c in ui._console.print.call_args_list]
        assert any("Mode:" in c for c in calls)

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
