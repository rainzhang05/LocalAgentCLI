"""Shared runtime services and execution helpers."""

from __future__ import annotations

import json
import sys
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from rich.console import Console

from localagentcli.agents.chat import ChatController
from localagentcli.agents.controller import AgentController
from localagentcli.agents.events import AgentEvent
from localagentcli.config.manager import ConfigManager
from localagentcli.features import FeatureRegistry
from localagentcli.mcp import McpManager
from localagentcli.models.abstraction import ModelAbstractionLayer
from localagentcli.models.backends.base import (
    ModelBackend,
    StreamChunk,
    backend_label,
    backend_requirement_names,
    check_backend_dependencies,
    install_backend_dependencies,
)
from localagentcli.models.detector import HardwareDetector, ModelDetector
from localagentcli.models.installer import ModelInstaller
from localagentcli.models.provider_readiness import aresolve_remote_model_readiness
from localagentcli.models.readiness import (
    build_target_readiness,
    default_local_capability_provenance,
    format_capability_brief,
    format_readiness_tradeoff,
    is_agent_ready,
    readiness_posture_label,
    selection_state_label,
)
from localagentcli.models.registry import ModelEntry, ModelRegistry
from localagentcli.providers.base import RemoteProvider, effective_model_request_timeout
from localagentcli.providers.keys import KeyManager
from localagentcli.providers.registry import ProviderRegistry
from localagentcli.safety.approval import ApprovalManager
from localagentcli.safety.boundary import WorkspaceBoundary
from localagentcli.safety.layer import SafetyLayer
from localagentcli.safety.policy import RuntimeSandboxPolicy
from localagentcli.safety.posture import parse_sandbox_mode
from localagentcli.safety.rollback import RollbackManager
from localagentcli.session.instructions import sync_workspace_instruction
from localagentcli.session.manager import SessionManager
from localagentcli.skills import SkillsManager
from localagentcli.storage.logger import Logger
from localagentcli.storage.manager import StorageManager
from localagentcli.tools import (
    LocalExecProcess,
    build_shell_exec_process,
    create_default_tool_registry,
)
from localagentcli.tools.router import DynamicToolSpec, ToolRouter

RuntimeMessageKind = Literal["info", "status", "warning", "error", "success"]


@dataclass(frozen=True)
class RuntimeMessage:
    """A user-visible runtime message that callers can render."""

    kind: RuntimeMessageKind
    text: str


@dataclass
class RuntimeTurn:
    """Prepared execution path for one plain-text request."""

    mode: Literal["chat", "agent"]
    stream: AsyncIterator[StreamChunk] | None = None
    events: AsyncIterator[AgentEvent] | None = None
    controller: AgentController | None = None
    route: str | None = None
    compaction_count: int = 0


@dataclass
class RuntimeServices:
    """Long-lived shared services used by shell and non-shell surfaces."""

    config: ConfigManager
    storage: StorageManager
    logger: Logger
    key_manager: KeyManager
    provider_registry: ProviderRegistry
    model_registry: ModelRegistry
    model_detector: ModelDetector
    hardware_detector: HardwareDetector
    model_installer: ModelInstaller
    session_manager: SessionManager
    skills_manager: SkillsManager
    dynamic_tool_specs: list[DynamicToolSpec]
    mcp_manager: McpManager | None
    feature_registry: FeatureRegistry

    @classmethod
    def create(
        cls,
        config: ConfigManager,
        storage: StorageManager,
        console: Console,
    ) -> RuntimeServices:
        """Create the shared runtime services for the current process."""
        logger = Logger(
            storage.logs_dir,
            config.get("general.logging_level", "normal"),
        )
        key_manager = KeyManager(storage.secrets_dir)
        from localagentcli.commands import hf_token as hf_token_cmd

        hf_token_cmd.restore_hf_token_environment(key_manager)

        provider_registry = ProviderRegistry(config, key_manager)
        model_registry = ModelRegistry(storage.registry_path)
        model_detector = ModelDetector()
        hardware_detector = HardwareDetector()
        model_installer = ModelInstaller(
            models_dir=storage.models_dir,
            cache_dir=storage.cache_dir,
            registry=model_registry,
            detector=model_detector,
            console=console,
        )

        def default_target_resolver(provider_name: str, model_name: str) -> tuple[str, str]:
            return _resolve_default_target(
                provider_name,
                model_name,
                provider_registry,
                model_registry,
                model_detector,
            )

        session_manager = SessionManager(
            storage.sessions_dir,
            config,
            default_target_resolver=default_target_resolver,
        )
        session_manager.new_session()
        skills_manager = SkillsManager(storage.skills_dir)

        return cls(
            config=config,
            storage=storage,
            logger=logger,
            key_manager=key_manager,
            provider_registry=provider_registry,
            model_registry=model_registry,
            model_detector=model_detector,
            hardware_detector=hardware_detector,
            model_installer=model_installer,
            session_manager=session_manager,
            skills_manager=skills_manager,
            dynamic_tool_specs=[],
            mcp_manager=McpManager.from_config(
                config.get("mcp_servers", {}),
                bearer_token_resolver=lambda server_name: key_manager.retrieve_key(
                    f"mcp_server:{server_name}"
                ),
            ),
            feature_registry=FeatureRegistry(config.get("features", {})),
        )

    def parse_name_version(self, model_name: str) -> tuple[str, str | None]:
        """Parse a stored model reference in name@version form."""
        if "@" in model_name:
            name, version = model_name.rsplit("@", 1)
            return name, version
        return model_name, None

    def refresh_model_entry(self, name: str, version: str | None) -> ModelEntry | None:
        """Re-detect a local model on disk and repair stale registry metadata."""
        entry = self.model_registry.get_model(name, version)
        if entry is None:
            return None

        model_path = Path(entry.path)
        if not model_path.exists():
            return entry

        try:
            detection = self.model_detector.detect(
                model_path,
                allow_unsupported_backend=True,
            )
        except Exception:
            return entry

        updates: dict[str, object] = {}
        if detection.format != entry.format:
            updates["format"] = detection.format

        merged_metadata = dict(entry.metadata)
        if merged_metadata.get("backend") != detection.backend:
            merged_metadata["backend"] = detection.backend
        for key, value in detection.metadata.items():
            if merged_metadata.get(key) != value:
                merged_metadata[key] = value
        if merged_metadata != entry.metadata:
            updates["metadata"] = merged_metadata

        if updates:
            try:
                self.model_registry.update_version(entry.name, entry.version, updates)
                entry = self.model_registry.get_model(name, version) or entry
            except KeyError:
                return entry
        return entry

    def workspace_root(self) -> Path:
        """Resolve the current session workspace to an absolute path."""
        return Path(self.session_manager.current.workspace).expanduser().resolve()

    def active_target_label(self) -> str:
        """Describe the active local model or remote provider for status surfaces."""
        session = self.session_manager.current
        if session.provider:
            model_name = session.model or "remote"
            return f"{session.provider} ({model_name})"
        if session.model:
            name, version = self.parse_name_version(session.model)
            entry = self.model_registry.get_model(name, version)
            if entry is not None:
                return f"{session.model} ({entry.format})"
            return session.model
        return "(none)"

    def register_dynamic_tool(self, spec: DynamicToolSpec) -> None:
        """Register one runtime-visible dynamic tool specification."""
        self.dynamic_tool_specs.append(spec)

    def build_tool_router(self, workspace_root: Path) -> ToolRouter:
        """Build the runtime tool router for the current turn."""
        dynamic_tools = list(self.dynamic_tool_specs)
        if self.mcp_manager is not None:
            dynamic_tools.extend(self.mcp_manager.build_dynamic_tool_specs())

        sandbox_mode_value = str(
            self.session_manager.get_effective_config("safety.sandbox_mode") or "workspace-write"
        )
        sandbox_policy = RuntimeSandboxPolicy.from_posture(
            parse_sandbox_mode(sandbox_mode_value),
            workspace_root,
        )
        backend_value = str(
            self.session_manager.get_effective_config("safety.os_sandbox_backend") or "off"
        )
        try:
            shell_exec_process = build_shell_exec_process(
                policy=sandbox_policy,
                backend=backend_value,
            )
        except Exception as exc:
            self.logger.normal(
                "Failed to configure OS sandbox backend '%s'; falling back to local exec: %s",
                backend_value,
                exc,
            )
            shell_exec_process = LocalExecProcess()

        return ToolRouter(
            workspace_root=workspace_root,
            builtins=create_default_tool_registry(
                workspace_root,
                shell_exec_process=shell_exec_process,
            ),
            dynamic_tools=dynamic_tools,
        )


class SessionExecutionRuntime:
    """Reusable runtime boundary for chat and agent execution."""

    def __init__(
        self,
        services: RuntimeServices,
        emit: Callable[[RuntimeMessage], None],
        confirm_backend_install: Callable[[str, str, str], bool | None],
    ) -> None:
        self._services = services
        self._emit = emit
        self._confirm_backend_install = confirm_backend_install
        self._active_provider: RemoteProvider | None = None
        self._active_provider_name = ""
        self._active_provider_binding: str = ""
        self._active_backend: ModelBackend | None = None
        self._active_backend_model = ""
        self._agent_controller: AgentController | None = None
        self._agent_controller_key: tuple[object, ...] | None = None

    @property
    def agent_controller(self) -> AgentController | None:
        """The active agent controller, if any."""
        return self._agent_controller

    def sync_workspace_instruction(self) -> None:
        """Refresh repository instructions for the active session."""
        try:
            sync_workspace_instruction(
                self._services.session_manager.current,
                skills_manager=self._services.skills_manager,
            )
        except Exception:
            return

    def build_generation_options(self) -> dict[str, object]:
        """Build generation options from the effective configuration."""
        options: dict[str, object] = {
            "temperature": self._services.session_manager.get_effective_config(
                "generation.temperature"
            )
            or 0.7,
            "max_tokens": self._services.session_manager.get_effective_config(
                "generation.max_tokens"
            )
            or 4096,
            "top_p": self._services.session_manager.get_effective_config("generation.top_p") or 1.0,
        }
        reasoning_effort = self._services.session_manager.get_effective_config(
            "generation.reasoning_effort"
        )
        if isinstance(reasoning_effort, str):
            normalized = reasoning_effort.strip().lower()
            if normalized in {"low", "medium", "high"}:
                options["reasoning_effort"] = normalized
        session = self._services.session_manager.current
        if session.provider and session.model:
            options["model"] = session.model
        global_rt = self._services.session_manager.get_effective_config("timeouts.model_response")
        entry = self._services.provider_registry.get(session.provider) if session.provider else None
        prov_opts = entry.options if entry is not None else {}
        options["request_timeout"] = effective_model_request_timeout(prov_opts, global_rt)
        return options

    def context_limit(self) -> int:
        """Return the best-known context limit for the active target."""
        session = self._services.session_manager.current
        if session.model and not session.provider:
            name, version = self._services.parse_name_version(session.model)
            entry = self._services.refresh_model_entry(name, version)
            if entry is not None:
                for key in ("context_length", "context_window", "n_ctx"):
                    value = entry.metadata.get(key)
                    if isinstance(value, int) and value > 0:
                        return value
        return 8192

    def active_target_label(self) -> str:
        """Return the current target label for UI surfaces."""
        return self._services.active_target_label()

    def workspace_root(self) -> Path:
        """Return the current workspace root."""
        return self._services.workspace_root()

    async def arun_chat_turn(self, text: str) -> RuntimeTurn | None:
        """Run one chat-mode turn through the shared runtime boundary (async)."""
        model = self.resolve_active_model()
        if model is None:
            return None

        chat_controller = ChatController(
            model=model,
            session=self._services.session_manager.current,
            context_limit=self.context_limit(),
            generation_config=self.build_generation_options(),
            on_session_mutated=self._services.session_manager.schedule_named_autosave,
        )
        gen = chat_controller.ahandle_input(
            text,
            generation_options=self.build_generation_options(),
        )
        return RuntimeTurn(
            mode="chat",
            stream=gen,
            compaction_count=chat_controller.last_compaction_count,
        )

    async def adispatch_agent_turn(self, text: str) -> RuntimeTurn | None:
        """Dispatch one text input through the agent runtime boundary (async)."""
        model = self.resolve_active_model()
        if model is None:
            return None

        if not await self._async_ensure_agent_dispatch_allowed():
            return None

        agent_controller = self.get_or_create_agent_controller(model)
        self._refresh_agent_tool_registry_if_enabled(agent_controller)
        if agent_controller.has_active_task:
            self._emit_message(
                "error",
                "An agent task is already running. Press Ctrl+C to stop it before "
                "starting a new one.",
            )
            return None

        dispatch = await agent_controller.adispatch_input(text)
        return RuntimeTurn(
            mode="agent",
            stream=cast(AsyncIterator[StreamChunk] | None, dispatch.stream),
            events=cast(AsyncIterator[AgentEvent] | None, dispatch.events),
            controller=agent_controller,
            route=dispatch.triage.outcome,
            compaction_count=agent_controller.last_compaction_count,
        )

    async def _async_ensure_agent_dispatch_allowed(self) -> bool:
        """Mirror /mode agent readiness checks at dispatch time."""
        session = self._services.session_manager.current
        if session.provider:
            prov = self._active_provider
            if prov is None or not session.model:
                return True
            readiness = await aresolve_remote_model_readiness(prov, session.model)
            if readiness.selection_state in {"legacy_fallback", "unknown"}:
                self._emit_message(
                    "error",
                    "Cannot run agent mode: active provider model is "
                    f"{selection_state_label(readiness.selection_state)}. "
                    f"Readiness posture: {readiness_posture_label(readiness)}. "
                    f"Tradeoff: {format_readiness_tradeoff(readiness)}. "
                    f"{readiness.guidance}",
                )
                return False
            tool_use = readiness.capabilities["tool_use"]
            if not is_agent_ready(readiness):
                self._emit_message(
                    "error",
                    "Cannot run agent mode: the active provider model reports "
                    f"{format_capability_brief('tool use', tool_use)} — {tool_use.reason}. "
                    f"Readiness posture: {readiness_posture_label(readiness)}. "
                    f"Tradeoff: {format_readiness_tradeoff(readiness)}. "
                    f"{readiness.agent_recommendation}",
                )
                return False
            return True

        if session.model:
            name, version = self._services.parse_name_version(session.model)
            entry = self._services.model_registry.get_model(name, version)
            if entry is None:
                return True
            readiness = build_target_readiness(
                kind="local",
                selection_state="local",
                capabilities=entry.capabilities,
                capability_provenance=entry.capability_provenance,
                default_builder=default_local_capability_provenance,
            )
            tool_use = readiness.capabilities["tool_use"]
            if not tool_use.supported:
                self._emit_message(
                    "error",
                    "Cannot run agent mode: the active model reports "
                    f"{format_capability_brief('tool use', tool_use)} — {tool_use.reason}. "
                    f"Readiness posture: {readiness_posture_label(readiness)}. "
                    f"Tradeoff: {format_readiness_tradeoff(readiness)}. "
                    f"{readiness.agent_recommendation}",
                )
                return False
        return True

    def resolve_active_model(self) -> ModelAbstractionLayer | None:
        """Resolve the active local backend or remote provider into a model abstraction."""
        session = self._services.session_manager.current
        backend: ModelBackend | None = None

        if session.model and not session.provider:
            backend = self._get_active_backend(session.model)
            if backend is None:
                self._emit_message(
                    "error",
                    f"Failed to load model '{session.model}'. Check /models inspect for details.",
                )
                return None
        elif session.provider:
            if not session.model:
                self._emit_message(
                    "info",
                    "No provider model selected. Use /set or /set default to choose one.",
                )
                return None
            backend = self._get_active_provider(session.provider)
            if backend is None:
                self._emit_message(
                    "error",
                    f"Failed to connect to provider '{session.provider}'. Check /providers test.",
                )
                return None

        if backend is None:
            self._emit_message(
                "info",
                "No model connected. Use /setup, /set, or configure a model/provider to "
                "start chatting.",
            )
            return None
        return ModelAbstractionLayer(backend)

    def get_or_create_agent_controller(self, model: ModelAbstractionLayer) -> AgentController:
        """Reuse the current agent controller when the target/session is unchanged."""
        session = self._services.session_manager.current
        key = (
            session.id,
            session.mode,
            session.workspace,
            session.provider,
            session.model,
            self._services.session_manager.get_effective_config("safety.approval_mode")
            or "balanced",
            id(model.backend),
        )
        if self._agent_controller is not None and self._agent_controller_key == key:
            return self._agent_controller
        self._agent_controller = self.create_agent_controller(model)
        self._agent_controller_key = key
        return self._agent_controller

    def _refresh_agent_tool_registry_if_enabled(self, controller: AgentController) -> None:
        """Refresh tool inventory between turns when enabled by feature flag."""
        enabled = bool(self._services.feature_registry.is_enabled("mcp_tool_inventory_refresh"))
        if not enabled:
            return
        if controller.has_active_task:
            return
        controller.set_tool_registry(self._services.build_tool_router(self.workspace_root()))

    def create_agent_controller(self, model: ModelAbstractionLayer) -> AgentController:
        """Build or replace the active agent controller for the current session."""
        approval = ApprovalManager(
            self._services.session_manager.get_effective_config("safety.approval_mode")
            or "balanced"
        )
        self._agent_controller = AgentController(
            model=model,
            session=self._services.session_manager.current,
            tool_registry=self._services.build_tool_router(self.workspace_root()),
            approval=approval,
            safety=SafetyLayer(
                approval,
                WorkspaceBoundary(self.workspace_root()),
                RollbackManager(
                    self._services.session_manager.current.id,
                    self._services.storage.cache_dir,
                ),
                sandbox_mode=self._services.session_manager.get_effective_config(
                    "safety.sandbox_mode"
                )
                or "workspace-write",
            ),
            rollback_storage=self._services.storage.cache_dir,
            context_limit=self.context_limit(),
            generation_config=self.build_generation_options(),
            inactivity_timeout=self._services.session_manager.get_effective_config(
                "timeouts.inactivity"
            )
            or 600,
            on_session_mutated=self._services.session_manager.schedule_named_autosave,
        )
        return self._agent_controller

    def close(self) -> None:
        """Release any cached runtime resources."""
        if self._active_backend is not None:
            try:
                self._active_backend.unload()
            except Exception:
                pass
            self._active_backend = None
            self._active_backend_model = ""

        if self._active_provider is not None:
            try:
                self._active_provider.close()
            except Exception:
                pass
            self._active_provider = None
            self._active_provider_name = ""
            self._active_provider_binding = ""

        if self._agent_controller is not None:
            self._agent_controller.stop()
            self._agent_controller = None
            self._agent_controller_key = None
        if self._services.mcp_manager is not None:
            self._services.mcp_manager.close()

    async def aclose(self) -> None:
        """Async close (closes remote AsyncClient when a loop is running)."""
        if self._active_backend is not None:
            try:
                self._active_backend.unload()
            except Exception:
                pass
            self._active_backend = None
            self._active_backend_model = ""

        if self._active_provider is not None:
            try:
                await self._active_provider.aclose()
            except Exception:
                pass
            self._active_provider = None
            self._active_provider_name = ""
            self._active_provider_binding = ""

        if self._agent_controller is not None:
            self._agent_controller.stop()
            self._agent_controller = None
            self._agent_controller_key = None
        if self._services.mcp_manager is not None:
            self._services.mcp_manager.close()

    def _provider_cache_binding(self, provider_name: str) -> str | None:
        """Fingerprint provider config + selected model for cache invalidation."""
        entry = self._services.provider_registry.get(provider_name)
        if entry is None:
            return None
        opts = json.dumps(entry.options or {}, sort_keys=True, default=str)
        model = (self._services.session_manager.current.model or "").strip()
        return (
            f"{provider_name}\0{entry.type}\0{entry.base_url}\0"
            f"{entry.default_model}\0{opts}\0{model}"
        )

    def _get_active_provider(self, provider_name: str) -> RemoteProvider | None:
        """Get the active provider, caching the instance."""
        want = self._provider_cache_binding(provider_name)
        if (
            self._active_provider is not None
            and self._active_provider_name == provider_name
            and want is not None
            and self._active_provider_binding == want
        ):
            return self._active_provider
        try:
            if self._active_provider is not None:
                self._active_provider.close()
            self._active_provider = self._services.provider_registry.create_provider(provider_name)
            self._active_provider_name = provider_name
            self._active_provider_binding = want or ""
            self._active_provider.set_active_model(
                self._services.session_manager.current.model or None
            )
            return self._active_provider
        except Exception:
            if self._active_provider is not None:
                self._active_provider.close()
            self._active_provider = None
            self._active_provider_name = ""
            self._active_provider_binding = ""
            return None

    def _get_active_backend(self, model_name: str) -> ModelBackend | None:
        """Get the active local model backend, loading it if needed."""
        if self._active_backend and self._active_backend_model == model_name:
            return self._active_backend

        if self._active_backend is not None:
            try:
                self._active_backend.unload()
            except Exception:
                pass
            self._active_backend = None
            self._active_backend_model = ""

        name, version = self._services.parse_name_version(model_name)
        entry = self._services.refresh_model_entry(name, version)
        if entry is None:
            return None

        try:
            if not self._ensure_backend_dependencies(entry.format):
                return None
            backend = self._create_backend(entry.format)
            backend.load(Path(entry.path))
            self._active_backend = backend
            self._active_backend_model = model_name
            return backend
        except Exception as exc:
            self._services.logger.error("Failed to load model '%s': %s", model_name, exc)
            self._active_backend = None
            self._active_backend_model = ""
            return None

    def _ensure_backend_dependencies(self, backend_name: str) -> bool:
        """Prompt to install missing optional backend dependencies when needed."""
        if backend_name == "mlx" and sys.platform != "darwin":
            return True

        installed, _missing = check_backend_dependencies(backend_name)
        if installed:
            return True

        label = backend_label(backend_name)
        dependency_list = ", ".join(backend_requirement_names(backend_name))
        should_install = self._confirm_backend_install(backend_name, label, dependency_list)
        if should_install is None:
            self._emit_message("warning", f"{label} backend loading cancelled.")
            return False

        if not should_install:
            self._emit_message("warning", f"{label} backend dependencies were not installed.")
            return False

        self._emit_message("status", f"Installing {label} backend dependencies...")
        success, message = install_backend_dependencies(backend_name)
        if not success:
            self._emit_message(
                "error",
                f"Failed to install {label} backend dependencies: {message}",
            )
            return False

        self._emit_message("success", f"{label} backend dependencies installed.")
        return True

    def _create_backend(self, fmt: str) -> ModelBackend:
        """Create the appropriate backend instance for a model format."""
        if fmt == "mlx":
            from localagentcli.models.backends.mlx import MLXBackend

            return MLXBackend()
        if fmt == "gguf":
            from localagentcli.models.backends.gguf import GGUFBackend

            return GGUFBackend()
        if fmt == "safetensors":
            from localagentcli.models.backends.safetensors import SafetensorsBackend

            return SafetensorsBackend()
        raise ValueError(f"Unknown model format: '{fmt}'")

    def _emit_message(self, kind: RuntimeMessageKind, text: str) -> None:
        self._emit(RuntimeMessage(kind=kind, text=text))


def _resolve_default_target(
    provider_name: str,
    model_name: str,
    provider_registry: ProviderRegistry,
    model_registry: ModelRegistry,
    model_detector: ModelDetector,
) -> tuple[str, str]:
    """Validate configured defaults and choose a fallback target when needed."""
    if provider_name:
        if provider_registry.get(provider_name) is not None and model_name:
            return provider_name, model_name
        return _fallback_target(provider_registry, model_registry, model_detector)

    if model_name:
        name, version = _parse_name_version(model_name)
        if _refresh_model_entry(model_registry, model_detector, name, version) is not None:
            return "", model_name
        return _fallback_target(provider_registry, model_registry, model_detector)

    return "", ""


def _fallback_target(
    provider_registry: ProviderRegistry,
    model_registry: ModelRegistry,
    model_detector: ModelDetector,
) -> tuple[str, str]:
    """Choose a best-effort replacement target when the configured default is invalid."""
    installed_models = model_registry.list_models()
    if installed_models:
        entry = installed_models[0]
        repaired = _refresh_model_entry(model_registry, model_detector, entry.name, entry.version)
        if repaired is not None:
            return "", f"{repaired.name}@{repaired.version}"
        return "", f"{entry.name}@{entry.version}"

    for provider_entry in provider_registry.list_providers():
        runtime: RemoteProvider | None = None
        try:
            runtime = provider_registry.create_provider(provider_entry.name)
            models = runtime.list_models()
        except Exception:
            models = []
        finally:
            if runtime is not None:
                try:
                    runtime.close()
                except Exception:
                    pass
        if models:
            selected = models[0].id or models[0].name
            if selected:
                return provider_entry.name, selected

    return "", ""


def _parse_name_version(model_name: str) -> tuple[str, str | None]:
    """Parse a stored model reference in name@version form."""
    if "@" in model_name:
        name, version = model_name.rsplit("@", 1)
        return name, version
    return model_name, None


def _refresh_model_entry(
    model_registry: ModelRegistry,
    model_detector: ModelDetector,
    name: str,
    version: str | None,
) -> ModelEntry | None:
    """Re-detect a local model on disk and repair stale registry metadata."""
    entry = model_registry.get_model(name, version)
    if entry is None:
        return None

    model_path = Path(entry.path)
    if not model_path.exists():
        return entry

    try:
        detection = model_detector.detect(
            model_path,
            allow_unsupported_backend=True,
        )
    except Exception:
        return entry

    updates: dict[str, object] = {}
    if detection.format != entry.format:
        updates["format"] = detection.format

    merged_metadata = dict(entry.metadata)
    if merged_metadata.get("backend") != detection.backend:
        merged_metadata["backend"] = detection.backend
    for key, value in detection.metadata.items():
        if merged_metadata.get(key) != value:
            merged_metadata[key] = value
    if merged_metadata != entry.metadata:
        updates["metadata"] = merged_metadata

    if updates:
        try:
            model_registry.update_version(entry.name, entry.version, updates)
            entry = model_registry.get_model(name, version) or entry
        except KeyError:
            return entry
    return entry
