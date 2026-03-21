"""Entry point for the localagent CLI."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from rich.console import Console

from localagentcli.config.manager import ConfigManager
from localagentcli.runtime import RuntimeMessage, RuntimeServices, SessionExecutionRuntime
from localagentcli.shell.streaming import StreamRenderer
from localagentcli.shell.ui import ShellUI
from localagentcli.storage.manager import StorageManager


def main(argv: Sequence[str] | None = None) -> int:
    """Launch the interactive shell or a one-shot non-interactive turn."""
    args = _parse_args([] if argv is None else list(argv))
    storage, config, first_run = _bootstrap_application()

    if args.command == "exec":
        prompt = " ".join(args.prompt).strip()
        return _run_exec(prompt, config, storage)

    shell = ShellUI(config=config, storage=storage, first_run=first_run)
    shell.run()
    return 0


def _parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse top-level CLI arguments."""
    parser = argparse.ArgumentParser(prog="localagentcli")
    subcommands = parser.add_subparsers(dest="command")

    exec_parser = subcommands.add_parser(
        "exec",
        help="Run a single non-interactive chat turn.",
    )
    exec_parser.add_argument(
        "prompt",
        nargs="+",
        help="Prompt text to send through the shared runtime core.",
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


def _run_exec(prompt: str, config: ConfigManager, storage: StorageManager) -> int:
    """Run one non-interactive prompt through the shared runtime boundary."""
    output_console = Console()
    error_console = Console(stderr=True)
    services = RuntimeServices.create(config, storage, output_console)
    runtime = SessionExecutionRuntime(
        services=services,
        emit=lambda message: _emit_exec_message(message, error_console),
        confirm_backend_install=lambda _backend, _label, _deps: False,
    )
    renderer = StreamRenderer(output_console)

    try:
        runtime.sync_workspace_instruction()
        turn = runtime.run_chat_turn(prompt)
        if turn is None or turn.stream is None:
            return 1
        if turn.compaction_count:
            _emit_exec_message(
                RuntimeMessage(
                    kind="info",
                    text=f"Context compacted: summarized {turn.compaction_count} messages",
                ),
                error_console,
            )
        renderer.render_stream(turn.stream)
        return 0
    except KeyboardInterrupt:
        model = runtime.resolve_active_model()
        if model is not None:
            model.cancel()
        _emit_exec_message(
            RuntimeMessage(kind="warning", text="Generation interrupted."),
            error_console,
        )
        return 1
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


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
