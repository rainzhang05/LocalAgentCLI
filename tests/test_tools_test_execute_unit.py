"""Unit tests for test_execute tool helpers (no real subprocess I/O)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from localagentcli.tools.test_execute import TestExecuteTool, _stringify_output


def test_stringify_output_bytes_and_none():
    assert "ab" in _stringify_output(b"ab\xff")
    assert _stringify_output(None) == ""


@pytest.fixture
def tool(tmp_path: Path) -> TestExecuteTool:
    t = TestExecuteTool.__new__(TestExecuteTool)
    t._workspace_root = tmp_path
    t.resolve_path = lambda p: (tmp_path / p).resolve() if p != "." else tmp_path.resolve()
    t.started_at = MagicMock(side_effect=[0.0, 1.0, 2.0, 3.0])
    return t


def test_build_command_pytest_and_args(tool: TestExecuteTool):
    cmd = tool._build_command("pytest", "tests", "-q")
    assert "pytest" in cmd
    assert "tests" in cmd
    assert "-q" in cmd


def test_build_command_npm_with_path_and_args(tool: TestExecuteTool):
    cmd = tool._build_command("npm", "src", "-- --watch")
    assert cmd[:2] == ["npm", "test"]
    assert "--" in cmd


def test_build_command_cargo_go(tool: TestExecuteTool):
    assert "cargo" in tool._build_command("cargo", "pkg", None)
    assert tool._build_command("go", None, None)[-1] == "./..."


def test_build_command_unsupported(tool: TestExecuteTool):
    with pytest.raises(ValueError, match="Unsupported"):
        tool._build_command("make", None, None)


def test_combine_output(tool: TestExecuteTool):
    assert tool._combine_output("a", "b") == "a\nb"
    assert tool._combine_output("only", "") == "only"
    assert tool._combine_output("", "erronly") == "erronly"


def test_build_command_pytest_no_path(tool: TestExecuteTool):
    cmd = tool._build_command("pytest", None, None)
    assert cmd[-1] not in ("", None)


def test_build_command_npm_args_only(tool: TestExecuteTool):
    cmd = tool._build_command("npm", None, "-- --coverage")
    assert "--coverage" in cmd


def test_detect_framework_pytest_ini(tool: TestExecuteTool, tmp_path: Path):
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    assert tool._detect_framework(tmp_path) == "pytest"


def test_detect_framework_pyproject_pytest(tool: TestExecuteTool, tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
    assert tool._detect_framework(tmp_path) == "pytest"


def test_detect_framework_setup_cfg(tool: TestExecuteTool, tmp_path: Path):
    (tmp_path / "setup.cfg").write_text("[tool:pytest]\n", encoding="utf-8")
    assert tool._detect_framework(tmp_path) == "pytest"


def test_detect_framework_npm(tool: TestExecuteTool, tmp_path: Path):
    (tmp_path / "package.json").write_text('{"scripts":{"test":"jest"}}\n', encoding="utf-8")
    assert tool._detect_framework(tmp_path) == "npm"


def test_detect_framework_cargo(tool: TestExecuteTool, tmp_path: Path):
    (tmp_path / "Cargo.toml").write_text("[package]\nname='x'\n", encoding="utf-8")
    assert tool._detect_framework(tmp_path) == "cargo"


def test_detect_framework_go(tool: TestExecuteTool, tmp_path: Path):
    (tmp_path / "go.mod").write_text("module x\n", encoding="utf-8")
    assert tool._detect_framework(tmp_path) == "go"


def test_execute_no_framework(tool: TestExecuteTool, tmp_path: Path):
    r = tool.execute()
    assert "Unable to detect" in r.summary


def test_execute_success(tmp_path: Path):
    t = TestExecuteTool.__new__(TestExecuteTool)
    t._workspace_root = tmp_path
    t.resolve_path = lambda p: tmp_path
    t.started_at = MagicMock(side_effect=[0.0, 0.1])
    (tmp_path / "pytest.ini").touch()
    completed = MagicMock()
    completed.returncode = 0
    completed.stdout = "ok"
    completed.stderr = ""
    with patch("localagentcli.tools.test_execute.subprocess.run", return_value=completed):
        r = t.execute(framework="pytest")
    assert r.status == "success"


def test_execute_failure_exit_code(tmp_path: Path):
    t = TestExecuteTool.__new__(TestExecuteTool)
    t._workspace_root = tmp_path
    t.resolve_path = lambda p: tmp_path
    t.started_at = MagicMock(side_effect=[0.0, 0.1])
    completed = MagicMock()
    completed.returncode = 2
    completed.stdout = ""
    completed.stderr = "err"
    with patch("localagentcli.tools.test_execute.subprocess.run", return_value=completed):
        r = t.execute(framework="pytest")
    assert r.status != "success"


def test_execute_timeout(tmp_path: Path):
    t = TestExecuteTool.__new__(TestExecuteTool)
    t._workspace_root = tmp_path
    t.resolve_path = lambda p: tmp_path
    t.started_at = MagicMock(side_effect=[0.0, 0.1])
    exc = subprocess.TimeoutExpired(cmd="x", timeout=1)
    exc.stdout = b"o"
    exc.stderr = b"e"
    with patch("localagentcli.tools.test_execute.subprocess.run", side_effect=exc):
        r = t.execute(framework="pytest")
    assert r.status == "timeout"


def test_execute_generic_exception(tmp_path: Path):
    t = TestExecuteTool.__new__(TestExecuteTool)
    t._workspace_root = tmp_path
    t.resolve_path = lambda p: tmp_path
    t.started_at = MagicMock(side_effect=[0.0, 0.1])
    with patch("localagentcli.tools.test_execute.subprocess.run", side_effect=RuntimeError("boom")):
        r = t.execute(framework="pytest")
    assert "boom" in (r.error or "")
