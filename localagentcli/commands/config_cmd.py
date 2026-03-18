"""/config command handler."""

from __future__ import annotations

from localagentcli.commands.router import CommandHandler, CommandResult, CommandRouter
from localagentcli.config.manager import ConfigManager


class ConfigHandler(CommandHandler):
    """Read or write configuration values."""

    def __init__(self, config: ConfigManager):
        self._config = config

    def execute(self, args: list[str]) -> CommandResult:
        if not args:
            return self._show_all()
        if len(args) == 1:
            return self._show_key(args[0])
        return self._set_key(args[0], args[1])

    def _show_all(self) -> CommandResult:
        config = self._config.get_all()
        lines = ["Configuration:", ""]
        for section, values in sorted(config.items()):
            if isinstance(values, dict) and values:
                lines.append(f"  [{section}]")
                for key, value in sorted(values.items()):
                    if isinstance(value, dict):
                        continue  # Skip nested tables like [providers.*]
                    lines.append(f"    {key} = {_format_value(value)}")
                lines.append("")
        return CommandResult.ok("\n".join(lines))

    def _show_key(self, key: str) -> CommandResult:
        value = self._config.get(key)
        if value is None:
            return CommandResult.error(
                f"Unknown config key: '{key}'\n"
                "Use /config to see all available keys."
            )
        return CommandResult.ok(f"{key} = {_format_value(value)}")

    def _set_key(self, key: str, value: str) -> CommandResult:
        try:
            self._config.set(key, value)
        except ValueError as e:
            return CommandResult.error(str(e))
        display_value = _format_value(self._config.get(key))
        return CommandResult.ok(f"Set {key} = {display_value}")

    def help_text(self) -> str:
        return (
            "Read or write configuration values.\n"
            "Usage:\n"
            "  /config                    Show all config values\n"
            "  /config <key>              Show value for a specific key\n"
            "  /config <key> <value>      Set a config value\n"
            "\n"
            "Keys use dotted notation (e.g., general.default_mode, generation.temperature)."
        )


def _format_value(value: object) -> str:
    """Format a config value for display."""
    if isinstance(value, str):
        return f'"{value}"'
    return str(value)


def register(router: CommandRouter, config: ConfigManager) -> None:
    """Register the /config command."""
    router.register("config", ConfigHandler(config))
