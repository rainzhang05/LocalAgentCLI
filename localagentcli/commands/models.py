"""/models command handlers — interactive picker, list, search, install, remove, use, inspect."""

from __future__ import annotations

import platform
import shutil
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console

from localagentcli.commands.router import CommandHandler, CommandResult, CommandRouter
from localagentcli.models.detector import HardwareDetector
from localagentcli.models.installer import ModelInstaller, _fmt_size
from localagentcli.models.registry import ModelEntry, ModelRegistry
from localagentcli.session.manager import SessionManager
from localagentcli.shell.prompt import SelectionOption, select_option, supports_interactive_prompt

ModelSelector = Callable[[str, Sequence[SelectionOption]], SelectionOption | None]

_BACK_SENTINEL = "__back__"
_CANCEL_SENTINEL = "__cancel__"


@dataclass(frozen=True)
class FeaturedModel:
    """Curated Hugging Face model option shown by the interactive /models picker."""

    backend: str
    family: str
    label: str
    repo: str
    install_name: str
    size_hint: str
    summary: str


_FEATURED_HF_MODELS: tuple[FeaturedModel, ...] = (
    FeaturedModel(
        backend="gguf",
        family="gpt-oss",
        label="GPT-OSS 20B (GGUF)",
        repo="unsloth/gpt-oss-20b-GGUF",
        install_name="gpt-oss-20b-gguf",
        size_hint="20B",
        summary="Quantized GPT-OSS build for llama.cpp-compatible runtimes.",
    ),
    FeaturedModel(
        backend="gguf",
        family="gpt-oss",
        label="GPT-OSS 120B (GGUF)",
        repo="unsloth/gpt-oss-120b-GGUF",
        install_name="gpt-oss-120b-gguf",
        size_hint="120B",
        summary="Large GPT-OSS GGUF build for high-memory machines.",
    ),
    FeaturedModel(
        backend="gguf",
        family="qwen",
        label="Qwen3 8B (GGUF)",
        repo="Qwen/Qwen3-8B-GGUF",
        install_name="qwen3-8b-gguf",
        size_hint="8B",
        summary="Balanced general-purpose GGUF build with broad hardware support.",
    ),
    FeaturedModel(
        backend="gguf",
        family="qwen",
        label="Qwen3 14B (GGUF)",
        repo="Qwen/Qwen3-14B-GGUF",
        install_name="qwen3-14b-gguf",
        size_hint="14B",
        summary="Stronger reasoning and coding quality in GGUF format.",
    ),
    FeaturedModel(
        backend="gguf",
        family="qwen",
        label="Qwen3 32B (GGUF)",
        repo="Qwen/Qwen3-32B-GGUF",
        install_name="qwen3-32b-gguf",
        size_hint="32B",
        summary="Large GGUF Qwen option for higher-end local setups.",
    ),
    FeaturedModel(
        backend="gguf",
        family="gemma",
        label="Gemma 3 12B Instruct (GGUF)",
        repo="google/gemma-3-12b-it-qat-q4_0-gguf",
        install_name="gemma-3-12b-it-gguf",
        size_hint="12B",
        summary="Google Gemma 3 quantized GGUF instruct model.",
    ),
    FeaturedModel(
        backend="gguf",
        family="gemma",
        label="Gemma 3 27B Instruct (GGUF)",
        repo="google/gemma-3-27b-it-qat-q4_0-gguf",
        install_name="gemma-3-27b-it-gguf",
        size_hint="27B",
        summary="Larger Gemma 3 GGUF option for stronger local reasoning.",
    ),
    FeaturedModel(
        backend="mlx",
        family="gpt-oss",
        label="GPT-OSS 20B (MLX 8-bit)",
        repo="lmstudio-community/gpt-oss-20b-MLX-8bit",
        install_name="gpt-oss-20b-mlx-8bit",
        size_hint="20B",
        summary="Apple Silicon tuned MLX build with 8-bit weights.",
    ),
    FeaturedModel(
        backend="mlx",
        family="gpt-oss",
        label="GPT-OSS 20B (MLX Q8)",
        repo="mlx-community/gpt-oss-20b-MXFP4-Q8",
        install_name="gpt-oss-20b-mlx-q8",
        size_hint="20B",
        summary="Alternative MLX GPT-OSS build from mlx-community.",
    ),
    FeaturedModel(
        backend="mlx",
        family="qwen",
        label="Qwen3 8B (MLX 4-bit)",
        repo="mlx-community/Qwen3-8B-4bit",
        install_name="qwen3-8b-mlx-4bit",
        size_hint="8B",
        summary="Compact Apple Silicon-friendly Qwen3 MLX build.",
    ),
    FeaturedModel(
        backend="mlx",
        family="qwen",
        label="Qwen3 14B (MLX 4-bit)",
        repo="mlx-community/Qwen3-14B-4bit",
        install_name="qwen3-14b-mlx-4bit",
        size_hint="14B",
        summary="Higher quality MLX Qwen3 model for Macs with more memory.",
    ),
    FeaturedModel(
        backend="mlx",
        family="gemma",
        label="Gemma 3 4B Instruct (MLX 4-bit)",
        repo="mlx-community/gemma-3-4b-it-4bit",
        install_name="gemma-3-4b-it-mlx-4bit",
        size_hint="4B",
        summary="Fast-entry MLX Gemma build for Apple Silicon laptops.",
    ),
    FeaturedModel(
        backend="mlx",
        family="gemma",
        label="Gemma 3 12B Instruct (MLX 4-bit)",
        repo="mlx-community/gemma-3-12b-it-4bit",
        install_name="gemma-3-12b-it-mlx-4bit",
        size_hint="12B",
        summary="Bigger MLX Gemma 3 option with stronger reasoning quality.",
    ),
    FeaturedModel(
        backend="safetensors",
        family="gpt-oss",
        label="GPT-OSS 20B (PyTorch)",
        repo="openai/gpt-oss-20b",
        install_name="gpt-oss-20b",
        size_hint="20B",
        summary="Official Hugging Face repo with standard PyTorch weights.",
    ),
    FeaturedModel(
        backend="safetensors",
        family="gpt-oss",
        label="GPT-OSS 120B (PyTorch)",
        repo="openai/gpt-oss-120b",
        install_name="gpt-oss-120b",
        size_hint="120B",
        summary="Largest official GPT-OSS weights; expect very high RAM/VRAM use.",
    ),
    FeaturedModel(
        backend="safetensors",
        family="qwen",
        label="Qwen3 8B (PyTorch)",
        repo="Qwen/Qwen3-8B",
        install_name="qwen3-8b",
        size_hint="8B",
        summary="Standard Transformers-compatible Qwen3 weights.",
    ),
    FeaturedModel(
        backend="safetensors",
        family="qwen",
        label="Qwen3 14B (PyTorch)",
        repo="Qwen/Qwen3-14B",
        install_name="qwen3-14b",
        size_hint="14B",
        summary="Larger Qwen3 PyTorch model for better reasoning and coding.",
    ),
    FeaturedModel(
        backend="safetensors",
        family="gemma",
        label="Gemma 3 4B Instruct (PyTorch)",
        repo="google/gemma-3-4b-it",
        install_name="gemma-3-4b-it",
        size_hint="4B",
        summary="Lightweight Gemma 3 instruct model in standard PyTorch format.",
    ),
    FeaturedModel(
        backend="safetensors",
        family="gemma",
        label="Gemma 3 12B Instruct (PyTorch)",
        repo="google/gemma-3-12b-it",
        install_name="gemma-3-12b-it",
        size_hint="12B",
        summary="Stronger Gemma 3 instruct model with standard weights.",
    ),
)

_BACKEND_LABELS = {
    "gguf": "GGUF",
    "mlx": "MLX",
    "safetensors": "PyTorch / Safetensors",
}

_BACKEND_DESCRIPTIONS = {
    "gguf": "Quantized llama.cpp-compatible models that run well across macOS, Linux, and Windows.",
    "mlx": "Apple Silicon optimized models for MLX. Best on modern Macs.",
    "safetensors": "Standard PyTorch / Transformers model repositories from Hugging Face.",
}

_FAMILY_LABELS = {
    "gpt-oss": "GPT-OSS",
    "qwen": "Qwen",
    "gemma": "Gemma",
}


def _parse_name_version(arg: str) -> tuple[str, str | None]:
    """Parse 'name@v1' syntax into (name, version) or (name, None)."""
    if "@" in arg:
        name, version = arg.rsplit("@", 1)
        return name, version
    return arg, None


class ModelsParentHandler(CommandHandler):
    """Parent handler that launches the interactive featured-model picker."""

    def __init__(
        self,
        installer: ModelInstaller,
        hardware_detector: HardwareDetector,
        session_manager: SessionManager,
        console: Console,
        selector: ModelSelector | None = None,
    ):
        self._installer = installer
        self._hw_detector = hardware_detector
        self._session_manager = session_manager
        self._console = console
        self._selector = selector or select_option

    def execute(self, args: list[str]) -> CommandResult:
        if args:
            return CommandResult.error(
                "/models does not accept arguments.\n"
                "Use /models to open the picker or a subcommand such as /models list."
            )
        if not supports_interactive_prompt():
            return CommandResult.ok(
                "Interactive model picker requires a terminal TTY.\n\n" + self.help_text()
            )

        selection = self._pick_featured_model()
        if selection is None:
            return CommandResult.ok("Model selection cancelled.")

        install_result = self._installer.install_from_hf(
            selection.repo,
            name=selection.install_name,
        )
        if not install_result.success or install_result.model_entry is None:
            return CommandResult.error(install_result.message)

        activation = _activate_model_entry(
            install_result.model_entry,
            self._hw_detector,
            self._session_manager,
            self._console,
        )
        if not activation.success:
            return CommandResult.error(
                f"Installed '{selection.label}' from {selection.repo}, but could not activate it.\n"
                f"{activation.message}"
            )

        return CommandResult.ok(
            f"Installed '{selection.label}' from {selection.repo}.\n{activation.message}"
        )

    def help_text(self) -> str:
        return (
            "Manage local models.\n"
            "/models opens an interactive Hugging Face picker with curated popular models.\n"
            "Subcommands:\n"
            "  /models list                    List installed models\n"
            "  /models search <query>          Search installed models\n"
            "  /models install hf <repo>       Install from HuggingFace\n"
            "  /models install url <url>       Install from URL\n"
            "  /models remove <name[@version]> Remove a model\n"
            "  /models inspect <name[@version]> Show model details\n"
            "Use /set to switch the active local or remote model."
        )

    def _pick_featured_model(self) -> FeaturedModel | None:
        backend = self._select_backend()
        if backend is None:
            return None

        while True:
            family = self._select_family(backend)
            if family is None:
                return None
            if family == _BACK_SENTINEL:
                backend = self._select_backend()
                if backend is None:
                    return None
                continue

            selection = self._select_model(backend, family)
            if selection is None:
                return None
            if selection == _BACK_SENTINEL:
                continue
            if isinstance(selection, FeaturedModel):
                return selection
            return None

    def _select_backend(self) -> str | None:
        choice = self._selector(
            "Choose a model runtime",
            _backend_options(),
        )
        return _resolve_flow_choice(choice)

    def _select_family(self, backend: str) -> str | None:
        models = _models_for_backend(backend)
        families = _ordered_unique(model.family for model in models)
        options = [
            SelectionOption(
                value=family,
                label=_FAMILY_LABELS.get(family, family.title()),
                description=(
                    f"{len([m for m in models if m.family == family])} curated "
                    f"{_BACKEND_LABELS[backend]} options"
                ),
                aliases=(family.replace("-", ""),),
            )
            for family in families
        ]
        options.extend(_flow_navigation_options(include_back=True))
        choice = self._selector(
            f"Choose a {_BACKEND_LABELS[backend]} model family",
            options,
        )
        return _resolve_flow_choice(choice)

    def _select_model(self, backend: str, family: str) -> FeaturedModel | str | None:
        models = [model for model in _models_for_backend(backend) if model.family == family]
        options = [
            SelectionOption(
                value=model.install_name,
                label=model.label,
                description=f"{model.size_hint} • {model.summary}",
                aliases=(model.family, model.repo, model.install_name),
            )
            for model in models
        ]
        options.extend(_flow_navigation_options(include_back=True))
        choice = self._selector(
            f"Choose a {_FAMILY_LABELS.get(family, family.title())} model",
            options,
        )
        resolved = _resolve_flow_choice(choice)
        if resolved in {None, _BACK_SENTINEL}:
            return resolved

        return next((model for model in models if model.install_name == resolved), None)


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
            if not supports_interactive_prompt():
                return CommandResult.error(
                    "Model name required.\nUsage: /models remove <name[@version]>"
                )
            if not self._registry.list_models():
                return CommandResult.ok("No models installed. Use /models install to add one.")
            selection = _select_installed_model_option(
                self._registry,
                "Choose a model to remove",
            )
            if selection is None:
                return CommandResult.ok("Model removal cancelled.")
            args = [selection.value]

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
            if not supports_interactive_prompt():
                return CommandResult.error(
                    "Model name required.\nUsage: /models use <name[@version]>"
                )
            if not self._registry.list_models():
                return CommandResult.ok("No models installed. Use /models install to add one.")
            selection = _select_installed_model_option(
                self._registry,
                "Choose a local model",
                default=self._session_manager.current.model,
            )
            if selection is None:
                return CommandResult.ok("Model selection cancelled.")
            args = [selection.value]

        name, version = _parse_name_version(args[0])
        entry = self._registry.get_model(name, version)
        if entry is None:
            label = f"{name}@{version}" if version else name
            return CommandResult.error(
                f"Model '{label}' not found.\nUse /models list to see installed models."
            )

        return _activate_model_entry(
            entry,
            self._hw_detector,
            self._session_manager,
            self._console,
        )

    def help_text(self) -> str:
        return (
            "Set the active local model for this session.\n"
            "Usage: /models use <name>        Use latest version\n"
            "       /models use <name@v1>     Use specific version\n"
            "Prefer /set for interactive target selection."
        )


class ModelsInspectHandler(CommandHandler):
    """Show detailed information about an installed model."""

    def __init__(self, registry: ModelRegistry):
        self._registry = registry

    def execute(self, args: list[str]) -> CommandResult:
        if not args:
            if not supports_interactive_prompt():
                return CommandResult.error(
                    "Model name required.\nUsage: /models inspect <name[@version]>"
                )
            if not self._registry.list_models():
                return CommandResult.ok("No models installed. Use /models install to add one.")
            selection = _select_installed_model_option(
                self._registry,
                "Choose a model to inspect",
                default="",
            )
            if selection is None:
                return CommandResult.ok("Model inspection cancelled.")
            args = [selection.value]

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
    router.register(
        "models",
        ModelsParentHandler(
            installer,
            hardware_detector,
            session_manager,
            console,
        ),
    )
    router.register("models list", ModelsListHandler(registry))
    router.register("models search", ModelsSearchHandler(registry))
    router.register("models install", ModelsInstallHandler(installer))
    router.register("models remove", ModelsRemoveHandler(registry, models_dir))
    router.register(
        "models use",
        ModelsUseHandler(registry, hardware_detector, session_manager, console),
        visible_in_menu=False,
    )
    router.register("models inspect", ModelsInspectHandler(registry))


def _activate_model_entry(
    entry: ModelEntry,
    hardware_detector: HardwareDetector,
    session_manager: SessionManager,
    console: Console,
) -> CommandResult:
    """Apply hardware checks and make a model the active session model."""
    can_run, warnings = hardware_detector.can_run_model(entry.size_bytes)
    for warning in warnings:
        console.print(f"[yellow]⚠ {warning}[/yellow]")
    if not can_run:
        return CommandResult.error(
            "Model is too large for available hardware. "
            "Try a smaller quantization or use a remote provider."
        )

    session = session_manager.current
    session.model = f"{entry.name}@{entry.version}"
    session.provider = ""
    session.touch()

    return CommandResult.ok(
        f"Active model set to '{entry.name}' ({entry.version}, {entry.format})."
    )


def build_model_selection_options(registry: ModelRegistry) -> list[SelectionOption]:
    """Build interactive selection options for installed model entries."""
    options: list[SelectionOption] = []
    for entry in registry.list_models():
        label = f"{entry.name}@{entry.version}"
        repo = str(entry.metadata.get("repo", ""))
        options.append(
            SelectionOption(
                value=label,
                label=label,
                description=f"{entry.format} • {_fmt_size(entry.size_bytes)}",
                aliases=(entry.name, entry.version, repo),
            )
        )
    return options


def _select_installed_model_option(
    registry: ModelRegistry,
    message: str,
    *,
    default: str | None = None,
) -> SelectionOption | None:
    """Prompt for one installed model entry."""
    options = build_model_selection_options(registry)
    if not options:
        return None
    return select_option(message, options, default=default)


def _backend_options() -> list[SelectionOption]:
    """Return the available top-level backend choices for the picker."""
    backends = ["gguf", "safetensors"]
    if _mlx_supported_on_host():
        backends.insert(1, "mlx")

    options = [
        SelectionOption(
            value=backend,
            label=_BACKEND_LABELS[backend],
            description=_BACKEND_DESCRIPTIONS[backend],
            aliases=(backend, _BACKEND_LABELS[backend].lower()),
        )
        for backend in backends
    ]
    options.extend(_flow_navigation_options(include_back=False))
    return options


def _flow_navigation_options(*, include_back: bool) -> list[SelectionOption]:
    """Return reusable back/cancel options for multi-step selection flows."""
    options: list[SelectionOption] = []
    if include_back:
        options.append(
            SelectionOption(
                value=_BACK_SENTINEL,
                label="Back",
                description="Return to the previous selection step.",
                aliases=("previous", "back"),
            )
        )
    options.append(
        SelectionOption(
            value=_CANCEL_SENTINEL,
            label="Cancel",
            description="Exit the model picker without making changes.",
            aliases=("quit", "cancel"),
        )
    )
    return options


def _models_for_backend(backend: str) -> list[FeaturedModel]:
    """Return curated models for a specific backend, preserving the catalog order."""
    return [model for model in _FEATURED_HF_MODELS if model.backend == backend]


def _ordered_unique(values: Iterable[str]) -> list[str]:
    """Return the first occurrence of each value in order."""
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _resolve_flow_choice(choice: SelectionOption | None) -> str | None:
    """Normalize menu selections and treat cancel as a null result."""
    if choice is None or choice.value == _CANCEL_SENTINEL:
        return None
    return choice.value


def _mlx_supported_on_host() -> bool:
    """Whether MLX should be offered in the interactive model picker."""
    return platform.system() == "Darwin" and platform.machine().lower() in {"arm64", "aarch64"}
