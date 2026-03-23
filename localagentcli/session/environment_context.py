"""Gather and format environment context for model injection."""

from __future__ import annotations

import os
import platform
import time
from datetime import datetime
from pathlib import Path


def get_environment_context_xml(workspace_path: str | Path | None = None) -> str:
    """Build an XML representation of the current execution environment."""
    lines: list[str] = ["<environment_context>"]

    # Add Current Working Directory
    if workspace_path:
        # Use provided workspace path, preferring absolute
        try:
            cwd = str(Path(workspace_path).resolve())
        except Exception:
            cwd = str(workspace_path)
        lines.append(f"  <cwd>{cwd}</cwd>")
    else:
        # Fallback to process cwd
        try:
            lines.append(f"  <cwd>{os.getcwd()}</cwd>")
        except Exception:
            pass

    # Add Shell
    # Typically set by the OS in POSIX
    shell = os.environ.get("SHELL", "")
    if not shell and platform.system() == "Windows":
        shell = os.environ.get("COMSPEC", "cmd.exe")
    if not shell:
        shell = "unknown"
    lines.append(f"  <shell>{shell}</shell>")

    # Add Current Date/Time
    # ISO-like format: YYYY-MM-DD HH:MM:SS
    now = datetime.now()
    lines.append(f"  <current_date>{now.strftime('%Y-%m-%d %H:%M:%S')}</current_date>")

    # Add Timezone
    try:
        # time.tzname returns a tuple: (non-DST, DST)
        tz = time.tzname[time.daylight] if time.daylight else time.tzname[0]
        if tz:
            lines.append(f"  <timezone>{tz}</timezone>")
    except Exception:
        pass

    lines.append("</environment_context>")
    return "\n".join(lines)
