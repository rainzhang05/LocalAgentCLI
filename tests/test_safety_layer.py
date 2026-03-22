"""Tests for central safety classification and rollback hooks."""

from __future__ import annotations

from pathlib import Path

import pytest

from localagentcli.safety.approval import ApprovalManager, RiskLevel
from localagentcli.safety.boundary import WorkspaceBoundary
from localagentcli.safety.layer import SafetyLayer
from localagentcli.safety.posture import SandboxPosture, parse_sandbox_mode
from localagentcli.safety.rollback import RollbackManager
from localagentcli.tools.file_read import FileReadTool
from localagentcli.tools.file_write import FileWriteTool
from localagentcli.tools.shell_execute import ShellExecuteTool


def _make_safety(tmp_path: Path, mode: str = "balanced") -> SafetyLayer:
    approval = ApprovalManager(mode=mode)
    return SafetyLayer(
        approval,
        WorkspaceBoundary(tmp_path),
        RollbackManager("session-1", tmp_path / "cache"),
    )


class TestSafetyLayer:
    def test_balanced_mode_requires_approval_for_writes(self, tmp_path: Path):
        tool = FileWriteTool(tmp_path)
        safety = _make_safety(tmp_path)

        result = safety.check_and_approve(tool, {"path": "file.txt", "content": "hello"})

        assert result.requires_approval is True
        assert result.risk_level == RiskLevel.NORMAL

    def test_autonomous_mode_auto_approves_standard_writes(self, tmp_path: Path):
        tool = FileWriteTool(tmp_path)
        safety = _make_safety(tmp_path, mode="autonomous")

        result = safety.check_and_approve(tool, {"path": "file.txt", "content": "hello"})

        assert result.approved is True
        assert result.requires_approval is False

    def test_high_risk_reads_still_require_approval_in_autonomous_mode(self, tmp_path: Path):
        tool = FileReadTool(tmp_path)
        safety = _make_safety(tmp_path, mode="autonomous")

        result = safety.check_and_approve(tool, {"path": ".env"})

        assert result.requires_approval is True
        assert result.risk_level == RiskLevel.HIGH

    def test_outside_workspace_path_is_blocked(self, tmp_path: Path):
        tool = FileReadTool(tmp_path)
        safety = _make_safety(tmp_path)

        result = safety.check_and_approve(tool, {"path": "../secret.txt"})

        assert result.blocked is True
        assert "outside the workspace root" in (result.reason or "")

    def test_shell_command_detects_high_risk_and_outside_path_warning(self, tmp_path: Path):
        tool = ShellExecuteTool(tmp_path)
        safety = _make_safety(tmp_path, mode="autonomous")

        result = safety.check_and_approve(
            tool,
            {
                "command": f"rm -rf {tmp_path.parent / 'other'}",
                "working_dir": ".",
            },
        )

        assert result.requires_approval is True
        assert result.risk_level == RiskLevel.HIGH
        assert "outside the workspace" in result.warnings[0]

    def test_post_action_records_and_undoes_created_file(self, tmp_path: Path):
        tool = FileWriteTool(tmp_path)
        safety = _make_safety(tmp_path)
        args = {"path": "new.txt", "content": "hello"}

        safety.pre_action(tool, args)
        result = tool.execute(**args)
        safety.post_action(tool, args, result)

        history = safety.rollback.get_history()
        assert len(history) == 1
        assert history[0].action == "created"

        safety.rollback.undo_last()
        assert not (tmp_path / "new.txt").exists()

    def test_read_only_sandbox_blocks_mutating_tool_even_when_autonomous(self, tmp_path: Path):
        tool = FileWriteTool(tmp_path)
        approval = ApprovalManager(mode="autonomous")
        safety = SafetyLayer(
            approval,
            WorkspaceBoundary(tmp_path),
            RollbackManager("session-1", tmp_path / "cache"),
            sandbox_mode="read-only",
        )
        result = safety.check_and_approve(tool, {"path": "file.txt", "content": "hello"})
        assert result.blocked is True
        assert "read-only" in (result.reason or "")

    def test_read_only_sandbox_allows_read_only_tool(self, tmp_path: Path):
        (tmp_path / "a.txt").write_text("hi")
        tool = FileReadTool(tmp_path)
        safety = SafetyLayer(
            ApprovalManager(mode="balanced"),
            WorkspaceBoundary(tmp_path),
            RollbackManager("session-1", tmp_path / "cache"),
            sandbox_mode="read-only",
        )
        result = safety.check_and_approve(tool, {"path": "a.txt"})
        assert result.approved is True

    def test_danger_full_access_does_not_block_on_sandbox_policy(self, tmp_path: Path):
        tool = FileWriteTool(tmp_path)
        approval = ApprovalManager(mode="autonomous")
        safety = SafetyLayer(
            approval,
            WorkspaceBoundary(tmp_path),
            RollbackManager("session-1", tmp_path / "cache"),
            sandbox_mode="danger-full-access",
        )
        result = safety.check_and_approve(tool, {"path": "file.txt", "content": "hello"})
        assert result.approved is True

    def test_sandbox_mode_property_returns_config_string(self, tmp_path: Path):
        safety = SafetyLayer(
            ApprovalManager(),
            WorkspaceBoundary(tmp_path),
            RollbackManager("session-1", tmp_path / "cache"),
            sandbox_mode=SandboxPosture.READ_ONLY,
        )
        assert safety.sandbox_mode == "read-only"
        assert safety.sandbox_posture is SandboxPosture.READ_ONLY

    def test_set_sandbox_mode_accepts_string_and_enum(self, tmp_path: Path):
        safety = _make_safety(tmp_path)
        safety.set_sandbox_mode("read-only")
        assert safety.sandbox_posture is SandboxPosture.READ_ONLY
        safety.set_sandbox_mode(SandboxPosture.WORKSPACE_WRITE)
        assert safety.sandbox_mode == "workspace-write"

    def test_parse_sandbox_mode_invalid(self):
        with pytest.raises(ValueError, match="Invalid sandbox mode"):
            parse_sandbox_mode("not-a-mode")

    @pytest.mark.parametrize(
        ("command", "expect_high"),
        [
            ("chmod 644 safe.txt", False),
            ("chmod -R 777 build", True),
            ("docker ps", False),
            ("docker rm -f c1", True),
            ("docker rmi img:1", True),
            ("kubectl get pods", False),
            ("kubectl delete pod foo", True),
        ],
    )
    def test_shell_high_risk_extended_patterns(
        self, tmp_path: Path, command: str, expect_high: bool
    ):
        safety = _make_safety(tmp_path)
        risk, _reason = safety.describe_risk("shell_execute", {"command": command})
        if expect_high:
            assert risk == RiskLevel.HIGH
        else:
            assert risk == RiskLevel.NORMAL
