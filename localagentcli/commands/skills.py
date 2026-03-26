"""/skills command handlers — local skill list/install/remove."""

from __future__ import annotations

from pathlib import Path

from localagentcli.commands.router import CommandHandler, CommandResult, CommandRouter, CommandSpec
from localagentcli.skills import SkillsManager


class SkillsParentHandler(CommandHandler):
    def execute(self, args: list[str]) -> CommandResult:
        return CommandResult.error(
            "/skills requires a subcommand: list, install, remove, sync-remote. "
            "Use /help skills for details."
        )

    def describe(self) -> CommandSpec:
        return CommandSpec(
            group="Skills",
            summary="Manage local skill overlays.",
            usage="/skills <list|install|remove|sync-remote>",
            argument_hint="<subcommand>",
            details=(
                "Installed skills live under ~/.localagent/skills and are merged into "
                "system instructions as prompt overlays."
            ),
        )


class SkillsListHandler(CommandHandler):
    def __init__(self, manager: SkillsManager):
        self._manager = manager

    def execute(self, args: list[str]) -> CommandResult:
        skills = self._manager.list_installed()
        if not skills:
            return CommandResult.ok(
                "No installed skills. Use /skills install <path-to-SKILL.md-or-dir>.",
                presentation="status",
            )

        lines = ["Installed skills:", "", f"  {'Name':<24s} Path"]
        lines.append(f"  {'─' * 24} {'─' * 40}")
        for skill in skills:
            lines.append(f"  {skill.name:<24s} {skill.path}")
        return CommandResult.ok("\n".join(lines))

    def describe(self) -> CommandSpec:
        return CommandSpec(
            group="Skills",
            summary="List installed skills.",
            usage="/skills list",
        )


class SkillsInstallHandler(CommandHandler):
    def __init__(self, manager: SkillsManager):
        self._manager = manager

    def execute(self, args: list[str]) -> CommandResult:
        if not args:
            return CommandResult.error(
                "Skill path is required. Usage: /skills install <path> [name]"
            )

        source = Path(args[0])
        name = args[1] if len(args) > 1 else None
        try:
            installed = self._manager.install_from_path(source, name=name)
        except (FileNotFoundError, FileExistsError, ValueError) as exc:
            return CommandResult.error(str(exc))

        return CommandResult.ok(
            f"Installed skill '{installed.name}'.",
            presentation="success",
        )

    def describe(self) -> CommandSpec:
        return CommandSpec(
            group="Skills",
            summary="Install a skill from SKILL.md file or skill directory.",
            usage="/skills install <path> [name]",
            argument_hint="<path> [name]",
        )


class SkillsRemoveHandler(CommandHandler):
    def __init__(self, manager: SkillsManager):
        self._manager = manager

    def execute(self, args: list[str]) -> CommandResult:
        if not args:
            return CommandResult.error("Skill name is required. Usage: /skills remove <name>")

        try:
            removed = self._manager.remove(args[0])
        except (FileNotFoundError, ValueError) as exc:
            return CommandResult.error(str(exc))

        return CommandResult.ok(
            f"Removed skill '{removed.name}'.",
            presentation="success",
        )

    def describe(self) -> CommandSpec:
        return CommandSpec(
            group="Skills",
            summary="Remove an installed skill.",
            usage="/skills remove <name>",
            argument_hint="<name>",
        )


class SkillsSyncRemoteHandler(CommandHandler):
    def __init__(self, manager: SkillsManager):
        self._manager = manager

    def execute(self, args: list[str]) -> CommandResult:
        if not args:
            return CommandResult.error(
                "Manifest URL is required. Usage: /skills sync-remote <manifest-url>"
            )
        try:
            synced = self._manager.sync_from_manifest_url(args[0])
        except Exception as exc:
            return CommandResult.error(f"Remote skills sync failed: {exc}")
        if not synced:
            return CommandResult.ok(
                "No new remote skills were installed.",
                presentation="status",
            )
        names = ", ".join(skill.name for skill in synced)
        return CommandResult.ok(
            f"Installed {len(synced)} skill(s) from remote manifest: {names}",
            presentation="success",
        )

    def describe(self) -> CommandSpec:
        return CommandSpec(
            group="Skills",
            summary="Install skills from a remote manifest URL.",
            usage="/skills sync-remote <manifest-url>",
            argument_hint="<manifest-url>",
        )


def register(router: CommandRouter, manager: SkillsManager) -> None:
    router.register("skills", SkillsParentHandler(), visible_in_menu=False)
    router.register("skills list", SkillsListHandler(manager))
    router.register("skills install", SkillsInstallHandler(manager))
    router.register("skills remove", SkillsRemoveHandler(manager))
    router.register("skills sync-remote", SkillsSyncRemoteHandler(manager))
