"""ShellUI — main input loop, rendering, and lifecycle management."""

from __future__ import annotations

import asyncio
import difflib
import json
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from localagentcli import __version__
from localagentcli.agents.controller import AgentController
from localagentcli.agents.events import ToolCallRequested
from localagentcli.commands import agent as agent_cmd
from localagentcli.commands import agents as agents_cmd
from localagentcli.commands import (
    config_cmd,
    exit_cmd,
    set_cmd,
    setup_cmd,
)
from localagentcli.commands import help as help_cmd
from localagentcli.commands import (
    hf_token as hf_token_cmd,
)
from localagentcli.commands import mcp as mcp_cmd
from localagentcli.commands import mode as mode_cmd
from localagentcli.commands import models as models_cmd
from localagentcli.commands import plugin as plugin_cmd
from localagentcli.commands import providers as providers_cmd
from localagentcli.commands import session as session_cmd
from localagentcli.commands import skills as skills_cmd
from localagentcli.commands import status as status_cmd
from localagentcli.commands.router import CommandResult, CommandRouter
from localagentcli.config.manager import ConfigManager
from localagentcli.models.abstraction import ModelAbstractionLayer
from localagentcli.models.registry import ModelEntry
from localagentcli.plugins import PluginManager
from localagentcli.runtime import (
    ApprovalDecisionOp,
    InterruptOp,
    RuntimeEvent,
    RuntimeMessage,
    RuntimeServices,
    SessionEventLog,
    SessionExecutionRuntime,
    SessionRuntime,
    UserTurnOp,
)
from localagentcli.safety.rollback import RollbackManager
from localagentcli.shell.animation import ThinkingAnimationConfig, ThinkingAnimator
from localagentcli.shell.notifications import ShellNotification
from localagentcli.shell.prompt import (
    LinePromptSession,
    SelectionOption,
    confirm_choice,
    create_prompt_session,
    get_prompt_history_strings,
    prompt_action,
    prompt_text,
)
from localagentcli.shell.streaming import StreamRenderer
from localagentcli.shell.themes import resolve_shell_theme
from localagentcli.storage.manager import StorageManager


class ShellUI:
    """Interactive shell for LocalAgentCLI."""

    def __init__(
        self,
        config: ConfigManager,
        storage: StorageManager,
        first_run: bool = False,
    ):
        self._config = config
        self._storage = storage
        self._first_run = first_run
        self._console = Console()
        self._services = RuntimeServices.create(config, storage, self._console)
        self._logger = self._services.logger
        self._key_manager = self._services.key_manager
        self._provider_registry = self._services.provider_registry
        self._model_registry = self._services.model_registry
        self._model_detector = self._services.model_detector
        self._hardware_detector = self._services.hardware_detector
        self._model_installer = self._services.model_installer
        self._session_manager = self._services.session_manager
        self._shell_theme = resolve_shell_theme(str(self._config.get("shell.theme", "default")))
        thinking_config = ThinkingAnimationConfig(
            enabled=bool(self._config.get("shell.thinking_indicator_enabled", True)),
            style=str(self._config.get("shell.thinking_indicator_style", "dots")),
            interval_ms=int(self._config.get("shell.thinking_animation_interval_ms", 120)),
        )
        self._thinking_indicator_enabled = bool(thinking_config.enabled)
        self._thinking_animator = ThinkingAnimator(
            style=thinking_config.normalized_style(),
            interval_ms=thinking_config.normalized_interval_ms(),
            prefer_ascii=not _console_supports_unicode(self._console),
        )
        self._stream_renderer = StreamRenderer(
            self._console,
            persistent_details_lane=bool(self._config.get("shell.persistent_details_lane", False)),
            theme=self._shell_theme,
            notification_dedupe=bool(self._config.get("shell.notification_dedupe", True)),
            thinking_indicator_enabled=self._thinking_indicator_enabled,
        )
        self._execution_runtime = SessionExecutionRuntime(
            services=self._services,
            emit=self._emit_runtime_message,
            confirm_backend_install=self._confirm_backend_install,
        )
        self._runtime = self._build_session_runtime()
        self._agent_controller: AgentController | None = None
        self._awaiting_idle_exit_confirmation = False

        self._router = CommandRouter()
        self._register_commands()
        if self._services.mcp_manager is not None:
            self._services.mcp_manager.set_elicitation_handler(self._handle_mcp_elicitation)
        self._prompt_session = create_prompt_session(
            self._router,
            self._session_prompt_history(),
            toolbar_provider=self._prompt_toolbar_text,
        )
        self._sync_workspace_instruction()

    def _build_session_runtime(self) -> SessionRuntime:
        """Create a submission/event runtime for the current session."""
        event_log = SessionEventLog(
            self._storage.cache_dir / "runtime-events",
            self._session_manager.current.id,
        )
        self._session_manager.current.metadata["runtime_event_log"] = str(event_log.path)
        return SessionRuntime(self._execution_runtime, event_log=event_log)

    def _register_commands(self) -> None:
        """Register all command handlers."""
        help_cmd.register(self._router)
        status_cmd.register(
            self._router,
            self._session_manager,
            self._config,
            target_resolver=self._active_target_label,
            workspace_formatter=self._abbreviate_home,
        )
        config_cmd.register(self._router, self._config)
        hf_token_cmd.register(self._router, self._key_manager)
        mcp_cmd.register(self._router, self._services.mcp_manager, self._key_manager)
        plugin_cmd.register(
            self._router,
            PluginManager(self._storage.plugins_dir),
            self._workspace_root,
        )
        skills_cmd.register(self._router, self._services.skills_manager)
        setup_cmd.register(self._router, self._config, self._session_manager, self._console)
        session_cmd.register(self._router, self._session_manager)
        exit_cmd.register(self._router)
        agents_cmd.register(
            self._router,
            runtime_provider=lambda: self._execution_runtime,
        )
        agent_cmd.register(
            self._router,
            lambda: self._agent_controller,
            self._config,
            undo_last=self._undo_last_agent_change,
            undo_all=self._undo_all_agent_changes,
        )
        providers_cmd.register(
            self._router,
            self._provider_registry,
            self._key_manager,
            self._session_manager,
            self._config,
            self._console,
        )
        models_cmd.register(
            self._router,
            self._model_registry,
            self._model_installer,
            self._hardware_detector,
            self._session_manager,
            self._console,
            self._storage.models_dir,
        )
        set_cmd.register(
            self._router,
            self._model_registry,
            self._provider_registry,
            self._hardware_detector,
            self._config,
            self._session_manager,
            self._console,
        )
        mode_cmd.register(
            self._router,
            self._session_manager,
            self._model_registry,
            self._provider_registry,
            self._stop_agent_task_with_confirmation,
        )

    def run(self) -> None:
        """Main input loop (sync entrypoint around async runtime)."""
        asyncio.run(self.run_async())

    async def run_async(self) -> None:
        """Main input loop driven by the async session runtime."""
        self._logger.normal("Session started (id: %s)", self._session_manager.current.id)
        self._render_default_target_warning()

        self._display_welcome()
        if self._first_run:
            self._run_first_time_setup()

        try:
            self._storage.cleanup_cache()
            self._storage.cleanup_logs()
        except Exception:
            pass

        while True:
            try:
                self._sync_workspace_instruction()
                if isinstance(self._prompt_session, LinePromptSession):
                    user_input = self._prompt_session.prompt("> ")
                else:
                    user_input = await self._prompt_session.prompt_async("> ")
                self._awaiting_idle_exit_confirmation = False
                if not user_input.strip():
                    continue

                stripped = user_input.strip()
                self._sync_prompt_history_to_session()

                if stripped.startswith("/"):
                    try:
                        result = self._router.dispatch(stripped[1:])
                        self._render_command_result(result)

                        action = result.data.get("action") if result.data else None
                        if action == "session_changed":
                            await self._runtime.aclose()
                            self._execution_runtime = SessionExecutionRuntime(
                                services=self._services,
                                emit=self._emit_runtime_message,
                                confirm_backend_install=self._confirm_backend_install,
                            )
                            self._runtime = self._build_session_runtime()
                            self._agent_controller = None
                            self._rebuild_prompt_session()
                            self._sync_workspace_instruction()
                            self._render_default_target_warning()
                        if action == "agent_resume":
                            await self._handle_agent_resume(result)
                        if action == "exit":
                            await self._ahandle_exit_async()
                            break
                    except Exception as exc:
                        self._stream_renderer.render_error(f"Command failed: {exc}")
                else:
                    await self._ahandle_plain_text(stripped)

            except KeyboardInterrupt:
                self._console.print()
                if self._should_exit_after_idle_interrupt():
                    await self._ahandle_exit_async(prompt_to_save=False)
                    break
                continue
            except EOFError:
                self._console.print()
                await self._ahandle_exit_async()
                break

    async def _ahandle_plain_text(self, text: str) -> None:
        """Handle plain text input according to the current session mode."""
        try:
            self._runtime.submit(
                UserTurnOp(
                    prompt=text,
                    mode=self._session_manager.current.mode,  # explicit surface mode
                    approval_policy="shell",
                )
            )
            await self._adrain_runtime_events()
        except KeyboardInterrupt:
            interrupted = False
            async for event in self._runtime.ainterrupt():
                interrupted = True
                await self._ahandle_runtime_event(event)
            if not interrupted:
                model = self._resolve_active_model()
                if model is not None:
                    model.cancel()
                if (
                    self._session_manager.current.mode == "agent"
                    and self._agent_controller is not None
                ):
                    self._agent_controller.stop("Agent task interrupted.")
                    self._stream_renderer.render_warning("Agent task interrupted.")
                else:
                    self._stream_renderer.render_warning("Generation interrupted.")
        except Exception as exc:
            if self._session_manager.current.mode == "agent" and self._agent_controller is not None:
                self._agent_controller.stop()
            self._stream_renderer.render_error(str(exc))

    def _resolve_active_model(self) -> ModelAbstractionLayer | None:
        """Resolve the active local backend or remote provider into a model abstraction."""
        return self._execution_runtime.resolve_active_model()

    def _get_active_provider(self, provider_name: str):
        """Get the active provider, caching the instance."""
        return self._execution_runtime._get_active_provider(provider_name)

    def _get_active_backend(self, model_name: str):
        """Get the active local model backend, loading it if needed."""
        return self._execution_runtime._get_active_backend(model_name)

    def _ensure_backend_dependencies(self, backend_name: str) -> bool:
        """Prompt to install missing optional backend dependencies when needed."""
        return self._execution_runtime._ensure_backend_dependencies(backend_name)

    def _create_backend(self, fmt: str):
        """Create the appropriate backend instance for a model format."""
        return self._execution_runtime._create_backend(fmt)

    def _display_welcome(self) -> None:
        """Show the welcome banner."""
        self._console.print()
        if bool(self._config.get("shell.startup_banner", True)):
            snapshot = self._status_snapshot()
            summary = "\n".join(
                [
                    f"Mode: {snapshot.mode}",
                    f"Target: {snapshot.target}",
                    f"Workspace: {snapshot.workspace}",
                    f"Session: {snapshot.session_name}",
                ]
            )
            self._console.print(
                Panel(
                    summary,
                    title=f"LocalAgent CLI v{__version__}",
                    border_style=self._shell_theme.panel_border_style,
                )
            )
            hint = "Type /help for commands, /mode chat or /mode agent to switch behavior."
            if self._shell_theme.dim_style:
                self._console.print(Text(hint, style=self._shell_theme.dim_style))
            else:
                self._console.print(hint)
        else:
            self._console.print(Text(f"LocalAgent CLI v{__version__}", style="bold"))
        self._console.print()

    def _run_first_time_setup(self) -> None:
        """Run the setup wizard on first launch."""
        self._console.print("Welcome to LocalAgent CLI!")
        self._console.print()
        self._console.print("Let's get you set up. This will only take a moment.")
        self._console.print()

        result = self._router.dispatch("setup")
        self._render_command_result(result)

        self._console.print("You're all set! Here's how to get started:")
        self._console.print()
        self._console.print("  Just type naturally to start a conversation or task.")
        self._console.print("  Use /help to see all available commands.")
        self._console.print("  Use /mode chat for conversation, /mode agent for tasks.")
        self._console.print()

    def _status_snapshot(self) -> status_cmd.StatusSnapshot:
        """Build the current shell-status snapshot for toolbar and /status views."""
        session = self._session_manager.current
        task_state = (
            session.metadata.get("agent_task_state", {})
            if isinstance(session.metadata.get("agent_task_state", {}), dict)
            else {}
        )
        approval_mode = str(
            session.metadata.get(
                "approval_mode",
                self._config.get("safety.approval_mode", "balanced"),
            )
        )
        return status_cmd.build_status_snapshot(
            mode=session.mode,
            target=self._active_target_label(),
            workspace=self._abbreviate_home(session.workspace),
            session_name=session.name or "(unsaved)",
            approval_mode=approval_mode,
            message_count=len(session.history),
            agent_route=str(task_state.get("route", "") or ""),
            agent_phase=str(task_state.get("phase", "") or ""),
            agent_step=self._format_agent_step(task_state),
            agent_pending_tool=str(task_state.get("pending_tool", "") or ""),
            agent_wait_reason=str(task_state.get("wait_reason", "") or ""),
            agent_retry_count=int(task_state.get("retry_count", 0) or 0),
            agent_last_error=str(task_state.get("last_error", "") or ""),
            agent_active=bool(task_state.get("active", False)),
            agent_started_at=str(task_state.get("started_at", "") or ""),
            agent_updated_at=str(task_state.get("updated_at", "") or ""),
            rollback_count=int(task_state.get("rollback_count", 0) or 0),
        )

    def _prompt_toolbar_text(self) -> str:
        """Render the prompt-time status toolbar."""
        return status_cmd.format_status_toolbar(self._status_snapshot())

    def _render_command_result(self, result: CommandResult) -> None:
        """Render a command result to the console."""
        if result.message == "exit" and result.data and result.data.get("action") == "exit":
            return

        if result.presentation == "status":
            if result.message:
                self._stream_renderer.render_status(result.message)
                self._stream_renderer.flush_pending_details()
        elif result.presentation == "success":
            if result.message:
                self._stream_renderer.render_success(result.message)
        elif result.presentation == "warning":
            if result.message:
                self._stream_renderer.render_warning(result.message)
        elif result.presentation == "error":
            if result.message:
                self._stream_renderer.render_error(result.message)
        elif result.message:
            self._console.print(result.message)

        if result.body:
            self._console.print(result.body)

    def _render_default_target_warning(self) -> None:
        """Render any pending default-target repair warning once."""
        warning = self._session_manager.consume_default_target_warning()
        if warning:
            self._stream_renderer.render_warning(warning)

    async def _ahandle_exit_async(self, *, prompt_to_save: bool = True) -> None:
        """Handle clean shutdown with optional session save."""
        self._sync_prompt_history_to_session()
        self._session_manager.flush_named_autosave()
        session = self._session_manager.current
        if prompt_to_save and session.is_modified:
            save = confirm_choice("Save session before exiting?", default=False)
            if save:
                path = self._session_manager.save_session()
                self._console.print(f"Session saved to {path}")

        await self._runtime.aclose()
        self._agent_controller = None

        self._logger.normal("Session ended")
        self._console.print("[dim]Goodbye.[/dim]")

    def _generation_options(self) -> dict[str, object]:
        """Build generation options from the effective configuration."""
        return self._execution_runtime.build_generation_options()

    def _context_limit(self) -> int:
        """Return the best-known context limit for the active target."""
        return self._execution_runtime.context_limit()

    def _get_or_create_agent_controller(self, model: ModelAbstractionLayer) -> AgentController:
        """Reuse the current agent controller when the target/session is unchanged."""
        self._agent_controller = self._execution_runtime.get_or_create_agent_controller(model)
        return self._agent_controller

    def _create_agent_controller(self, model: ModelAbstractionLayer) -> AgentController:
        """Build or replace the active agent controller for the current session."""
        self._agent_controller = self._execution_runtime.create_agent_controller(model)
        return self._agent_controller

    def _drain_agent_events(self, events) -> None:
        """Render agent events and handle any inline approval prompts."""
        for event in events:
            self._stream_renderer.render_agent_event(event)
            if isinstance(event, ToolCallRequested) and event.requires_approval:
                decision = self._prompt_for_tool_approval(event)
                if self._agent_controller is None:
                    return
                if decision == "approve":
                    self._drain_agent_events(self._agent_controller.approve_action())
                elif decision == "approve_all":
                    self._drain_agent_events(self._agent_controller.approve_action(autonomous=True))
                elif decision == "deny":
                    self._drain_agent_events(self._agent_controller.deny_action())
                else:
                    self._agent_controller.stop("Agent task stopped during approval prompt.")
                    self._stream_renderer.render_warning("Agent task stopped.")
                return
        self._stream_renderer.flush_agent_event_tail()

    async def _adrain_runtime_events(self) -> None:
        """Drain typed runtime events until the current submission pauses or finishes."""
        thinking_stop: asyncio.Event | None = None
        thinking_task: asyncio.Task[None] | None = None
        if self._thinking_indicator_enabled:
            self._thinking_animator.reset()
            thinking_stop = asyncio.Event()
            thinking_task = asyncio.create_task(self._run_thinking_heartbeat(thinking_stop))
        try:
            async for event in self._runtime.aiter_events():
                await self._ahandle_runtime_event(event)
        finally:
            if thinking_stop is not None:
                thinking_stop.set()
            if thinking_task is not None:
                try:
                    await thinking_task
                except asyncio.CancelledError:
                    pass
            self._stream_renderer.stop_thinking_indicator()
            self._stream_renderer.finalize()
            self._session_manager.flush_named_autosave()

    async def _run_thinking_heartbeat(self, stop_event: asyncio.Event) -> None:
        """Render transient thinking frames while a submission is being drained."""
        self._stream_renderer.start_thinking_indicator()
        self._stream_renderer.render_thinking_indicator(self._thinking_animator.next_frame())
        interval = self._thinking_animator.interval_seconds
        while not stop_event.is_set():
            await asyncio.sleep(interval)
            if stop_event.is_set():
                return
            self._stream_renderer.render_thinking_indicator(self._thinking_animator.next_frame())

    async def _ahandle_runtime_event(self, event: RuntimeEvent) -> None:
        """Render and respond to one typed runtime event."""
        self._agent_controller = self._runtime.active_agent_controller
        if event.type == "stream_chunk":
            chunk = event.data
            if chunk is not None:
                self._stream_renderer.render_chunk(chunk)
            return
        if event.type == "route_selected":
            route = ""
            if isinstance(event.data, dict):
                route = str(event.data.get("route", "") or "")
            if route:
                self._stream_renderer.render_status(f"Agent route: {_humanize_route(route)}.")
            return
        if event.type == "agent_event":
            event_type = str(getattr(event.data, "type", "") or "")
            if event_type in {
                "task_routed",
                "task_complete",
                "task_failed",
                "task_stopped",
                "task_timed_out",
            }:
                return
            if event.data is not None:
                self._stream_renderer.render_agent_event(event.data)
            return
        if event.type == "approval_requested":
            requested = event.data
            if isinstance(requested, ToolCallRequested):
                decision = self._prompt_for_tool_approval(requested)
                if decision == "approve":
                    self._runtime.submit(ApprovalDecisionOp("approve"))
                elif decision == "approve_all":
                    self._runtime.submit(ApprovalDecisionOp("approve_all", autonomous=True))
                elif decision == "deny":
                    self._runtime.submit(ApprovalDecisionOp("deny"))
                else:
                    self._runtime.submit(InterruptOp())
                    self._stream_renderer.render_warning("Agent task stopped.")
                await self._adrain_runtime_events()
            return
        if event.type == "turn_completed":
            if isinstance(event.data, dict) and event.data.get("mode") == "agent":
                self._stream_renderer.render_success("Task completed.")
                self._stream_renderer.flush_pending_details()
                route = str(event.data.get("route", "") or "")
                summary = event.message.strip()
                if summary and route != "direct_answer":
                    self._stream_renderer.render_markdown_message(event.message)
            return
        if event.type == "turn_failed":
            self._stream_renderer.render_error(event.message or "Turn failed.")
            return
        if event.type == "turn_interrupted":
            self._stream_renderer.render_warning(event.message or "Turn interrupted.")
            return
        if event.type == "warning":
            self._stream_renderer.render_warning(event.message)
            return
        if event.type == "error":
            self._stream_renderer.render_error(event.message)
            return

    def _prompt_for_tool_approval(self, event: ToolCallRequested) -> str:
        """Prompt inline for approval of a pending tool call."""
        while True:
            self._stream_renderer.flush_pending_details()
            self._stream_renderer.render_approval_prompt()
            selection = prompt_action(
                "Choose approval action",
                [
                    SelectionOption(
                        value="approve",
                        label="Approve",
                        description="Run the requested tool call now.",
                    ),
                    SelectionOption(
                        value="deny",
                        label="Deny",
                        description="Reject this tool call and let the agent recover.",
                        aliases=("d", "/agent deny"),
                    ),
                    SelectionOption(
                        value="details",
                        label="View details",
                        description="Inspect a fuller preview before deciding.",
                        aliases=("v", "view"),
                    ),
                    SelectionOption(
                        value="approve_all",
                        label="Approve all",
                        description="Enable autonomous approvals for this and future sessions.",
                        aliases=("a", "/agent approve"),
                    ),
                ],
                default="approve",
            )
            if selection is None:
                return "stop"
            if selection.value == "approve":
                return "approve"
            if selection.value == "deny":
                return "deny"
            if selection.value == "approve_all":
                return "approve_all"
            if selection.value == "details":
                self._stream_renderer.render_preview(
                    f"{event.tool_name} preview",
                    self._format_tool_preview(event),
                )
                continue

    def _format_tool_preview(self, event: ToolCallRequested) -> str:
        """Render a detailed preview of a pending tool call."""
        arguments = dict(event.arguments)
        header = self._tool_preview_header(event)
        if event.tool_name == "patch_apply":
            old_raw = str(arguments.get("old_text", ""))
            new_raw = str(arguments.get("new_text", ""))
            old_text, old_truncated = self._truncate_preview_text(old_raw)
            new_text, new_truncated = self._truncate_preview_text(new_raw)
            replace_label = "Replace" + (" (truncated)" if old_truncated else "")
            with_label = "With" + (" (truncated)" if new_truncated else "")
            diff_text, diff_truncated = self._build_unified_diff_preview(old_raw, new_raw)
            diff_label = "Unified diff" + (" (truncated)" if diff_truncated else "")
            added, removed = self._diff_change_counts(diff_text)
            return header + (
                "Action: patch existing file\n"
                f"Change summary: +{added} / -{removed}\n\n"
                f"{replace_label}:\n{old_text}\n\n"
                f"{with_label}:\n{new_text}\n\n"
                f"{diff_label}:\n```diff\n{diff_text}\n```"
            )
        if event.tool_name == "file_write":
            content = str(arguments.get("content", ""))
            preview, truncated = self._truncate_preview_text(content)
            path = str(arguments.get("path", "(unknown)"))
            action = (
                "overwrite existing file" if self._preview_path_exists(path) else "create new file"
            )
            label = "Content preview" + (" (truncated)" if truncated else "")
            language = self._guess_preview_language(path)
            return header + f"Action: {action}\n\n{label}:\n```{language}\n{preview}\n```"
        if event.tool_name == "shell_execute":
            command_preview, command_truncated = self._truncate_preview_text(
                str(arguments.get("command", ""))
            )
            command_label = "Command" + (" (truncated)" if command_truncated else "")
            return header + (
                f"{command_label}:\n{command_preview}\n\n"
                f"Working directory: {arguments.get('working_dir', '.')}"
            )
        if event.tool_name == "test_execute":
            framework = arguments.get("framework", "(auto-detect)")
            path = arguments.get("path", ".")
            extra_args = arguments.get("args", "")
            lines = [header.rstrip(), f"Framework: {framework}", f"Target: {path}"]
            if extra_args:
                lines.append(f"Args: {extra_args}")
            return "\n".join(lines)
        if event.tool_name == "git_commit":
            files = arguments.get("files") or []
            staged = ", ".join(files) if isinstance(files, list) and files else "all staged changes"
            return header + (f"Commit message: {arguments.get('message', '')}\nFiles: {staged}")
        return header + json.dumps(
            {"tool": event.tool_name, "arguments": arguments},
            indent=2,
            ensure_ascii=False,
        )

    def _tool_preview_header(self, event: ToolCallRequested) -> str:
        """Build the common high-signal header for a pending tool preview."""
        arguments = dict(event.arguments)
        lines = [f"Tool: {event.tool_name}"]
        if "path" in arguments:
            lines.append(f"Target: {arguments.get('path', '(unknown)')}")
        if event.risk_level == "high":
            lines.append("Risk: HIGH RISK")
        if event.risk_reason:
            lines.append(f"Why flagged: {event.risk_reason}")
        if event.warnings:
            lines.append("Warnings:")
            lines.extend(f"- {warning}" for warning in event.warnings)
        if event.rollback_summary:
            lines.append(event.rollback_summary)
        return "\n".join(lines) + "\n\n"

    def _preview_path_exists(self, raw_path: str) -> bool:
        """Check whether a preview path currently exists inside the workspace."""
        try:
            return (self._workspace_root() / raw_path).resolve().exists()
        except Exception:
            return False

    def _truncate_preview_text(self, text: str, *, limit: int = 500) -> tuple[str, bool]:
        """Return preview-safe text plus whether truncation was applied."""
        if len(text) <= limit:
            return text, False
        return text[:limit] + "...", True

    def _build_unified_diff_preview(self, old_text: str, new_text: str) -> tuple[str, bool]:
        """Build a unified diff preview body for patch_apply approvals."""
        diff_lines = difflib.unified_diff(
            old_text.splitlines(),
            new_text.splitlines(),
            fromfile="before",
            tofile="after",
            lineterm="",
        )
        diff_text = "\n".join(diff_lines).strip()
        if not diff_text:
            diff_text = "(no textual changes detected)"
        return self._truncate_preview_text(diff_text, limit=2200)

    @staticmethod
    def _diff_change_counts(diff_text: str) -> tuple[int, int]:
        """Count added and removed lines in a unified diff preview."""
        added = 0
        removed = 0
        for line in diff_text.splitlines():
            if line.startswith("+++") or line.startswith("---"):
                continue
            if line.startswith("+"):
                added += 1
            elif line.startswith("-"):
                removed += 1
        return added, removed

    @staticmethod
    def _guess_preview_language(path: str) -> str:
        """Best-effort lexer hint for fenced preview blocks."""
        suffix = Path(path).suffix.lower()
        mapping = {
            ".py": "python",
            ".js": "javascript",
            ".ts": "typescript",
            ".tsx": "tsx",
            ".jsx": "jsx",
            ".json": "json",
            ".md": "markdown",
            ".sh": "bash",
            ".yml": "yaml",
            ".yaml": "yaml",
            ".toml": "toml",
            ".rs": "rust",
            ".go": "go",
            ".java": "java",
            ".c": "c",
            ".cpp": "cpp",
            ".h": "c",
            ".html": "html",
            ".css": "css",
        }
        return mapping.get(suffix, "text")

    async def _handle_agent_resume(self, result: CommandResult) -> None:
        """Resume a paused agent task after an /agent command."""
        if self._runtime.active_submission_id is None:
            return
        decision = result.data.get("decision") if result.data else None
        autonomous = bool(result.data.get("autonomous")) if result.data else False
        if decision == "approve":
            self._runtime.submit(ApprovalDecisionOp("approve", autonomous=autonomous))
        elif decision == "deny":
            self._runtime.submit(ApprovalDecisionOp("deny"))
        else:
            return
        await self._adrain_runtime_events()

    def _undo_last_agent_change(self) -> tuple[str, str | None]:
        """Undo the most recent rollback entry for the current session."""
        if self._agent_controller is not None and self._agent_controller.has_active_task:
            raise RuntimeError("Stop the active agent task before undoing changes.")
        manager = self._rollback_manager()
        entry = manager.undo_last()
        self._refresh_agent_rollback_state(manager)
        body = (
            f"Tool: {entry.tool}\n"
            f"Path: {entry.file_path}\n"
            f"Action: {entry.action}\n"
            f"Summary: {entry.summary}"
        )
        return f"Reverted last agent change: {Path(entry.file_path).name}", body

    def _undo_all_agent_changes(self) -> tuple[str, str | None]:
        """Undo every rollback entry for the current session."""
        if self._agent_controller is not None and self._agent_controller.has_active_task:
            raise RuntimeError("Stop the active agent task before undoing changes.")
        manager = self._rollback_manager()
        undone = manager.undo_all()
        self._refresh_agent_rollback_state(manager)
        if not undone:
            raise ValueError("No rollback history is available for this session.")
        lines = [
            f"{index}. {entry.action} {entry.file_path} ({entry.tool})"
            for index, entry in enumerate(undone, start=1)
        ]
        return f"Reverted {len(undone)} agent change(s).", "\n".join(lines)

    def _stop_agent_task_with_confirmation(self) -> bool:
        """Stop an active agent task before mode or session changes."""
        if self._agent_controller is None or not self._agent_controller.has_active_task:
            return True
        stop = confirm_choice(
            "An agent task is active. Stop it before switching modes?",
            default=True,
        )
        if stop is None:
            return False
        if not stop:
            return False
        self._agent_controller.stop("Agent task stopped before switching modes.")
        self._stream_renderer.render_warning("Agent task stopped.")
        return True

    def _workspace_root(self) -> Path:
        """Resolve the current session workspace to an absolute path."""
        return self._execution_runtime.workspace_root()

    def _rollback_manager(self) -> RollbackManager:
        """Return the rollback manager for the current session."""
        return RollbackManager(self._session_manager.current.id, self._storage.cache_dir)

    def _refresh_agent_rollback_state(self, manager: RollbackManager) -> None:
        """Keep status surfaces aligned after manual rollback commands."""
        task_state = self._session_manager.current.metadata.get("agent_task_state", {})
        if not isinstance(task_state, dict):
            task_state = {}
        task_state["rollback_count"] = len(manager.get_history())
        self._session_manager.current.touch()
        task_state["updated_at"] = self._session_manager.current.updated_at.isoformat()
        self._session_manager.current.metadata["agent_task_state"] = task_state

    def _format_agent_step(self, task_state: dict[str, object]) -> str:
        """Render the current or last agent step for status views."""
        step_index = task_state.get("step_index")
        step_description = str(task_state.get("step_description", "") or "")
        if isinstance(step_index, int) and step_description:
            return f"{step_index}. {step_description}"
        return step_description

    def _active_target_label(self) -> str:
        """Describe the active local model or remote provider for the status header."""
        return self._execution_runtime.active_target_label()

    def _session_prompt_history(self) -> list[str]:
        """Load persisted prompt history from the current session metadata."""
        history = self._session_manager.current.metadata.get("input_history", [])
        if not isinstance(history, list):
            return []
        return [item for item in history if isinstance(item, str)]

    def _sync_prompt_history_to_session(self) -> None:
        """Persist the current prompt history back into the active session metadata."""
        session = self._session_manager.current
        history = get_prompt_history_strings(self._prompt_session)
        if session.metadata.get("input_history") != history:
            session.metadata["input_history"] = history

    def _rebuild_prompt_session(self) -> None:
        """Rebuild the prompt session after a session switch."""
        self._prompt_session = create_prompt_session(
            self._router,
            self._session_prompt_history(),
            toolbar_provider=self._prompt_toolbar_text,
        )

    def _abbreviate_home(self, path: str) -> str:
        """Display home-relative paths more compactly."""
        try:
            home = str(Path.home())
            if path.startswith(home):
                return "~" + path[len(home) :]
        except Exception:
            pass
        return path

    def _parse_name_version(self, model_name: str) -> tuple[str, str | None]:
        """Parse a stored model reference in name@version form."""
        return self._services.parse_name_version(model_name)

    def _refresh_model_entry(self, name: str, version: str | None) -> ModelEntry | None:
        """Re-detect a local model on disk and repair stale registry metadata."""
        return self._services.refresh_model_entry(name, version)

    def _should_exit_after_idle_interrupt(self) -> bool:
        """Exit after two idle Ctrl+C presses with no other action in between."""
        if self._awaiting_idle_exit_confirmation:
            self._awaiting_idle_exit_confirmation = False
            return True
        self._awaiting_idle_exit_confirmation = True
        self._console.print("[dim]Press Ctrl+C again to exit.[/dim]")
        return False

    def _sync_workspace_instruction(self) -> None:
        """Cache repository-level AGENTS.md instructions for the active session."""
        self._execution_runtime.sync_workspace_instruction()

    def _emit_runtime_message(self, message: RuntimeMessage) -> None:
        """Render runtime-owned user-visible messages through shell surfaces."""
        if message.kind == "status":
            self._stream_renderer.render_notification(
                ShellNotification(level="status", message=message.text)
            )
        elif message.kind == "success":
            self._stream_renderer.render_notification(
                ShellNotification(level="success", message=message.text)
            )
        elif message.kind == "warning":
            self._stream_renderer.render_notification(
                ShellNotification(level="warning", message=message.text)
            )
        elif message.kind == "error":
            self._stream_renderer.render_notification(
                ShellNotification(level="error", message=message.text)
            )
        else:
            self._console.print(f"[dim]{message.text}[/dim]")

    def _confirm_backend_install(
        self,
        _backend_name: str,
        label: str,
        dependency_list: str,
    ) -> bool | None:
        """Prompt the user before installing missing backend dependencies."""
        return confirm_choice(
            f"The {label} backend requires {dependency_list}. Install it now?",
            default=True,
        )

    def _handle_mcp_elicitation(
        self,
        server_name: str,
        tool_name: str,
        request: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Prompt for MCP elicitation responses requested during tool execution."""
        message = str(request.get("message", "MCP server requested additional input.")).strip()
        self._stream_renderer.render_notification(
            ShellNotification(
                level="warning",
                message=f"MCP elicitation requested by {server_name}/{tool_name}: {message}",
            )
        )

        schema = request.get("requestedSchema") or request.get("requested_schema")
        if not isinstance(schema, dict):
            answer = prompt_text("Elicitation response", default="")
            if answer is None:
                return None
            return {"value": answer}

        properties = schema.get("properties")
        required = schema.get("required")
        if not isinstance(properties, dict) or not properties:
            answer = prompt_text("Elicitation response", default="")
            if answer is None:
                return None
            return {"value": answer}

        required_set = (
            {item for item in required if isinstance(item, str)}
            if isinstance(required, list)
            else set()
        )
        response: dict[str, Any] = {}
        for field_name, field_schema in properties.items():
            if not isinstance(field_schema, dict):
                field_schema = {}
            description = str(field_schema.get("description", "")).strip()
            prompt_label = f"{field_name}"
            if description:
                prompt_label = f"{field_name} ({description})"
            default_value = ""
            raw_value = prompt_text(prompt_label, default=default_value)
            if raw_value is None:
                return None
            if not raw_value and field_name in required_set:
                self._stream_renderer.render_warning(
                    f"Required elicitation field '{field_name}' cannot be empty."
                )
                return None
            if raw_value:
                response[str(field_name)] = raw_value
        return response


def _humanize_route(route: str) -> str:
    mapping = {
        "direct_answer": "direct answer",
        "single_step_task": "single-step task",
        "multi_step_task": "multi-step task",
    }
    return mapping.get(route, route.replace("_", " "))


def _console_supports_unicode(console: Console) -> bool:
    """Return whether the current output encoding can represent a unicode spinner."""
    file = getattr(console, "file", None)
    encoding = getattr(file, "encoding", None)
    if not isinstance(encoding, str) or not encoding:
        return True
    try:
        "⠋".encode(encoding)
    except (LookupError, UnicodeEncodeError):
        return False
    return True
