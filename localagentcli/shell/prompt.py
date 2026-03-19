"""Prompt handling, history management, and command tab completion."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.history import InMemoryHistory

from localagentcli.commands.router import CommandRouter

MAX_INPUT_HISTORY = 1000


class CommandCompleter(Completer):
    """Tab completion for slash commands."""

    def __init__(self, router: CommandRouter):
        self._router = router

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor

        if not text.startswith("/"):
            return

        # Get all registered command completions
        completions = self._router.get_completions()

        # Match against current input
        for cmd in completions:
            if cmd.startswith(text):
                # Yield the remaining portion after what's already typed
                yield Completion(cmd, start_position=-len(text))


def create_prompt_session(
    router: CommandRouter,
    history_source: Path | Sequence[str] | None = None,
) -> PromptSession:
    """Create a configured PromptSession with session-backed history."""
    history = InMemoryHistory()

    if isinstance(history_source, Path):
        history_source.parent.mkdir(parents=True, exist_ok=True)
    elif history_source is not None:
        for item in list(history_source)[-MAX_INPUT_HISTORY:]:
            history.append_string(item)

    return PromptSession(
        history=history,
        completer=CommandCompleter(router),
        complete_while_typing=False,
    )


def get_prompt_history_strings(prompt_session: PromptSession) -> list[str]:
    """Return the current prompt history as a bounded list of strings."""
    history = getattr(prompt_session, "history", None)
    if history is None or not hasattr(history, "get_strings"):
        return []
    return list(history.get_strings())[-MAX_INPUT_HISTORY:]
