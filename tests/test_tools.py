"""Tests for concrete tools in localagentcli.tools."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

from localagentcli.tools import (
    DirectoryListTool,
    FileReadTool,
    FileSearchTool,
    FileWriteTool,
    GitCommitTool,
    GitDiffTool,
    GitStatusTool,
    PatchApplyTool,
    ShellExecuteTool,
    TestExecuteTool,
    ToolResult,
)


class TestToolResult:
    def test_success_factory(self):
        result = ToolResult.success("ok", output="hello", files_changed=["a.txt"])
        assert result.status == "success"
        assert result.summary == "ok"
        assert result.output == "hello"
        assert result.files_changed == ["a.txt"]

    def test_to_dict(self):
        result = ToolResult.error("bad", error="boom")
        data = result.to_dict()
        assert data["status"] == "error"
        assert data["error"] == "boom"

    def test_error_descriptor_setter_updates_instance(self):
        result = ToolResult.success("ok", output="x")
        result.error = "patched"
        assert result._error == "patched"


class TestFileReadTool:
    def test_reads_text_file(self, tmp_path: Path):
        path = tmp_path / "notes.txt"
        path.write_text("one\ntwo\nthree\n", encoding="utf-8")

        result = FileReadTool(tmp_path).execute("notes.txt", offset=1, limit=1)

        assert result.status == "success"
        assert result.output == "two\n"

    def test_reports_binary_size(self, tmp_path: Path):
        path = tmp_path / "blob.bin"
        path.write_bytes(b"abc\x00def")

        result = FileReadTool(tmp_path).execute("blob.bin")

        assert result.status == "success"
        assert "bytes (binary)" in result.output


class TestFileSearchTool:
    def test_searches_filenames(self, tmp_path: Path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.py").write_text("print('hi')", encoding="utf-8")
        (tmp_path / "src" / "notes.txt").write_text("hello", encoding="utf-8")

        result = FileSearchTool(tmp_path).execute("*.py", path="src")

        assert result.status == "success"
        assert result.output.splitlines() == ["src/app.py"]

    def test_searches_file_contents(self, tmp_path: Path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.py").write_text("alpha\nbeta\n", encoding="utf-8")

        result = FileSearchTool(tmp_path).execute("*.py", path="src", content_pattern="beta")

        assert result.status == "success"
        assert "src/app.py:2: beta" in result.output


class TestDirectoryListTool:
    def test_lists_directory(self, tmp_path: Path):
        (tmp_path / "dir").mkdir()
        (tmp_path / "dir" / "nested.txt").write_text("abc", encoding="utf-8")

        result = DirectoryListTool(tmp_path).execute("dir")

        assert result.status == "success"
        assert "dir/" in result.output
        assert "dir/nested.txt" in result.output


class TestFileWriteTool:
    def test_writes_file(self, tmp_path: Path):
        result = FileWriteTool(tmp_path).execute("out/file.txt", "hello")

        assert result.status == "success"
        assert (tmp_path / "out" / "file.txt").read_text(encoding="utf-8") == "hello"
        assert result.files_changed == ["out/file.txt"]


class TestPatchApplyTool:
    def test_applies_exact_replacement(self, tmp_path: Path):
        path = tmp_path / "file.txt"
        path.write_text("hello world", encoding="utf-8")

        result = PatchApplyTool(tmp_path).execute("file.txt", "world", "agent")

        assert result.status == "success"
        assert path.read_text(encoding="utf-8") == "hello agent"

    def test_errors_on_multiple_matches(self, tmp_path: Path):
        path = tmp_path / "file.txt"
        path.write_text("foo foo", encoding="utf-8")

        result = PatchApplyTool(tmp_path).execute("file.txt", "foo", "bar")

        assert result.status == "error"
        assert "more than one location" in result.error


class TestShellExecuteTool:
    def test_runs_successfully(self, tmp_path: Path, monkeypatch):
        captured = {}

        def fake_run(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return subprocess.CompletedProcess(args[0], 0, stdout="done\n", stderr="")

        monkeypatch.setattr("localagentcli.tools.shell_execute.subprocess.run", fake_run)

        result = ShellExecuteTool(tmp_path).execute("echo done", timeout=10)

        assert result.status == "success"
        assert result.output == "done"
        assert captured["kwargs"]["cwd"] == tmp_path.resolve()

    def test_timeout_maps_to_timeout_status(self, tmp_path: Path, monkeypatch):
        def fake_run(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd=args[0], timeout=1, output="partial", stderr="oops")

        monkeypatch.setattr("localagentcli.tools.shell_execute.subprocess.run", fake_run)

        result = ShellExecuteTool(tmp_path).execute("sleep 1", timeout=1)

        assert result.status == "timeout"
        assert "partial" in result.output


class TestTestExecuteTool:
    def test_detects_pytest_and_builds_command(self, tmp_path: Path, monkeypatch):
        (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
        captured = {}

        def fake_run(command, **kwargs):
            captured["command"] = command
            captured["kwargs"] = kwargs
            return subprocess.CompletedProcess(command, 0, stdout="1 passed in 0.01s\n", stderr="")

        monkeypatch.setattr("localagentcli.tools.test_execute.subprocess.run", fake_run)

        result = TestExecuteTool(tmp_path).execute(path="tests/test_sample.py", args="-q")

        assert result.status == "success"
        assert captured["command"][0] == sys.executable
        assert captured["command"][1:4] == ["-m", "pytest", "tests/test_sample.py"]
        assert captured["command"][-1] == "-q"

    def test_returns_error_when_framework_unknown(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("localagentcli.tools.test_execute.subprocess.run", MagicMock())

        result = TestExecuteTool(tmp_path).execute()

        assert result.status == "error"
        assert "Unable to detect test framework" in result.summary


class TestGitStatusTool:
    def test_parses_status_output(self, tmp_path: Path, monkeypatch):
        output = " M modified.txt\nA  added.txt\n?? new.txt\n"

        def fake_run(*args, **kwargs):
            return subprocess.CompletedProcess(args[0], 0, stdout=output, stderr="")

        monkeypatch.setattr("localagentcli.tools.git_status.subprocess.run", fake_run)

        result = GitStatusTool(tmp_path).execute()

        assert result.status == "success"
        assert "modified.txt" in result.output
        assert "added.txt" in result.output
        assert "new.txt" in result.output


class TestGitDiffTool:
    def test_builds_staged_diff_command(self, tmp_path: Path, monkeypatch):
        captured = {}

        def fake_run(command, **kwargs):
            captured["command"] = command
            return subprocess.CompletedProcess(command, 0, stdout="diff", stderr="")

        monkeypatch.setattr("localagentcli.tools.git_diff.subprocess.run", fake_run)

        result = GitDiffTool(tmp_path).execute(staged=True, path="file.txt")

        assert result.status == "success"
        assert captured["command"] == ["git", "diff", "--staged", "--", "file.txt"]


class TestGitCommitTool:
    def test_stages_files_then_commits(self, tmp_path: Path, monkeypatch):
        captured = []

        def fake_run(command, **kwargs):
            captured.append(command)
            if command[:2] == ["git", "add"]:
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
            if command[:2] == ["git", "commit"]:
                return subprocess.CompletedProcess(command, 0, stdout="commit ok\n", stderr="")
            if command[:2] == ["git", "diff"]:
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
            raise AssertionError(f"unexpected command: {command}")

        monkeypatch.setattr("localagentcli.tools.git_commit.subprocess.run", fake_run)

        path = tmp_path / "example.txt"
        path.write_text("hello", encoding="utf-8")
        result = GitCommitTool(tmp_path).execute("feat: add example", files=["example.txt"])

        assert result.status == "success"
        assert ["git", "add", "--", "example.txt"] in captured
        assert ["git", "commit", "-m", "feat: add example"] in captured
        assert result.files_changed == ["example.txt"]
