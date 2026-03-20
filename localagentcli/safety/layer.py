"""Central approval, boundary, and rollback gate for tool execution."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from localagentcli.safety.approval import ApprovalManager, RiskLevel
from localagentcli.safety.boundary import WorkspaceBoundary, WorkspaceBoundaryError
from localagentcli.safety.rollback import RollbackManager
from localagentcli.tools.base import Tool, ToolResult


@dataclass
class ApprovalResult:
    """Structured decision returned by the safety layer."""

    status: str
    risk_level: RiskLevel
    reason: str | None = None
    warnings: list[str] = field(default_factory=list)
    risk_reason: str | None = None
    rollback_summary: str | None = None

    @property
    def approved(self) -> bool:
        return self.status == "approved"

    @property
    def requires_approval(self) -> bool:
        return self.status == "needs_approval"

    @property
    def blocked(self) -> bool:
        return self.status == "blocked"


@dataclass
class _PendingRollback:
    """Temporary state captured before a modifying tool runs."""

    file_path: Path
    existed_before: bool
    backup_path: Path | None


class SafetyLayer:
    """Apply central safety policy before and after every tool execution."""

    HIGH_RISK_COMMANDS = (
        re.compile(r"\brm\b", re.IGNORECASE),
        re.compile(r"\brmdir\b", re.IGNORECASE),
        re.compile(r"\bsudo\b", re.IGNORECASE),
        re.compile(r"\bsystemctl\b", re.IGNORECASE),
        re.compile(r"\bcurl\b", re.IGNORECASE),
        re.compile(r"\bwget\b", re.IGNORECASE),
        re.compile(r"\bpip\s+install\b", re.IGNORECASE),
        re.compile(r"\bnpm\s+install\b", re.IGNORECASE),
        re.compile(r"\bgit\s+push\s+--force\b", re.IGNORECASE),
        re.compile(r"\bgit\s+reset\s+--hard\b", re.IGNORECASE),
    )
    HIGH_RISK_FILE_PATTERNS = (
        re.compile(r"(^|/)\.env$", re.IGNORECASE),
        re.compile(r"\.pem$", re.IGNORECASE),
        re.compile(r"\.key$", re.IGNORECASE),
        re.compile(r"credentials", re.IGNORECASE),
        re.compile(r"secrets?\.(json|yaml|yml|toml)$", re.IGNORECASE),
    )

    def __init__(
        self,
        approval_manager: ApprovalManager,
        boundary: WorkspaceBoundary,
        rollback: RollbackManager,
    ):
        self._approval = approval_manager
        self._boundary = boundary
        self._rollback = rollback
        self._pending_changes: dict[str, list[_PendingRollback]] = {}

    @property
    def rollback(self) -> RollbackManager:
        """Expose the rollback manager for future command surfaces."""
        return self._rollback

    def check_and_approve(self, tool: Tool, args: dict) -> ApprovalResult:
        """Run boundary validation, risk classification, and approval checks."""
        try:
            warnings = self._validate_arguments(tool, args)
        except WorkspaceBoundaryError as exc:
            return ApprovalResult(
                status="blocked",
                risk_level=RiskLevel.HIGH,
                reason=str(exc),
                risk_reason="The action escapes the active workspace boundary.",
            )

        risk_level, risk_reason = self.describe_risk(tool.name, args)
        rollback_summary = self.describe_rollback(tool, args)
        if self._approval.needs_approval(tool, risk_level):
            return ApprovalResult(
                status="needs_approval",
                risk_level=risk_level,
                warnings=warnings,
                risk_reason=risk_reason,
                rollback_summary=rollback_summary,
            )
        return ApprovalResult(
            status="approved",
            risk_level=risk_level,
            warnings=warnings,
            risk_reason=risk_reason,
            rollback_summary=rollback_summary,
        )

    def pre_action(self, tool: Tool, args: dict) -> None:
        """Create backups before a modifying tool executes."""
        pending: list[_PendingRollback] = []
        for file_path in self._modification_targets(tool, args):
            resolved = self._boundary.validate_path(file_path)
            existed_before = resolved.exists()
            backup_path = self._rollback.backup_file(resolved) if existed_before else None
            pending.append(
                _PendingRollback(
                    file_path=resolved,
                    existed_before=existed_before,
                    backup_path=backup_path,
                )
            )

        if pending:
            self._pending_changes[self._action_key(tool, args)] = pending

    def post_action(self, tool: Tool, args: dict, result: ToolResult) -> None:
        """Record rollback history for successful file modifications."""
        pending = self._pending_changes.pop(self._action_key(tool, args), [])
        if result.status != "success":
            return

        for change in pending:
            if change.existed_before and change.backup_path is not None:
                self._rollback.record_modification(
                    change.file_path,
                    change.backup_path,
                    tool.name,
                    result.summary,
                )
            elif change.file_path.exists():
                self._rollback.record_creation(change.file_path, tool.name, result.summary)

    def classify_risk(self, tool_name: str, args: dict) -> RiskLevel:
        """Classify a tool call as normal or high risk."""
        return self.describe_risk(tool_name, args)[0]

    def describe_risk(self, tool_name: str, args: dict) -> tuple[RiskLevel, str | None]:
        """Classify a tool call and explain why it was considered risky."""
        if tool_name == "shell_execute":
            command = str(args.get("command", ""))
            for pattern in self.HIGH_RISK_COMMANDS:
                if pattern.search(command):
                    return (
                        RiskLevel.HIGH,
                        f"Command matches a high-risk pattern: {pattern.pattern}",
                    )

        for file_path in self._risk_sensitive_paths(tool_name, args):
            normalized = self._normalize_display_path(file_path)
            for pattern in self.HIGH_RISK_FILE_PATTERNS:
                if pattern.search(normalized):
                    return (
                        RiskLevel.HIGH,
                        f"Path appears sensitive: {normalized}",
                    )

        return RiskLevel.NORMAL, None

    def describe_rollback(self, tool: Tool, args: dict) -> str | None:
        """Explain whether rollback will be available after a successful action."""
        targets = self._modification_targets(tool, args)
        if not targets:
            return "Rollback is not available for this action."

        summaries: list[str] = []
        for raw_path in targets:
            resolved = self._boundary.validate_path(raw_path)
            display_path = self._normalize_display_path(raw_path)
            if resolved.exists():
                summaries.append(f"Rollback available: {display_path} will be backed up.")
            else:
                summaries.append(f"Rollback available: {display_path} can be removed with undo.")
        return "\n".join(summaries)

    def _validate_arguments(self, tool: Tool, args: dict) -> list[str]:
        warnings: list[str] = []
        for path_arg in self._path_argument_values(tool.name, args):
            self._boundary.validate_path(path_arg)

        if tool.name == "shell_execute":
            working_dir = str(args.get("working_dir", "."))
            self._boundary.validate_path(working_dir)
            warnings.extend(self._boundary.inspect_command(str(args.get("command", ""))))

        return warnings

    def _path_argument_values(self, tool_name: str, args: dict) -> list[str]:
        values: list[str] = []
        single_path_tools = {
            "directory_list",
            "file_read",
            "file_search",
            "file_write",
            "git_diff",
            "patch_apply",
            "test_execute",
        }
        if tool_name in single_path_tools:
            value = args.get("path")
            if isinstance(value, str) and value:
                values.append(value)
        if tool_name == "git_commit":
            files = args.get("files", [])
            if isinstance(files, list):
                values.extend(str(path) for path in files if isinstance(path, str) and path)
        return values

    def _risk_sensitive_paths(self, tool_name: str, args: dict) -> list[str]:
        if tool_name == "shell_execute":
            return []
        return self._path_argument_values(tool_name, args)

    def _modification_targets(self, tool: Tool, args: dict) -> list[str]:
        if tool.name in {"file_write", "patch_apply"}:
            path = args.get("path")
            if isinstance(path, str) and path:
                return [path]
        return []

    def _normalize_display_path(self, file_path: str) -> str:
        path = Path(file_path).expanduser()
        if not path.is_absolute():
            path = self._boundary.root / path
        try:
            relative = path.resolve(strict=False).relative_to(self._boundary.root)
            return relative.as_posix()
        except ValueError:
            return path.as_posix()

    def _action_key(self, tool: Tool, args: dict) -> str:
        return f"{tool.name}:{json.dumps(args, sort_keys=True, ensure_ascii=False)}"
