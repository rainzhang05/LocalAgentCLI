"""Search for matching files and content within the workspace."""

from __future__ import annotations

import fnmatch
import re

from localagentcli.tools.base import Tool, ToolResult


def _looks_like_glob(pattern: str) -> bool:
    return any(char in pattern for char in "*?[]")


class FileSearchTool(Tool):
    """Search file paths and optional file contents."""

    @property
    def name(self) -> str:
        return "file_search"

    @property
    def description(self) -> str:
        return "Search for files by path pattern and optionally search their contents."

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob or regex file pattern"},
                "path": {"type": "string", "description": "Directory to search", "default": "."},
                "content_pattern": {
                    "type": "string",
                    "description": "Optional regular expression to search file contents",
                },
            },
            "required": ["pattern"],
        }

    @property
    def requires_approval(self) -> bool:
        return False

    @property
    def is_read_only(self) -> bool:
        return True

    def execute(
        self,
        pattern: str,
        path: str = ".",
        content_pattern: str | None = None,
    ) -> ToolResult:
        started = self.started_at()
        try:
            base = self.resolve_path(path)
            if not base.exists() or not base.is_dir():
                raise FileNotFoundError(f"Directory '{path}' not found")

            matcher = self._build_matcher(pattern)
            content_regex = re.compile(content_pattern) if content_pattern else None
            matches: list[str] = []

            for target in sorted(candidate for candidate in base.rglob("*") if candidate.is_file()):
                rel = self.relative_path(target)
                if not matcher(rel):
                    continue
                if content_regex is None:
                    matches.append(rel)
                    continue

                try:
                    text = target.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue

                for line_number, line in enumerate(text.splitlines(), start=1):
                    if content_regex.search(line):
                        matches.append(f"{rel}:{line_number}: {line}")

            summary = f"Found {len(matches)} match(es) for '{pattern}'"
            return ToolResult.success(
                summary,
                output="\n".join(matches),
                duration=self.started_at() - started,
            )
        except re.error as exc:
            return ToolResult.error_result(
                f"Invalid search pattern '{pattern}'",
                str(exc),
                duration=self.started_at() - started,
            )
        except Exception as exc:
            return ToolResult.error_result(
                f"Search failed for '{pattern}'",
                str(exc),
                duration=self.started_at() - started,
            )

    def _build_matcher(self, pattern: str):
        if _looks_like_glob(pattern):
            return lambda rel: fnmatch.fnmatch(rel, pattern)
        regex = re.compile(pattern)
        return lambda rel: bool(regex.search(rel))
