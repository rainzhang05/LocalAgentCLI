"""Python REPL execution tool (subprocess-based)."""

from __future__ import annotations

import subprocess
import sys
import time

from localagentcli.tools.base import Tool, ToolResult


class PythonReplTool(Tool):
    """Execute Python snippets in a subprocess with bounded timeout."""

    @property
    def name(self) -> str:
        return "python_repl_execute"

    @property
    def description(self) -> str:
        return "Execute a Python code snippet and return stdout/stderr output."

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Python code snippet to execute.",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default: 30).",
                },
            },
            "required": ["code"],
        }

    @property
    def requires_approval(self) -> bool:
        return True

    @property
    def is_read_only(self) -> bool:
        return False

    def execute(self, code: str, timeout: int = 30) -> ToolResult:
        started = time.monotonic()
        if not code.strip():
            return ToolResult.error_result(
                "Python execution failed.",
                "Code snippet cannot be empty.",
                duration=time.monotonic() - started,
            )

        run_timeout = max(1, int(timeout))
        try:
            result = subprocess.run(
                [sys.executable, "-c", code],
                cwd=str(self._workspace_root),
                capture_output=True,
                text=True,
                timeout=run_timeout,
            )
        except subprocess.TimeoutExpired as exc:
            output = _coerce_text(exc.stdout)
            stderr_text = _coerce_text(exc.stderr)
            if stderr_text:
                output = f"{output}\n{stderr_text}" if output else stderr_text
            return ToolResult.timeout_result(
                "Python execution timed out.",
                f"Execution exceeded {run_timeout} seconds.",
                output=output.strip(),
                duration=time.monotonic() - started,
            )
        except Exception as exc:  # pragma: no cover - defensive branch
            return ToolResult.error_result(
                "Python execution failed.",
                str(exc),
                duration=time.monotonic() - started,
            )

        combined = (result.stdout or "") + ("\n" + result.stderr if result.stderr else "")
        output = combined.strip()
        if result.returncode == 0:
            return ToolResult.success(
                "Python execution completed.",
                output=output,
                exit_code=result.returncode,
                duration=time.monotonic() - started,
            )
        return ToolResult.error_result(
            "Python execution failed.",
            f"Python exited with code {result.returncode}.",
            output=output,
            exit_code=result.returncode,
            duration=time.monotonic() - started,
        )


def _coerce_text(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
