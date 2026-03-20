"""CommandRouter — registry, dispatch, and parsing for slash commands."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from difflib import get_close_matches
from typing import Literal

CommandPresentation = Literal["plain", "status", "success", "warning", "error"]


@dataclass(frozen=True)
class CommandSpec:
    """Operator-facing metadata for one slash command."""

    group: str
    summary: str
    usage: str
    argument_hint: str = ""
    details: str = ""
    examples: tuple[str, ...] = ()


@dataclass
class CommandResult:
    """Result of a command execution."""

    success: bool
    message: str
    data: dict | None = None
    presentation: CommandPresentation = "plain"
    body: str | None = None

    @classmethod
    def ok(
        cls,
        message: str,
        data: dict | None = None,
        *,
        presentation: CommandPresentation = "plain",
        body: str | None = None,
    ) -> CommandResult:
        return cls(
            success=True,
            message=message,
            data=data,
            presentation=presentation,
            body=body,
        )

    @classmethod
    def error(
        cls,
        message: str,
        *,
        body: str | None = None,
    ) -> CommandResult:
        return cls(
            success=False,
            message=message,
            presentation="error",
            body=body,
        )


class CommandHandler(ABC):
    """Base class for all command handlers."""

    @abstractmethod
    def execute(self, args: list[str]) -> CommandResult:
        """Execute the command with the given arguments."""
        ...

    @abstractmethod
    def describe(self) -> CommandSpec:
        """Return metadata for help, completion, and command framing."""

    def help_text(self) -> str:
        """Render one consistent help block from the command metadata."""
        spec = self.describe()
        lines = [spec.summary, f"Usage: {spec.usage}"]
        if spec.details:
            lines.extend(["", spec.details])
        if spec.examples:
            lines.extend(["", "Examples:"])
            lines.extend(f"  {example}" for example in spec.examples)
        return "\n".join(lines)


class CommandRouter:
    """Registry and dispatcher for slash commands."""

    def __init__(self):
        self._commands: dict[str, CommandHandler] = {}
        self._menu_visible: dict[str, bool] = {}

    def register(
        self,
        name: str,
        handler: CommandHandler,
        *,
        visible_in_menu: bool = True,
    ) -> None:
        """Register a command handler.

        Name can be multi-word for subcommands (e.g., 'session save').
        """
        self._commands[name] = handler
        self._menu_visible[name] = visible_in_menu

    def dispatch(self, input_line: str) -> CommandResult:
        """Parse input and dispatch to the registered handler.

        Tries two-word subcommand match first, then falls back to one-word.
        """
        parts = input_line.strip().split()
        if not parts:
            return CommandResult.error("Command is empty. Use /help to see available commands.")

        command_name = parts[0]

        # Try two-word subcommand first (e.g., "session save")
        if len(parts) > 1:
            subcommand_name = f"{parts[0]} {parts[1]}"
            if subcommand_name in self._commands:
                return self._commands[subcommand_name].execute(parts[2:])

        # Try one-word command
        if command_name in self._commands:
            return self._commands[command_name].execute(parts[1:])

        # Check if parent command exists with subcommands
        subcommands = [name for name in self._commands if name.startswith(f"{command_name} ")]
        if subcommands:
            subs = ", ".join(name.split(" ", 1)[1] for name in sorted(subcommands))
            return CommandResult.error(
                f"/{command_name} requires a subcommand: {subs}. "
                f"Use /help {command_name} for details."
            )

        suggestion = self._suggest_command(command_name)
        if suggestion:
            return CommandResult.error(
                f"Unknown command: /{command_name}. Did you mean /{suggestion}? "
                "Use /help to see all commands."
            )
        return CommandResult.error(
            f"Unknown command: /{command_name}. Use /help to see all commands."
        )

    def _suggest_command(self, command_name: str) -> str | None:
        """Return the closest command name for user-facing suggestions."""
        roots = sorted({name.split(" ", 1)[0] for name in self._commands})
        root_match = get_close_matches(command_name, roots, n=1, cutoff=0.6)
        if root_match:
            return root_match[0]
        full_matches = get_close_matches(command_name, sorted(self._commands), n=1, cutoff=0.7)
        return full_matches[0] if full_matches else None

    def get_commands(self) -> dict[str, CommandHandler]:
        """Return the command registry."""
        return dict(self._commands)

    def is_visible(self, name: str) -> bool:
        """Return whether a command should appear in menus and help."""
        return self._menu_visible.get(name, True)

    def set_visibility(self, name: str, visible: bool) -> None:
        """Update the menu/help visibility of a registered command."""
        if name in self._commands:
            self._menu_visible[name] = visible

    def get_visible_commands(self) -> dict[str, CommandHandler]:
        """Return only commands that should appear in the live slash menu."""
        return {
            name: handler
            for name, handler in self._commands.items()
            if self._menu_visible.get(name, True)
        }

    def get_completions(self) -> list[str]:
        """Return all registered command names prefixed with /."""
        return [f"/{name}" for name in sorted(self.get_visible_commands().keys())]
