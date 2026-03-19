"""Packaging-oriented CLI subprocess tests."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

_CONFIG_TEMPLATE = """
[general]
default_mode = "{mode}"
workspace = "."
logging_level = "normal"

[model]
active_model = ""

[provider]
active_provider = ""

[safety]
approval_mode = "balanced"

[generation]
temperature = 0.7
max_tokens = 4096
top_p = 1.0

[timeouts]
shell_command = 120
model_response = 300
inactivity = 600

[providers]
"""


def _cli_env(home: Path) -> dict[str, str]:
    """Build an environment that redirects LocalAgentCLI storage to a temp home."""
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    return env


def _write_config(home: Path, mode: str = "agent") -> Path:
    """Create a valid config file under a temporary home directory."""
    config_dir = home / ".localagent"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "config.toml"
    config_path.write_text(
        textwrap.dedent(_CONFIG_TEMPLATE.format(mode=mode)).strip() + "\n",
        encoding="utf-8",
    )
    return config_path


def _run_cli(home: Path, user_input: str, timeout: int = 20) -> subprocess.CompletedProcess[str]:
    """Run the CLI in a subprocess and return the completed result."""
    return subprocess.run(
        [sys.executable, "-m", "localagentcli"],
        input=user_input,
        text=True,
        capture_output=True,
        cwd=REPO_ROOT,
        env=_cli_env(home),
        timeout=timeout,
    )


def _interrupt_process(process: subprocess.Popen[str]) -> None:
    """Send the closest available Ctrl+C-style signal for the current platform."""
    if os.name == "nt":
        process.send_signal(signal.CTRL_BREAK_EVENT)
        return
    process.send_signal(signal.SIGINT)


class TestPackagingCLI:
    def test_first_run_setup_creates_config(self, tmp_path: Path):
        home = tmp_path / "home"
        result = _run_cli(home, ".\nagent\nnormal\n/exit\n")

        assert result.returncode == 0
        assert "Setup Wizard" in result.stdout
        assert "You're all set!" in result.stdout
        assert (home / ".localagent" / "config.toml").exists()

    def test_session_restore_across_relaunches(self, tmp_path: Path):
        home = tmp_path / "home"
        _write_config(home)

        first = _run_cli(home, "/mode chat\n/session save smoke\n/session new\n/exit\n")
        assert first.returncode == 0
        assert "Switched to chat mode." in first.stdout
        assert "Session saved to" in first.stdout

        second = _run_cli(home, "/session load smoke\n/status\n/exit\n")
        assert second.returncode == 0
        assert "Session 'smoke' loaded." in second.stdout
        assert "Mode:          chat" in second.stdout
        assert "Session:       smoke" in second.stdout

    def test_keyboard_interrupt_returns_to_prompt(self, tmp_path: Path):
        home = tmp_path / "home"
        _write_config(home)

        process = subprocess.Popen(
            [sys.executable, "-m", "localagentcli"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=REPO_ROOT,
            env=_cli_env(home),
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )

        try:
            time.sleep(1)
            _interrupt_process(process)
            time.sleep(0.5)
            assert process.stdin is not None
            process.stdin.write("/exit\n")
            process.stdin.flush()
            stdout, stderr = process.communicate(timeout=20)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()

        assert process.returncode == 0
        assert stdout.count("LocalAgent | mode: agent") >= 2
        assert "Goodbye." in stdout
        assert "Traceback" not in stderr
