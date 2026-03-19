"""Run project tests using a detected or explicit framework."""

from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path

from localagentcli.tools.base import Tool, ToolResult


def _stringify_output(data: bytes | str | None) -> str:
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return data or ""


class TestExecuteTool(Tool):
    """Run tests via pytest, npm, cargo, or go."""

    __test__ = False

    @property
    def name(self) -> str:
        return "test_execute"

    @property
    def description(self) -> str:
        return "Run tests using a detected or explicitly provided framework."

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "framework": {"type": "string", "description": "pytest, npm, cargo, or go"},
                "path": {"type": "string", "description": "Optional test file or directory"},
                "args": {"type": "string", "description": "Additional test arguments"},
            },
        }

    def execute(
        self,
        framework: str | None = None,
        path: str | None = None,
        args: str | None = None,
    ) -> ToolResult:
        started = self.started_at()
        try:
            target = self.resolve_path(path or ".")
            detected = framework or self._detect_framework(target)
            if detected is None:
                return ToolResult.error_result(
                    "Unable to detect test framework",
                    "No supported test framework configuration was found.",
                    duration=self.started_at() - started,
                )

            command = self._build_command(detected, path, args)
            completed = subprocess.run(
                command,
                cwd=self._workspace_root,
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )
            output = self._combine_output(completed.stdout, completed.stderr)
            duration = self.started_at() - started
            if completed.returncode == 0:
                return ToolResult.success(
                    f"Ran {detected} tests",
                    output=output,
                    exit_code=0,
                    duration=duration,
                )
            return ToolResult.error_result(
                f"Failed running {detected} tests",
                f"Test command exited with status {completed.returncode}",
                output=output,
                exit_code=completed.returncode,
                duration=duration,
            )
        except subprocess.TimeoutExpired as exc:
            return ToolResult.timeout_result(
                "Test execution timed out",
                error="The test command exceeded the timeout.",
                output=self._combine_output(
                    _stringify_output(exc.stdout),
                    _stringify_output(exc.stderr),
                ),
                duration=self.started_at() - started,
            )
        except Exception as exc:
            return ToolResult.error_result(
                "Failed to execute tests",
                str(exc),
                duration=self.started_at() - started,
            )

    def _detect_framework(self, target: Path) -> str | None:
        current = target if target.is_dir() else target.parent
        search_roots = [current, *current.parents]
        for root in search_roots:
            if (root / "pytest.ini").exists():
                return "pytest"
            pyproject = root / "pyproject.toml"
            if pyproject.exists() and "[tool.pytest" in pyproject.read_text(encoding="utf-8"):
                return "pytest"
            setup_cfg = root / "setup.cfg"
            if setup_cfg.exists() and "[tool:pytest]" in setup_cfg.read_text(encoding="utf-8"):
                return "pytest"
            package_json = root / "package.json"
            if package_json.exists():
                text = package_json.read_text(encoding="utf-8")
                if '"test"' in text:
                    return "npm"
            if (root / "Cargo.toml").exists():
                return "cargo"
            if (root / "go.mod").exists():
                return "go"
        return None

    def _build_command(self, framework: str, path: str | None, args: str | None) -> list[str]:
        fragments: list[str]
        if framework == "pytest":
            fragments = [sys.executable, "-m", "pytest"]
            if path:
                fragments.append(path)
        elif framework == "npm":
            fragments = ["npm", "test"]
            passthrough: list[str] = []
            if path:
                passthrough.append(path)
            if args:
                passthrough.extend(shlex.split(args))
            if passthrough:
                fragments.extend(["--", *passthrough])
            return fragments
        elif framework == "cargo":
            fragments = ["cargo", "test"]
            if path:
                fragments.append(path)
        elif framework == "go":
            fragments = ["go", "test", path or "./..."]
        else:
            raise ValueError(f"Unsupported test framework '{framework}'")

        if args:
            fragments.extend(shlex.split(args))
        return fragments

    def _combine_output(self, stdout: str, stderr: str) -> str:
        if stdout and stderr:
            return f"{stdout.rstrip()}\n{stderr.rstrip()}".strip()
        return (stdout or stderr).rstrip()
