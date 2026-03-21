"""Entry point for the localagent CLI."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from rich.console import Console

from localagentcli.config.manager import ConfigManager
from localagentcli.runtime import (
    RuntimeEvent,
    RuntimeMessage,
    RuntimeServices,
    SessionEventLog,
    SessionExecutionRuntime,
    SessionRuntime,
    UserTurnOp,
)
from localagentcli.shell.ui import ShellUI
from localagentcli.storage.manager import StorageManager


def main(argv: Sequence[str] | None = None) -> int:
    """Launch the interactive shell or a one-shot non-interactive turn."""
    args = _parse_args([] if argv is None else list(argv))
    storage, config, first_run = _bootstrap_application()

    if args.command == "exec":
        prompt = " ".join(args.prompt).strip()
        return _run_exec(
            prompt,
            config,
            storage,
            mode=args.mode,
            json_mode=bool(args.json),
            approval_policy=args.approval_policy,
            session_name=args.session,
            fork_name=args.fork,
            save_session=args.save_session,
        )

    shell = ShellUI(config=config, storage=storage, first_run=first_run)
    shell.run()
    return 0


def _parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse top-level CLI arguments."""
    parser = argparse.ArgumentParser(prog="localagentcli")
    subcommands = parser.add_subparsers(dest="command")

    exec_parser = subcommands.add_parser(
        "exec",
        help="Run a single non-interactive turn.",
    )
    exec_parser.add_argument(
        "prompt",
        nargs="+",
        help="Prompt text to send through the shared runtime core.",
    )
    exec_parser.add_argument(
        "--mode",
        choices=("chat", "agent"),
        default=None,
        help="Execution mode for the one-shot turn.",
    )
    exec_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit one runtime event per line on stdout.",
    )
    exec_parser.add_argument(
        "--approval-policy",
        choices=("deny", "auto"),
        default="deny",
        help="How headless exec responds to approval-requiring tool actions.",
    )
    session_group = exec_parser.add_mutually_exclusive_group()
    session_group.add_argument(
        "--session",
        help="Resume and update a saved session by name.",
    )
    session_group.add_argument(
        "--fork",
        help="Fork a saved session by name before running the turn.",
    )
    exec_parser.add_argument(
        "--save-session",
        default=None,
        help="Save the resulting session under this name after the turn finishes.",
    )

    return parser.parse_args(argv)


def _bootstrap_application() -> tuple[StorageManager, ConfigManager, bool]:
    """Initialize storage and config shared by all entrypoints."""
    storage = StorageManager()
    storage.initialize()
    first_run = not storage.config_path.exists()

    config = ConfigManager(storage.config_path)
    config.load()
    return storage, config, first_run


def _run_exec(
    prompt: str,
    config: ConfigManager,
    storage: StorageManager,
    *,
    mode: str | None,
    json_mode: bool,
    approval_policy: str,
    session_name: str | None,
    fork_name: str | None,
    save_session: str | None,
) -> int:
    """Run one non-interactive prompt through the shared submission/event runtime."""
    output_console = Console()
    error_console = Console(stderr=True)
    services = RuntimeServices.create(config, storage, output_console)
    persisted_session_name = _prepare_exec_session(
        services,
        session_name=session_name,
        fork_name=fork_name,
        save_session=save_session,
    )
    execution_runtime = SessionExecutionRuntime(
        services=services,
        emit=lambda message: _emit_exec_message(message, error_console),
        confirm_backend_install=lambda _backend, _label, _deps: False,
    )
    runtime = SessionRuntime(
        execution_runtime,
        event_log=SessionEventLog(
            storage.cache_dir / "runtime-events",
            services.session_manager.current.id,
        ),
    )

    try:
        execution_runtime.sync_workspace_instruction()
        runtime.submit(
            UserTurnOp(
                prompt=prompt,
                mode=mode,
                approval_policy=approval_policy,  # type: ignore[arg-type]
            )
        )
        exit_code = _drain_exec_runtime_events(
            runtime,
            output_console,
            error_console,
            json_mode=json_mode,
        )
        if persisted_session_name:
            services.session_manager.save_session(persisted_session_name)
        return exit_code
    except KeyboardInterrupt:
        exit_code = 1
        for event in runtime.interrupt():
            if json_mode:
                output_console.print(json.dumps(event.to_dict(), ensure_ascii=False))
            else:
                exit_code = max(
                    exit_code,
                    _render_exec_human_event(event, output_console, error_console),
                )
        return exit_code
    except Exception as exc:
        _emit_exec_message(RuntimeMessage(kind="error", text=str(exc)), error_console)
        return 1
    finally:
        runtime.close()


def _emit_exec_message(message: RuntimeMessage, console: Console) -> None:
    """Render non-interactive runtime messages on stderr."""
    if message.kind == "status":
        console.print(f"[dim]{message.text}[/dim]")
    elif message.kind == "success":
        console.print(f"[green]{message.text}[/green]")
    elif message.kind == "warning":
        console.print(f"[yellow]{message.text}[/yellow]")
    elif message.kind == "error":
        console.print(f"[red]{message.text}[/red]")
    else:
        console.print(f"[dim]{message.text}[/dim]")


def _prepare_exec_session(
    services: RuntimeServices,
    *,
    session_name: str | None,
    fork_name: str | None,
    save_session: str | None,
) -> str | None:
    """Load or fork a saved session for exec and choose its persistence target."""
    if fork_name:
        forked = services.session_manager.fork_session(fork_name)
        return save_session or (forked.name or None)
    if session_name:
        services.session_manager.load_session(session_name)
        return save_session or session_name
    return save_session


def _drain_exec_runtime_events(
    runtime: SessionRuntime,
    output_console: Console,
    error_console: Console,
    *,
    json_mode: bool,
) -> int:
    """Drain runtime events for the headless exec surface."""
    exit_code = 0
    for event in runtime.iter_events():
        if json_mode:
            output_console.print(json.dumps(event.to_dict(), ensure_ascii=False))
            continue
        exit_code = max(
            exit_code,
            _render_exec_human_event(event, output_console, error_console),
        )
    return exit_code


def _render_exec_human_event(
    event: RuntimeEvent,
    output_console: Console,
    error_console: Console,
) -> int:
    """Render one runtime event for the human exec contract."""
    if event.type == "stream_chunk" and hasattr(event.data, "kind"):
        chunk = event.data
        if getattr(chunk, "kind", "") == "final_text" and getattr(chunk, "text", ""):
            output_console.print(chunk.text, end="", highlight=False)
        return 0

    if event.type == "route_selected":
        if event.message:
            error_console.print(f"[dim]Agent route: {event.message}[/dim]")
        return 0

    if event.type == "agent_event":
        event_type = str(getattr(event.data, "type", "") or "")
        if event_type in {
            "task_routed",
            "task_complete",
            "task_failed",
            "task_stopped",
            "task_timed_out",
        }:
            return 0
        summary = getattr(event.data, "summary", "") or getattr(event.data, "changes", "")
        if summary:
            error_console.print(f"[dim]{summary}[/dim]")
        elif event_type:
            error_console.print(f"[dim]{event_type}[/dim]")
        return 0

    if event.type == "approval_requested":
        tool_name = str(getattr(event.data, "tool_name", "") or event.message or "tool")
        error_console.print(
            f"[yellow]Approval required for {tool_name}; request denied in headless mode.[/yellow]"
        )
        return 0

    if event.type == "turn_completed":
        payload = event.data if isinstance(event.data, dict) else {}
        final_text = str(payload.get("final_text", "") or payload.get("summary", "") or "")
        if final_text:
            output_console.print()
            output_console.print(final_text)
        elif event.message:
            error_console.print(f"[dim]{event.message}[/dim]")
        return 0

    if event.type == "turn_failed":
        error_console.print(f"[red]{event.message or 'Turn failed.'}[/red]")
        return 1

    if event.type == "turn_interrupted":
        error_console.print(f"[yellow]{event.message or 'Turn interrupted.'}[/yellow]")
        return 1

    if event.type == "warning":
        error_console.print(f"[yellow]{event.message}[/yellow]")
        return 0

    if event.type == "error":
        error_console.print(f"[red]{event.message}[/red]")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
