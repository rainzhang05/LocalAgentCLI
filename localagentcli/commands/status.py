"""/status command handler."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from localagentcli.commands.router import CommandHandler, CommandResult, CommandRouter, CommandSpec
from localagentcli.config.manager import ConfigManager
from localagentcli.session.manager import SessionManager


@dataclass(frozen=True)
class StatusSnapshot:
    """Shared status data for the prompt toolbar and /status."""

    mode: str
    target: str
    workspace: str
    session_name: str
    approval_mode: str
    message_count: int
    agent_route: str = ""
    agent_phase: str = ""
    agent_step: str = ""
    agent_pending_tool: str = ""
    rollback_count: int = 0


def build_status_snapshot(
    *,
    mode: str,
    target: str,
    workspace: str,
    session_name: str,
    approval_mode: str,
    message_count: int,
    agent_route: str = "",
    agent_phase: str = "",
    agent_step: str = "",
    agent_pending_tool: str = "",
    rollback_count: int = 0,
) -> StatusSnapshot:
    """Create one reusable snapshot of the current CLI state."""
    return StatusSnapshot(
        mode=mode,
        target=target,
        workspace=workspace,
        session_name=session_name,
        approval_mode=approval_mode,
        message_count=message_count,
        agent_route=agent_route,
        agent_phase=agent_phase,
        agent_step=agent_step,
        agent_pending_tool=agent_pending_tool,
        rollback_count=rollback_count,
    )


def format_status_toolbar(snapshot: StatusSnapshot, *, hint: str = "Type /help") -> str:
    """Render the compact prompt-time status line."""
    sections = [
        " LocalAgent",
        f"mode: {snapshot.mode}",
        f"target: {snapshot.target}",
    ]
    agent_label = _agent_toolbar_label(snapshot)
    if agent_label:
        sections.append(f"agent: {agent_label}")
    if snapshot.rollback_count:
        sections.append(f"undo: {snapshot.rollback_count}")
    sections.extend([f"workspace: {snapshot.workspace}", hint])
    return " | ".join(sections) + " "


def format_status_report(snapshot: StatusSnapshot) -> str:
    """Render the expanded /status snapshot."""
    lines = [
        "Current status:",
        "",
        f"  Mode:          {snapshot.mode}",
        f"  Target:        {snapshot.target}",
        f"  Workspace:     {snapshot.workspace}",
        f"  Session:       {snapshot.session_name}",
        f"  Approval:      {snapshot.approval_mode}",
        f"  Messages:      {snapshot.message_count}",
    ]
    if snapshot.agent_route:
        lines.append(f"  Agent route:   {_humanize_route(snapshot.agent_route)}")
    if snapshot.agent_phase:
        lines.append(f"  Agent phase:   {_humanize_phase(snapshot.agent_phase)}")
    if snapshot.agent_step:
        lines.append(f"  Agent step:    {snapshot.agent_step}")
    if snapshot.agent_pending_tool:
        lines.append(f"  Pending tool:  {snapshot.agent_pending_tool}")
    if snapshot.rollback_count:
        lines.append(f"  Undo ready:    {snapshot.rollback_count} change(s)")
    return "\n".join(lines)


class StatusHandler(CommandHandler):
    """Display current session status."""

    def __init__(
        self,
        session_manager: SessionManager,
        config: ConfigManager,
        *,
        target_resolver: Callable[[], str] | None = None,
        workspace_formatter: Callable[[str], str] | None = None,
    ):
        self._session_manager = session_manager
        self._config = config
        self._target_resolver = target_resolver
        self._workspace_formatter = workspace_formatter or (lambda value: value)

    def execute(self, args: list[str]) -> CommandResult:
        snapshot = self._snapshot()
        return CommandResult.ok(format_status_report(snapshot))

    def describe(self) -> CommandSpec:
        return CommandSpec(
            group="System",
            summary="Show the current shell mode, target, workspace, and approval state.",
            usage="/status",
        )

    def _snapshot(self) -> StatusSnapshot:
        session = self._session_manager.current
        task_state = (
            session.metadata.get("agent_task_state", {})
            if isinstance(session.metadata.get("agent_task_state", {}), dict)
            else {}
        )
        approval_mode = str(
            session.metadata.get(
                "approval_mode",
                self._config.get("safety.approval_mode", "balanced"),
            )
        )
        target = (
            self._target_resolver()
            if self._target_resolver is not None
            else _default_target(
                session.provider,
                session.model,
            )
        )
        workspace = self._workspace_formatter(session.workspace)
        return build_status_snapshot(
            mode=session.mode,
            target=target,
            workspace=workspace,
            session_name=session.name or "(unsaved)",
            approval_mode=approval_mode,
            message_count=len(session.history),
            agent_route=str(task_state.get("route", "") or ""),
            agent_phase=str(task_state.get("phase", "") or ""),
            agent_step=_format_agent_step(task_state),
            agent_pending_tool=str(task_state.get("pending_tool", "") or ""),
            rollback_count=int(task_state.get("rollback_count", 0) or 0),
        )


def register(
    router: CommandRouter,
    session_manager: SessionManager,
    config: ConfigManager,
    *,
    target_resolver: Callable[[], str] | None = None,
    workspace_formatter: Callable[[str], str] | None = None,
) -> None:
    """Register the /status command."""
    router.register(
        "status",
        StatusHandler(
            session_manager,
            config,
            target_resolver=target_resolver,
            workspace_formatter=workspace_formatter,
        ),
    )


def _default_target(provider_name: str, model_name: str) -> str:
    """Render a stable target label when the shell cannot provide richer context."""
    if provider_name:
        selected_model = model_name or "remote"
        return f"{provider_name} ({selected_model})"
    if model_name:
        return model_name
    return "(none)"


def _format_agent_step(task_state: dict[str, object]) -> str:
    step_index = task_state.get("step_index")
    step_description = str(task_state.get("step_description", "") or "")
    if isinstance(step_index, int) and step_description:
        return f"{step_index}. {step_description}"
    return step_description


def _humanize_route(route: str) -> str:
    mapping = {
        "direct_answer": "direct answer",
        "single_step_task": "single-step task",
        "multi_step_task": "multi-step task",
    }
    return mapping.get(route, route.replace("_", " "))


def _humanize_phase(phase: str) -> str:
    return phase.replace("_", " ")


def _agent_toolbar_label(snapshot: StatusSnapshot) -> str:
    if not snapshot.agent_route and not snapshot.agent_phase:
        return ""

    parts: list[str] = []
    if snapshot.agent_route:
        parts.append(_humanize_route(snapshot.agent_route))
    if snapshot.agent_phase:
        parts.append(_humanize_phase(snapshot.agent_phase))
    if snapshot.agent_phase == "waiting_approval" and snapshot.agent_pending_tool:
        parts.append(snapshot.agent_pending_tool)
    elif snapshot.agent_step:
        parts.append(_compact(snapshot.agent_step, 28))
    return "/".join(parts)


def _compact(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."
