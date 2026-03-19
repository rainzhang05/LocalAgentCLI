"""/config command handler."""

from __future__ import annotations

from localagentcli.commands.router import CommandHandler, CommandResult, CommandRouter, CommandSpec
from localagentcli.config.defaults import CONFIG_SCHEMA
from localagentcli.config.manager import ConfigManager
from localagentcli.shell.prompt import (
    SelectionOption,
    prompt_text,
    select_option,
    supports_interactive_prompt,
)

_CONFIG_CHOICES: dict[str, tuple[str, ...]] = {
    "general.default_mode": ("agent", "chat"),
    "general.logging_level": ("normal", "verbose", "debug"),
    "safety.approval_mode": ("balanced", "autonomous"),
}


class ConfigHandler(CommandHandler):
    """Read or write configuration values."""

    def __init__(self, config: ConfigManager):
        self._config = config

    def execute(self, args: list[str]) -> CommandResult:
        if not args:
            if supports_interactive_prompt():
                return self._interactive_edit()
            return self._show_all()
        if len(args) == 1:
            return self._show_key(args[0])
        return self._set_key(args[0], " ".join(args[1:]))

    def _interactive_edit(self) -> CommandResult:
        """Open an interactive config picker for viewing or editing values."""
        selection = select_option(
            "Choose a config key",
            _config_selection_options(self._config),
            default=None,
        )
        if selection is None:
            return CommandResult.ok("Config edit cancelled.", presentation="warning")
        if selection.value == "__show_all__":
            return self._show_all()
        return self._prompt_for_value(selection.value)

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
                f"Unknown config key: '{key}'\nUse /config to see all available keys."
            )
        return CommandResult.ok(f"{key} = {_format_value(value)}")

    def _set_key(self, key: str, value: str) -> CommandResult:
        try:
            self._config.set(key, value)
        except ValueError as e:
            return CommandResult.error(str(e))
        display_value = _format_value(self._config.get(key))
        return CommandResult.ok(f"Set {key} = {display_value}", presentation="success")

    def _prompt_for_value(self, key: str) -> CommandResult:
        """Prompt for a valid value and persist it."""
        current_value = self._config.get(key)
        choices = _CONFIG_CHOICES.get(key)
        if choices:
            selection = select_option(
                f"Choose value for {key}",
                [
                    SelectionOption(
                        value=choice,
                        label=choice,
                        description=_choice_description(choice, current_value),
                    )
                    for choice in choices
                ],
                default=str(current_value) if current_value is not None else None,
            )
            if selection is None:
                return CommandResult.ok("Config edit cancelled.", presentation="warning")
            return self._set_key(key, selection.value)

        value = prompt_text(
            f"Set {key}",
            default=str(current_value) if current_value is not None else "",
        )
        if value is None:
            return CommandResult.ok("Config edit cancelled.", presentation="warning")
        return self._set_key(key, value)

    def describe(self) -> CommandSpec:
        return CommandSpec(
            group="System",
            summary="Read or edit configuration values.",
            usage="/config [key] [value]",
            argument_hint="[key] [value]",
            details=(
                "Run /config with no arguments to open the interactive config editor in a "
                "TTY, or use dotted keys for direct reads and writes."
            ),
            examples=(
                "/config",
                "/config general.default_mode",
                "/config generation.temperature 0.2",
            ),
        )


def _format_value(value: object) -> str:
    """Format a config value for display."""
    if isinstance(value, str):
        return f'"{value}"'
    return str(value)


def _choice_description(choice: str, current_value: object) -> str:
    """Describe whether a choice matches the current value."""
    return "Current value" if str(current_value) == choice else ""


def _config_selection_options(config: ConfigManager) -> list[SelectionOption]:
    """Build the interactive config picker options."""
    options = [
        SelectionOption(
            value="__show_all__",
            label="Show all values",
            description="Display the current config without editing anything.",
            aliases=("view", "list"),
        )
    ]

    for key in sorted(CONFIG_SCHEMA):
        current_value = config.get(key)
        options.append(
            SelectionOption(
                value=key,
                label=key,
                description=f"Current: {_format_value(current_value)}",
                aliases=tuple(key.split(".")),
            )
        )
    return options


def register(router: CommandRouter, config: ConfigManager) -> None:
    """Register the /config command."""
    router.register("config", ConfigHandler(config))
