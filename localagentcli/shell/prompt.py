"""Prompt handling, history management, command completion, and selection menus."""

from __future__ import annotations

import asyncio
import inspect
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from shutil import get_terminal_size
from typing import Callable

from prompt_toolkit import PromptSession
from prompt_toolkit.application.current import get_app_or_none
from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.shortcuts.prompt import CompleteStyle
from prompt_toolkit.styles import Style
from prompt_toolkit.validation import ValidationError, Validator

from localagentcli.commands.router import CommandRouter, CommandSpec

MAX_INPUT_HISTORY = 1000
COMMAND_MENU_HEIGHT = 10
# Briefly coalesce completion refreshes so each keystroke does not restart the menu.
COMPLETION_MENU_REFRESH_DEBOUNCE_SEC = 0.04
CHOICE_MENU_HEIGHT = 8
NARROW_TERMINAL_WIDTH = 80
NARROW_MENU_HEIGHT = 5
TEXT_PROMPT_TOOLBAR = "Enter accepts. Ctrl+C cancels."
SECRET_PROMPT_TOOLBAR = "Input is hidden. Enter accepts. Ctrl+C cancels."
ACTION_PROMPT_TOOLBAR = "Type to filter. Enter selects the default. Ctrl+C cancels."
CHOICE_PROMPT_TOOLBAR = "Type to filter. ↑/↓ choose. Enter selects. Ctrl+C cancels."
UI_ACCENT_HEX = "#40E0D0"

_PROMPT_STYLE = Style.from_dict(
    {
        "completion-menu": "bg:default",
        "completion-menu.completion": "bg:default fg:black",
        "completion-menu.completion.current": f"bg:default fg:{UI_ACCENT_HEX} bold noreverse",
        "completion-menu.meta.completion": "bg:default fg:black",
        "completion-menu.meta.completion.current": (
            f"bg:default fg:{UI_ACCENT_HEX} bold noreverse"
        ),
        "completion-menu.multi-column-meta": "bg:default fg:black",
        "completion-menu.multi-column-meta.current": (
            f"bg:default fg:{UI_ACCENT_HEX} bold noreverse"
        ),
        "scrollbar.background": "bg:default",
        "scrollbar.button": "bg:default fg:black",
        "bottom-toolbar": f"fg:{UI_ACCENT_HEX}",
    }
)


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

            spec = handler.describe()
            summary = _completion_summary(spec)
            display = command
            if spec.argument_hint:
                display = f"{display} {spec.argument_hint}"
            display = _truncate_with_ellipsis(display, _menu_line_width())
            summary = _truncate_with_ellipsis(summary, _menu_meta_width())
            yield Completion(
                command,
                start_position=-len(text),
                display=display,
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
                display=_truncate_with_ellipsis(option.label, _menu_line_width()),
                display_meta=_truncate_with_ellipsis(option.description, _menu_meta_width()),
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
    toolbar_provider: Callable[[], str] | None = None,
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
            reserve_space_for_menu=_menu_height_for_terminal(COMMAND_MENU_HEIGHT),
            key_bindings=_build_prompt_key_bindings(),
            bottom_toolbar=_build_status_toolbar(toolbar_provider),
            style=_PROMPT_STYLE,
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
    bottom_toolbar: str = CHOICE_PROMPT_TOOLBAR,
) -> SelectionOption | None:
    """Prompt the user to choose from a filterable, arrow-key-friendly option list."""
    if not options or not supports_interactive_prompt():
        return None

    session: PromptSession[str] = PromptSession(
        completer=SelectionCompleter(options),
        complete_while_typing=True,
        complete_style=CompleteStyle.COLUMN,
        reserve_space_for_menu=min(
            max(len(options), 1),
            _menu_height_for_terminal(CHOICE_MENU_HEIGHT),
        ),
        key_bindings=_build_prompt_key_bindings(always_navigate_completion=True),
        validator=SelectionValidator(options),
        validate_while_typing=False,
        bottom_toolbar=bottom_toolbar,
        style=_PROMPT_STYLE,
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
        if _should_use_in_thread_prompt(session):
            selected = session.prompt(f"{message}: ", pre_run=_pre_run, in_thread=True)
        else:
            selected = session.prompt(f"{message}: ", pre_run=_pre_run)
    except (EOFError, KeyboardInterrupt):
        return None

    return resolve_selection_option(selected, options)


def prompt_text(message: str, *, default: str | None = None) -> str | None:
    """Prompt for free-form text and return None when cancelled."""
    return _prompt_value(message, default=default, password=False, toolbar=TEXT_PROMPT_TOOLBAR)


def prompt_secret(message: str) -> str | None:
    """Prompt for a secret value with hidden input."""
    return _prompt_value(message, default=None, password=True, toolbar=SECRET_PROMPT_TOOLBAR)


def prompt_action(
    message: str,
    options: Sequence[SelectionOption],
    *,
    default: str | None = None,
) -> SelectionOption | None:
    """Prompt for one short action using the shared selection surface."""
    return select_option(
        message,
        options,
        default=default,
        bottom_toolbar=ACTION_PROMPT_TOOLBAR,
    )


def confirm_choice(message: str, *, default: bool = True) -> bool | None:
    """Prompt for a yes/no confirmation using the shared action surface."""
    selection = prompt_action(
        message,
        [
            SelectionOption(
                value="yes",
                label="Yes",
                description="Continue with the requested action.",
                aliases=("y",),
            ),
            SelectionOption(
                value="no",
                label="No",
                description="Cancel and keep the current state.",
                aliases=("n",),
            ),
        ],
        default="yes" if default else "no",
    )
    if selection is None:
        return None
    return selection.value == "yes"


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


def _has_running_event_loop() -> bool:
    """Return whether prompt code is executing inside a running asyncio loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def _prompt_supports_in_thread(session: PromptSession[str]) -> bool:
    """Return whether PromptSession.prompt accepts an in_thread keyword."""
    prompt_fn = getattr(session, "prompt", None)
    if prompt_fn is None:
        return False
    try:
        return "in_thread" in inspect.signature(prompt_fn).parameters
    except (TypeError, ValueError):
        return False


def _should_use_in_thread_prompt(session: PromptSession[str]) -> bool:
    """Return whether sync prompt calls should use prompt_toolkit's thread bridge."""
    if not _has_running_event_loop():
        return False

    if _prompt_supports_in_thread(session):
        return True

    raise RuntimeError(
        "Interactive prompt backend cannot run within an active event loop. "
        "Update prompt_toolkit or use a loop-safe prompt path."
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
    """Refresh the completion menu after short idle gaps to limit redraw churn."""

    pending_handle: list[object | None] = [None]

    def _flush() -> None:
        pending_handle[0] = None
        refresher(session.default_buffer)

    def _on_text_changed(_buffer) -> None:
        app = get_app_or_none()
        loop = app.loop if app is not None else None
        if loop is None or COMPLETION_MENU_REFRESH_DEBOUNCE_SEC <= 0:
            refresher(session.default_buffer)
            return
        is_closed = getattr(loop, "is_closed", None)
        if callable(is_closed) and is_closed():
            refresher(session.default_buffer)
            return

        existing = pending_handle[0]
        if existing is not None:
            cancel = getattr(existing, "cancel", None)
            if callable(cancel):
                cancel()
            pending_handle[0] = None

        pending_handle[0] = loop.call_later(
            COMPLETION_MENU_REFRESH_DEBOUNCE_SEC,
            _flush,
        )

    session.default_buffer.on_text_changed += _on_text_changed


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


def _completion_summary(spec: CommandSpec) -> str:
    """Render concise completion metadata from command metadata."""
    if spec.argument_hint:
        return f"{spec.summary} ({spec.argument_hint})"
    return spec.summary


def _build_status_toolbar(
    toolbar_provider: Callable[[], str] | None,
) -> Callable[[], str] | None:
    """Wrap a dynamic toolbar provider for prompt_toolkit."""
    if toolbar_provider is None:
        return None

    def _toolbar() -> str:
        try:
            return toolbar_provider()
        except Exception:
            return ""

    return _toolbar


def _prompt_value(
    message: str,
    *,
    default: str | None,
    password: bool,
    toolbar: str,
) -> str | None:
    """Prompt for one value using prompt-toolkit when available."""
    if not supports_interactive_prompt():
        try:
            value = LinePromptSession(InMemoryHistory()).prompt(
                _format_prompt_message(message, default)
            )
        except (EOFError, KeyboardInterrupt):
            return None
        return value or default

    try:
        session: PromptSession[str] = PromptSession(
            key_bindings=_build_prompt_key_bindings(),
            bottom_toolbar=toolbar,
            style=_PROMPT_STYLE,
        )
        if _should_use_in_thread_prompt(session):
            value = session.prompt(
                f"{message}: ",
                default=default or "",
                is_password=password,
                in_thread=True,
            )
        else:
            value = session.prompt(
                f"{message}: ",
                default=default or "",
                is_password=password,
            )
    except (EOFError, KeyboardInterrupt):
        return None
    return value or default


def _format_prompt_message(message: str, default: str | None) -> str:
    """Render a consistent input prompt label."""
    if default:
        return f"{message} [{default}]: "
    return f"{message}: "


def _terminal_columns() -> int:
    """Return terminal columns with a stable default for non-interactive paths."""
    columns = get_terminal_size(fallback=(NARROW_TERMINAL_WIDTH, 24)).columns
    if columns <= 0:
        return NARROW_TERMINAL_WIDTH
    return columns


def _menu_height_for_terminal(default_height: int) -> int:
    """Reduce completion menu height on narrow terminals."""
    columns = _terminal_columns()
    if columns < NARROW_TERMINAL_WIDTH:
        return min(default_height, NARROW_MENU_HEIGHT)
    return default_height


def _menu_line_width() -> int:
    """Width budget for completion item labels."""
    return max(_terminal_columns() - 24, 16)


def _menu_meta_width() -> int:
    """Width budget for completion metadata descriptions."""
    return max(_terminal_columns() - 28, 14)


def _truncate_with_ellipsis(text: str, width: int) -> str:
    """Truncate text for completion rows while preserving readability."""
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width <= 2:
        return text[:width]
    return f"{text[: width - 1]}…"
