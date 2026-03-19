"""/help command handler."""

from __future__ import annotations

from localagentcli.commands.router import CommandHandler, CommandResult, CommandRouter, CommandSpec

GROUP_ORDER = ("System", "Target", "Mode", "Agent", "Session", "Model", "Provider")


class HelpHandler(CommandHandler):
    """Display help for all commands or a specific command."""

    def __init__(self, router: CommandRouter):
        self._router = router

    def execute(self, args: list[str]) -> CommandResult:
        if args:
            return self._command_help(args[0])
        return self._all_help()

    def _all_help(self) -> CommandResult:
        commands = self._router.get_commands()
        grouped = self._group_visible_commands(commands)
        lines = ["Available commands:", ""]

        ordered_groups = list(GROUP_ORDER) + sorted(set(grouped).difference(GROUP_ORDER))
        for group_name in ordered_groups:
            entries = grouped.get(group_name, [])
            if not entries:
                continue
            lines.append(f"  {group_name}:")
            for name, spec in entries:
                command = f"/{name}"
                if spec.argument_hint:
                    command = f"{command} {spec.argument_hint}"
                lines.append(f"    {command:<28s} {spec.summary}")
            lines.append("")

        lines.append("Type /help <command> for detailed help on a specific command.")
        return CommandResult.ok("\n".join(lines))

    def _command_help(self, name: str) -> CommandResult:
        commands = self._router.get_commands()

        # Try exact match
        if name in commands:
            return CommandResult.ok(_format_command_help(name, commands[name].describe()))

        # Try with subcommands
        matching = {k: v for k, v in commands.items() if k.startswith(f"{name} ")}
        if matching:
            lines = [f"/{name} subcommands:", ""]
            for cmd_name, handler in sorted(matching.items()):
                spec = handler.describe()
                command = f"/{cmd_name}"
                if spec.argument_hint:
                    command = f"{command} {spec.argument_hint}"
                lines.append(f"  {command:<28s} {spec.summary}")
            return CommandResult.ok("\n".join(lines))

        return CommandResult.error(
            f"Unknown command: /{name}\nUse /help to see all available commands."
        )

    def describe(self) -> CommandSpec:
        return CommandSpec(
            group="System",
            summary="Show grouped command help or detailed help for one command.",
            usage="/help [command]",
            argument_hint="[command]",
            details="Without an argument, /help shows the visible command catalog.",
            examples=("/help", "/help set", "/help providers"),
        )

    def _group_visible_commands(
        self,
        commands: dict[str, CommandHandler],
    ) -> dict[str, list[tuple[str, CommandSpec]]]:
        grouped: dict[str, list[tuple[str, CommandSpec]]] = {}
        for name, handler in sorted(commands.items()):
            if not self._router.is_visible(name):
                continue
            spec = handler.describe()
            grouped.setdefault(spec.group, []).append((name, spec))
        return grouped


def register(router: CommandRouter) -> None:
    """Register the /help command."""
    router.register("help", HelpHandler(router))


def _format_command_help(name: str, spec: CommandSpec) -> str:
    """Render a structured command-help block from command metadata."""
    lines = [f"/{name}", "", spec.summary, f"Usage: {spec.usage}"]
    if spec.details:
        lines.extend(["", spec.details])
    if spec.examples:
        lines.extend(["", "Examples:"])
        lines.extend(f"  {example}" for example in spec.examples)
    return "\n".join(lines)
