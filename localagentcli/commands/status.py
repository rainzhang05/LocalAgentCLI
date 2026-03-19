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


def build_status_snapshot(
    *,
    mode: str,
    target: str,
    workspace: str,
    session_name: str,
    approval_mode: str,
    message_count: int,
) -> StatusSnapshot:
    """Create one reusable snapshot of the current CLI state."""
    return StatusSnapshot(
        mode=mode,
        target=target,
        workspace=workspace,
        session_name=session_name,
        approval_mode=approval_mode,
        message_count=message_count,
    )


def format_status_toolbar(snapshot: StatusSnapshot, *, hint: str = "Type /help") -> str:
    """Render the compact prompt-time status line."""
    return (
        f" LocalAgent | mode: {snapshot.mode} | target: {snapshot.target} | "
        f"workspace: {snapshot.workspace} | {hint} "
    )


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
