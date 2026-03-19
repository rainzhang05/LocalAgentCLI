"""Prompt handling, history management, command completion, and selection menus."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.shortcuts.prompt import CompleteStyle
from prompt_toolkit.validation import ValidationError, Validator

from localagentcli.commands.router import CommandRouter

MAX_INPUT_HISTORY = 1000
COMMAND_MENU_HEIGHT = 10
CHOICE_MENU_HEIGHT = 8


@dataclass(frozen=True)
class SelectionOption:
    """Single option in an interactive prompt_toolkit selection menu."""

    value: str
    label: str
    description: str = ""
    aliases: tuple[str, ...] = ()


class LinePromptSession:
    """Minimal prompt session used when no interactive terminal is available."""

    def __init__(self, history: InMemoryHistory):
        self.history = history

    def prompt(self, message: str = "") -> str:
        if message:
            print(message, end="", flush=True)

        line = sys.stdin.readline()
        if line == "":
            raise EOFError

        text = line.rstrip("\r\n")
        if text == "\x03":
            raise KeyboardInterrupt
        if text:
            self.history.append_string(text)
        return text


class CommandCompleter(Completer):
    """Interactive completion for slash commands."""

    def __init__(self, router: CommandRouter):
        self._router = router

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor

        if not text.startswith("/"):
            return

        lowered = text.lower()

        for command_name, handler in sorted(self._router.get_visible_commands().items()):
            command = f"/{command_name}"
            if not command.lower().startswith(lowered):
                continue

            summary = handler.help_text().splitlines()[0] if handler.help_text() else ""
            yield Completion(
                command,
                start_position=-len(text),
                display=command,
                display_meta=summary,
            )


class SelectionCompleter(Completer):
    """Interactive completion for arrow-key and filterable selection menus."""

    def __init__(self, options: Sequence[SelectionOption]):
        self._options = list(options)

    def get_completions(self, document, complete_event):
        typed = document.text_before_cursor
        lowered = typed.strip().lower()

        for option in self._options:
            if lowered and not _matches_selection(option, lowered):
                continue

            yield Completion(
                option.value,
                start_position=-len(typed),
                display=option.label,
                display_meta=option.description,
            )


class SelectionValidator(Validator):
    """Ensures a selection prompt resolves to one of the known options."""

    def __init__(self, options: Sequence[SelectionOption]):
        self._options = list(options)

    def validate(self, document) -> None:
        if resolve_selection_option(document.text, self._options) is None:
            raise ValidationError(message="Select one of the available options.")


def create_prompt_session(
    router: CommandRouter,
    history_source: Path | Sequence[str] | None = None,
) -> PromptSession | LinePromptSession:
    """Create a configured PromptSession with session-backed history."""
    history = InMemoryHistory()

    if isinstance(history_source, Path):
        history_source.parent.mkdir(parents=True, exist_ok=True)
    elif history_source is not None:
        for item in list(history_source)[-MAX_INPUT_HISTORY:]:
            history.append_string(item)

    if not _supports_interactive_prompt():
        return LinePromptSession(history)

    try:
        session: PromptSession[str] = PromptSession(
            history=history,
            completer=CommandCompleter(router),
            complete_while_typing=True,
            complete_style=CompleteStyle.COLUMN,
            reserve_space_for_menu=COMMAND_MENU_HEIGHT,
            key_bindings=_build_prompt_key_bindings(),
        )
        _wire_live_command_menu(session, router)
        return session
    except Exception:
        return LinePromptSession(history)


def select_option(
    message: str,
    options: Sequence[SelectionOption],
    *,
    default: str | None = None,
) -> SelectionOption | None:
    """Prompt the user to choose from a filterable, arrow-key-friendly option list."""
    if not options or not supports_interactive_prompt():
        return None

    session: PromptSession[str] = PromptSession(
        completer=SelectionCompleter(options),
        complete_while_typing=True,
        complete_style=CompleteStyle.COLUMN,
        reserve_space_for_menu=min(max(len(options), 1), CHOICE_MENU_HEIGHT),
        key_bindings=_build_prompt_key_bindings(always_navigate_completion=True),
        validator=SelectionValidator(options),
        validate_while_typing=False,
        bottom_toolbar="Type to filter. ↑/↓ choose. Enter selects. Ctrl+C cancels.",
    )
    _wire_live_selection_menu(session, options)

    def _pre_run() -> None:
        buffer = session.default_buffer
        if default:
            buffer.text = default
            buffer.cursor_position = len(default)
        buffer.start_completion(
            select_first=not default,
            complete_event=CompleteEvent(completion_requested=True),
        )

    try:
        selected = session.prompt(f"{message}: ", pre_run=_pre_run)
    except (EOFError, KeyboardInterrupt):
        return None

    return resolve_selection_option(selected, options)


def get_prompt_history_strings(prompt_session: PromptSession | LinePromptSession) -> list[str]:
    """Return the current prompt history as a bounded list of strings."""
    history = getattr(prompt_session, "history", None)
    if history is None or not hasattr(history, "get_strings"):
        return []
    return list(history.get_strings())[-MAX_INPUT_HISTORY:]


def supports_interactive_prompt() -> bool:
    """Return whether stdin/stdout look like an interactive terminal pair."""
    stdin = getattr(sys, "stdin", None)
    stdout = getattr(sys, "stdout", None)
    return bool(
        stdin is not None
        and stdout is not None
        and hasattr(stdin, "isatty")
        and hasattr(stdout, "isatty")
        and stdin.isatty()
        and stdout.isatty()
    )


def _matches_selection(option: SelectionOption, query: str) -> bool:
    searchable = (
        option.value.lower(),
        option.label.lower(),
        *(alias.lower() for alias in option.aliases),
    )
    return any(item.startswith(query) or query in item for item in searchable)


def resolve_selection_option(
    value: str,
    options: Sequence[SelectionOption],
) -> SelectionOption | None:
    """Resolve user-entered text back to one of the known selection options."""
    lowered = value.strip().lower()
    if not lowered:
        return None

    for option in options:
        names = (
            option.value.lower(),
            option.label.lower(),
            *(alias.lower() for alias in option.aliases),
        )
        if lowered in names:
            return option
    return None


def _build_prompt_key_bindings(*, always_navigate_completion: bool = False) -> KeyBindings:
    """Create prompt bindings that preserve history and add completion navigation."""
    bindings = KeyBindings()

    @bindings.add("down")
    def _handle_down(event) -> None:
        buffer = event.current_buffer
        if _should_navigate_completions(
            buffer.document.text_before_cursor,
            always_navigate_completion=always_navigate_completion,
        ):
            if buffer.complete_state is None:
                buffer.start_completion(select_first=True)
            else:
                buffer.complete_next()
            return
        buffer.auto_down()

    @bindings.add("up")
    def _handle_up(event) -> None:
        buffer = event.current_buffer
        if _should_navigate_completions(
            buffer.document.text_before_cursor,
            always_navigate_completion=always_navigate_completion,
        ):
            if buffer.complete_state is None:
                buffer.start_completion(select_last=True)
            else:
                buffer.complete_previous()
            return
        buffer.auto_up()

    return bindings


def _wire_live_command_menu(session: PromptSession, router: CommandRouter) -> None:
    """Keep the slash-command menu open while the user types or deletes characters."""
    _wire_live_completion_menu(
        session,
        lambda buffer: _refresh_command_completion(buffer, router),
    )


def _wire_live_selection_menu(
    session: PromptSession,
    options: Sequence[SelectionOption],
) -> None:
    """Keep selection menus open while the user types or deletes characters."""
    _wire_live_completion_menu(
        session,
        lambda buffer: _refresh_selection_completion(buffer, options),
    )


def _wire_live_completion_menu(
    session: PromptSession,
    refresher: Callable[[object], None],
) -> None:
    """Refresh the completion menu on every text edit."""

    def _refresh(_event) -> None:
        refresher(session.default_buffer)

    session.default_buffer.on_text_changed += _refresh


def _refresh_command_completion(buffer, router: CommandRouter) -> None:
    """Refresh slash-command completion so the menu tracks edits and backspaces."""
    text = buffer.document.text_before_cursor
    if not text.startswith("/"):
        buffer.cancel_completion()
        return
    if not _has_command_matches(router, text):
        buffer.cancel_completion()
        return
    buffer.start_completion(
        select_first=False,
        complete_event=CompleteEvent(completion_requested=True),
    )


def _refresh_selection_completion(
    buffer,
    options: Sequence[SelectionOption],
) -> None:
    """Refresh selection completion so nested pickers track edits and backspaces."""
    text = buffer.document.text_before_cursor
    if not _has_selection_matches(options, text):
        buffer.cancel_completion()
        return
    buffer.start_completion(
        select_first=False,
        complete_event=CompleteEvent(completion_requested=True),
    )


def _should_navigate_completions(
    text_before_cursor: str,
    *,
    always_navigate_completion: bool,
) -> bool:
    if always_navigate_completion:
        return True
    return text_before_cursor.startswith("/")


def _has_command_matches(router: CommandRouter, text: str) -> bool:
    lowered = text.lower()
    return any(
        f"/{command_name}".lower().startswith(lowered)
        for command_name in router.get_visible_commands()
    )


def _has_selection_matches(
    options: Sequence[SelectionOption],
    text: str,
) -> bool:
    query = text.strip().lower()
    if not query:
        return bool(options)
    return any(_matches_selection(option, query) for option in options)


def _supports_interactive_prompt() -> bool:
    """Backward-compatible alias for older tests and patches."""
    return supports_interactive_prompt()
