"""Tests for localagentcli.shell (prompt and ui)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from localagentcli.agents.events import ToolCallRequested
from localagentcli.commands.router import CommandResult, CommandRouter
from localagentcli.models.registry import ModelEntry
from localagentcli.shell.prompt import (
    COMMAND_MENU_HEIGHT,
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

    def test_includes_command_help_as_completion_metadata(self):
        router = CommandRouter()
        from tests.test_command_router import StubHandler

        router.register("help", StubHandler())

        completer = CommandCompleter(router)
        doc = MagicMock()
        doc.text_before_cursor = "/h"
        completion = next(iter(completer.get_completions(doc, None)))

        assert completion.display_meta_text == "Stub: ok"


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

    @patch("localagentcli.shell.prompt._supports_interactive_prompt", return_value=False)
    @patch("localagentcli.shell.prompt.sys.stdin.readline", return_value="/status\n")
    def test_falls_back_without_interactive_terminal(self, _mock_readline, _mock_supports):
        router = CommandRouter()

        session = create_prompt_session(router, ["/help"])
        value = session.prompt("> ")

        assert value == "/status"
        assert get_prompt_history_strings(session) == ["/help", "/status"]


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

    def test_active_target_label_for_provider(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        ui._session_manager.current.provider = "openai"
        ui._session_manager.current.model = "gpt-4.1"

        assert ui._active_target_label() == "openai (gpt-4.1)"

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

    def test_agent_mode_plain_text_uses_agent_controller(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        ui._session_manager.current.mode = "agent"
        ui._resolve_active_model = MagicMock(return_value=MagicMock())
        ui._stream_renderer = MagicMock()

        with patch("localagentcli.shell.ui.AgentController") as mock_controller_cls:
            controller = MagicMock()
            controller.handle_task.return_value = iter(["agent-event"])
            controller.last_compaction_count = 0
            controller.has_active_task = False
            mock_controller_cls.return_value = controller

            ui._handle_plain_text("do something")

        controller.handle_task.assert_called_once_with("do something")
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


class TestShellUIModelResolution:
    def test_resolve_active_model_uses_provider_backend(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        ui._session_manager.current.provider = "openai"
        backend = MagicMock()

        with patch.object(ui, "_get_active_provider", return_value=backend):
            model = ui._resolve_active_model()

        assert model is not None
        assert model.backend is backend

    def test_resolve_active_model_reports_local_load_failure(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        ui._console = MagicMock()
        ui._session_manager.current.model = "demo@v1"

        with patch.object(ui, "_get_active_backend", return_value=None):
            model = ui._resolve_active_model()

        assert model is None
        assert "Failed to load model" in ui._console.print.call_args.args[0]

    def test_ensure_backend_dependencies_installs_missing_packages(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        ui._console = MagicMock()

        with (
            patch(
                "localagentcli.shell.ui.check_backend_dependencies",
                return_value=(False, ["llama_cpp"]),
            ),
            patch("localagentcli.shell.ui.Confirm.ask", return_value=True) as mock_confirm,
            patch(
                "localagentcli.shell.ui.install_backend_dependencies",
                return_value=(True, "installed"),
            ) as mock_install,
        ):
            result = ui._ensure_backend_dependencies("gguf")

        assert result is True
        mock_confirm.assert_called_once()
        mock_install.assert_called_once_with("gguf")

    def test_ensure_backend_dependencies_handles_cancelled_prompt(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        ui._console = MagicMock()

        with (
            patch(
                "localagentcli.shell.ui.check_backend_dependencies",
                return_value=(False, ["llama_cpp"]),
            ),
            patch(
                "localagentcli.shell.ui.Confirm.ask",
                side_effect=KeyboardInterrupt,
            ),
        ):
            result = ui._ensure_backend_dependencies("gguf")

        assert result is False
        assert "loading cancelled" in ui._console.print.call_args.args[0]


class TestShellUIHelpers:
    def test_format_tool_preview_for_patch_apply(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        event = ToolCallRequested(
            tool_name="patch_apply",
            arguments={"path": "file.py", "old_text": "old", "new_text": "new"},
            requires_approval=True,
        )

        preview = ui._format_tool_preview(event)

        assert "Replace" in preview
        assert "With" in preview

    def test_format_tool_preview_truncates_file_write(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        event = ToolCallRequested(
            tool_name="file_write",
            arguments={"path": "file.py", "content": "a" * 600},
            requires_approval=True,
        )

        preview = ui._format_tool_preview(event)

        assert preview.endswith("...")

    def test_handle_agent_resume_approves_with_autonomy(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        controller = MagicMock()
        controller.approve_action.return_value = iter(["event"])
        ui._agent_controller = controller
        ui._drain_agent_events = MagicMock()

        ui._handle_agent_resume(
            CommandResult.ok(
                "approved",
                data={"decision": "approve", "autonomous": True},
            )
        )

        controller.approve_action.assert_called_once_with(autonomous=True)
        ui._drain_agent_events.assert_called_once()

    def test_stop_agent_task_with_confirmation_decline(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        controller = MagicMock()
        controller.has_active_task = True
        ui._agent_controller = controller
        ui._stream_renderer = MagicMock()

        with patch("localagentcli.shell.ui.Confirm.ask", return_value=False):
            result = ui._stop_agent_task_with_confirmation()

        assert result is False
        controller.stop.assert_not_called()

    def test_workspace_root_resolves_current_workspace(self, config, storage):
        ui = ShellUI(config=config, storage=storage)
        ui._session_manager.current.workspace = "."

        assert ui._workspace_root() == Path(".").resolve()
