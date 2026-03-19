"""/models command handlers — list, search, install, remove, use, inspect."""

from __future__ import annotations

import shutil
from pathlib import Path

from rich.console import Console

from localagentcli.commands.router import CommandHandler, CommandResult, CommandRouter
from localagentcli.models.detector import HardwareDetector
from localagentcli.models.installer import ModelInstaller, _fmt_size
from localagentcli.models.registry import ModelRegistry
from localagentcli.session.manager import SessionManager


def _parse_name_version(arg: str) -> tuple[str, str | None]:
    """Parse 'name@v1' syntax into (name, version) or (name, None)."""
    if "@" in arg:
        name, version = arg.rsplit("@", 1)
        return name, version
    return arg, None


class ModelsParentHandler(CommandHandler):
    """Parent handler that shows subcommand help."""

    def execute(self, args: list[str]) -> CommandResult:
        return CommandResult.error(
            "/models requires a subcommand: list, search, install, remove, use, inspect"
        )

    def help_text(self) -> str:
        return (
            "Manage local models.\n"
            "Subcommands:\n"
            "  /models list                    List installed models\n"
            "  /models search <query>          Search installed models\n"
            "  /models install hf <repo>       Install from HuggingFace\n"
            "  /models install url <url>       Install from URL\n"
            "  /models remove <name[@version]> Remove a model\n"
            "  /models use <name[@version]>    Set active model\n"
            "  /models inspect <name[@version]> Show model details"
        )


class ModelsListHandler(CommandHandler):
    """List all installed models."""

    def __init__(self, registry: ModelRegistry):
        self._registry = registry

    def execute(self, args: list[str]) -> CommandResult:
        entries = self._registry.list_models()
        if not entries:
            return CommandResult.ok("No models installed. Use /models install to add one.")

        lines = ["Installed models:", ""]
        lines.append(f"  {'Name':<25s} {'Version':<10s} {'Format':<12s} {'Size':<12s} {'Backend'}")
        lines.append(f"  {'─' * 25} {'─' * 10} {'─' * 12} {'─' * 12} {'─' * 12}")
        for entry in entries:
            backend = entry.metadata.get("backend", entry.format)
            lines.append(
                f"  {entry.name:<25s} {entry.version:<10s} "
                f"{entry.format:<12s} {_fmt_size(entry.size_bytes):<12s} {backend}"
            )
        return CommandResult.ok("\n".join(lines))

    def help_text(self) -> str:
        return "List all installed local models.\nUsage: /models list"


class ModelsSearchHandler(CommandHandler):
    """Search installed models by name or metadata."""

    def __init__(self, registry: ModelRegistry):
        self._registry = registry

    def execute(self, args: list[str]) -> CommandResult:
        if not args:
            return CommandResult.error("Search query required.\nUsage: /models search <query>")
        query = " ".join(args)
        results = self._registry.search(query)
        if not results:
            return CommandResult.ok(f"No models matching '{query}'.")

        lines = [f"Search results for '{query}':", ""]
        for entry in results:
            lines.append(
                f"  {entry.name} ({entry.version}) — {entry.format}, {_fmt_size(entry.size_bytes)}"
            )
        return CommandResult.ok("\n".join(lines))

    def help_text(self) -> str:
        return "Search installed models.\nUsage: /models search <query>"


class ModelsInstallHandler(CommandHandler):
    """Install a model from HuggingFace or URL."""

    def __init__(self, installer: ModelInstaller):
        self._installer = installer

    def execute(self, args: list[str]) -> CommandResult:
        if len(args) < 2:
            return CommandResult.error(
                "Source type and location required.\n"
                "Usage: /models install hf <repo>\n"
                "       /models install url <url>"
            )

        source_type = args[0].lower()
        location = args[1]
        name = args[2] if len(args) > 2 else None

        if source_type == "hf":
            result = self._installer.install_from_hf(location, name=name)
        elif source_type == "url":
            result = self._installer.install_from_url(location, name=name)
        else:
            return CommandResult.error(f"Unknown source type '{source_type}'. Use 'hf' or 'url'.")

        if result.success:
            return CommandResult.ok(result.message)
        return CommandResult.error(result.message)

    def help_text(self) -> str:
        return (
            "Install a model.\n"
            "Usage: /models install hf <repo>        Install from HuggingFace Hub\n"
            "       /models install url <url>        Install from direct URL\n"
            "       /models install hf <repo> <name> Install with custom name"
        )


class ModelsRemoveHandler(CommandHandler):
    """Remove an installed model."""

    def __init__(self, registry: ModelRegistry, models_dir: Path):
        self._registry = registry
        self._models_dir = models_dir

    def execute(self, args: list[str]) -> CommandResult:
        if not args:
            return CommandResult.error(
                "Model name required.\nUsage: /models remove <name[@version]>"
            )

        name, version = _parse_name_version(args[0])

        # Check model exists
        if version:
            entry = self._registry.get_model(name, version)
            if entry is None:
                return CommandResult.error(
                    f"Model '{name}' version '{version}' not found.\n"
                    "Use /models list to see installed models."
                )
        else:
            entry = self._registry.get_model(name)
            if entry is None:
                return CommandResult.error(
                    f"Model '{name}' not found.\nUse /models list to see installed models."
                )

        try:
            self._registry.unregister(name, version)
        except KeyError as e:
            return CommandResult.error(str(e))

        # Delete files
        if version:
            model_dir = self._models_dir / name / version
        else:
            model_dir = self._models_dir / name

        if model_dir.exists():
            shutil.rmtree(model_dir)

        label = f"{name}@{version}" if version else name
        return CommandResult.ok(f"Model '{label}' removed.")

    def help_text(self) -> str:
        return (
            "Remove an installed model.\n"
            "Usage: /models remove <name>        Remove all versions\n"
            "       /models remove <name@v1>     Remove specific version"
        )


class ModelsUseHandler(CommandHandler):
    """Set the active model for the current session."""

    def __init__(
        self,
        registry: ModelRegistry,
        hardware_detector: HardwareDetector,
        session_manager: SessionManager,
        console: Console,
    ):
        self._registry = registry
        self._hw_detector = hardware_detector
        self._session_manager = session_manager
        self._console = console

    def execute(self, args: list[str]) -> CommandResult:
        if not args:
            return CommandResult.error("Model name required.\nUsage: /models use <name[@version]>")

        name, version = _parse_name_version(args[0])
        entry = self._registry.get_model(name, version)
        if entry is None:
            label = f"{name}@{version}" if version else name
            return CommandResult.error(
                f"Model '{label}' not found.\nUse /models list to see installed models."
            )

        # Hardware check
        can_run, warnings = self._hw_detector.can_run_model(entry.size_bytes)
        for warning in warnings:
            self._console.print(f"[yellow]⚠ {warning}[/yellow]")
        if not can_run:
            return CommandResult.error(
                "Model is too large for available hardware. "
                "Try a smaller quantization or use a remote provider."
            )

        # Set session model
        session = self._session_manager.current
        session.model = f"{entry.name}@{entry.version}"
        # Clear provider since we're using a local model
        session.provider = ""

        return CommandResult.ok(
            f"Active model set to '{entry.name}' ({entry.version}, {entry.format})."
        )

    def help_text(self) -> str:
        return (
            "Set the active model for this session.\n"
            "Usage: /models use <name>        Use latest version\n"
            "       /models use <name@v1>     Use specific version"
        )


class ModelsInspectHandler(CommandHandler):
    """Show detailed information about an installed model."""

    def __init__(self, registry: ModelRegistry):
        self._registry = registry

    def execute(self, args: list[str]) -> CommandResult:
        if not args:
            return CommandResult.error(
                "Model name required.\nUsage: /models inspect <name[@version]>"
            )

        name, version = _parse_name_version(args[0])
        entry = self._registry.get_model(name, version)
        if entry is None:
            label = f"{name}@{version}" if version else name
            return CommandResult.error(
                f"Model '{label}' not found.\nUse /models list to see installed models."
            )

        lines = [f"Model: {entry.name}", ""]
        lines.append(f"  Version:      {entry.version}")
        lines.append(f"  Format:       {entry.format}")
        lines.append(f"  Path:         {entry.path}")
        lines.append(f"  Size:         {_fmt_size(entry.size_bytes)}")
        lines.append(f"  Tool use:     {entry.capabilities.get('tool_use', False)}")
        lines.append(f"  Reasoning:    {entry.capabilities.get('reasoning', False)}")
        lines.append(f"  Streaming:    {entry.capabilities.get('streaming', True)}")

        if entry.metadata:
            lines.append("")
            lines.append("  Metadata:")
            for key, value in entry.metadata.items():
                lines.append(f"    {key}: {value}")

        return CommandResult.ok("\n".join(lines))

    def help_text(self) -> str:
        return "Show detailed model information.\nUsage: /models inspect <name[@version]>"


def register(
    router: CommandRouter,
    registry: ModelRegistry,
    installer: ModelInstaller,
    hardware_detector: HardwareDetector,
    session_manager: SessionManager,
    console: Console,
    models_dir: Path,
) -> None:
    """Register all /models subcommands."""
    router.register("models", ModelsParentHandler())
    router.register("models list", ModelsListHandler(registry))
    router.register("models search", ModelsSearchHandler(registry))
    router.register("models install", ModelsInstallHandler(installer))
    router.register("models remove", ModelsRemoveHandler(registry, models_dir))
    router.register(
        "models use",
        ModelsUseHandler(registry, hardware_detector, session_manager, console),
    )
    router.register("models inspect", ModelsInspectHandler(registry))
