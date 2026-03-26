"""/plugin command handlers — local plugin list/install/remove."""

from __future__ import annotations

from pathlib import Path

from localagentcli.commands.router import CommandHandler, CommandResult, CommandRouter, CommandSpec
from localagentcli.plugins import PluginManager


class PluginParentHandler(CommandHandler):
    """Parent handler that explains plugin subcommands."""

    def execute(self, args: list[str]) -> CommandResult:
        return CommandResult.error(
            "/plugin requires a subcommand: list, install, remove. Use /help plugin for details."
        )

    def describe(self) -> CommandSpec:
        return CommandSpec(
            group="Plugin",
            summary="Manage local plugins.",
            usage="/plugin <list|install|remove>",
            argument_hint="<subcommand>",
            details=(
                "Plugins are local artifacts stored under ~/.localagent/plugins. "
                "This baseline slice supports list/install/remove only."
            ),
        )


class PluginListHandler(CommandHandler):
    def __init__(self, manager: PluginManager):
        self._manager = manager

    def execute(self, args: list[str]) -> CommandResult:
        plugins = self._manager.list_plugins()
        if not plugins:
            return CommandResult.ok(
                "No local plugins installed. Use /plugin install <path>.",
                presentation="status",
            )

        lines = ["Installed plugins:", "", f"  {'Name':<24s} {'Kind':<10s} Path"]
        lines.append(f"  {'─' * 24} {'─' * 10} {'─' * 40}")
        for plugin in plugins:
            lines.append(f"  {plugin.name:<24s} {plugin.kind:<10s} {plugin.path}")
        return CommandResult.ok("\n".join(lines))

    def describe(self) -> CommandSpec:
        return CommandSpec(
            group="Plugin",
            summary="List locally installed plugins.",
            usage="/plugin list",
        )


class PluginInstallHandler(CommandHandler):
    def __init__(self, manager: PluginManager):
        self._manager = manager

    def execute(self, args: list[str]) -> CommandResult:
        if not args:
            return CommandResult.error("Plugin path is required. Usage: /plugin install <path>")

        source = Path(args[0])
        name = args[1] if len(args) > 1 else None
        try:
            installed = self._manager.install_from_path(source, name=name)
        except (FileNotFoundError, FileExistsError, ValueError) as exc:
            return CommandResult.error(str(exc))

        return CommandResult.ok(
            f"Installed plugin '{installed.name}' from {source}.",
            presentation="success",
        )

    def describe(self) -> CommandSpec:
        return CommandSpec(
            group="Plugin",
            summary="Install a local plugin from a file or directory path.",
            usage="/plugin install <path> [name]",
            argument_hint="<path> [name]",
        )


class PluginRemoveHandler(CommandHandler):
    def __init__(self, manager: PluginManager):
        self._manager = manager

    def execute(self, args: list[str]) -> CommandResult:
        if not args:
            return CommandResult.error("Plugin name is required. Usage: /plugin remove <name>")

        try:
            removed = self._manager.remove(args[0])
        except (FileNotFoundError, ValueError) as exc:
            return CommandResult.error(str(exc))

        return CommandResult.ok(
            f"Removed plugin '{removed.name}'.",
            presentation="success",
        )

    def describe(self) -> CommandSpec:
        return CommandSpec(
            group="Plugin",
            summary="Remove a locally installed plugin.",
            usage="/plugin remove <name>",
            argument_hint="<name>",
        )


def register(router: CommandRouter, manager: PluginManager) -> None:
    """Register /plugin command group."""
    router.register("plugin", PluginParentHandler(), visible_in_menu=False)
    router.register("plugin list", PluginListHandler(manager))
    router.register("plugin install", PluginInstallHandler(manager))
    router.register("plugin remove", PluginRemoveHandler(manager))
