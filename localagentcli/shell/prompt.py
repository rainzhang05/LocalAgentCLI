"""Prompt handling and command tab completion."""

from __future__ import annotations

from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.history import FileHistory

from localagentcli.commands.router import CommandRouter


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
    history_file: Path,
) -> PromptSession:
    """Create a configured PromptSession with history and tab completion."""
    history_file.parent.mkdir(parents=True, exist_ok=True)

    return PromptSession(
        history=FileHistory(str(history_file)),
        completer=CommandCompleter(router),
        complete_while_typing=False,
    )
