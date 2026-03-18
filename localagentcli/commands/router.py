"""CommandRouter — registry, dispatch, and parsing for slash commands."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class CommandResult:
    """Result of a command execution."""

    success: bool
    message: str
    data: dict | None = None

    @classmethod
    def ok(cls, message: str, data: dict | None = None) -> CommandResult:
        return cls(success=True, message=message, data=data)

    @classmethod
    def error(cls, message: str) -> CommandResult:
        return cls(success=False, message=message)


class CommandHandler(ABC):
    """Base class for all command handlers."""

    @abstractmethod
    def execute(self, args: list[str]) -> CommandResult:
        """Execute the command with the given arguments."""
        ...

    @abstractmethod
    def help_text(self) -> str:
        """Return help text for this command."""
        ...


class CommandRouter:
    """Registry and dispatcher for slash commands."""

    def __init__(self):
        self._commands: dict[str, CommandHandler] = {}

    def register(self, name: str, handler: CommandHandler) -> None:
        """Register a command handler.

        Name can be multi-word for subcommands (e.g., 'session save').
        """
        self._commands[name] = handler

    def dispatch(self, input_line: str) -> CommandResult:
        """Parse input and dispatch to the registered handler.

        Tries two-word subcommand match first, then falls back to one-word.
        """
        parts = input_line.strip().split()
        if not parts:
            return CommandResult.error("Empty command.")

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
            return CommandResult.error(f"/{command_name} requires a subcommand: {subs}")

        return CommandResult.error(f"Unknown command: /{command_name}")

    def get_commands(self) -> dict[str, CommandHandler]:
        """Return the command registry."""
        return dict(self._commands)

    def get_completions(self) -> list[str]:
        """Return all registered command names prefixed with /."""
        return [f"/{name}" for name in sorted(self._commands.keys())]
