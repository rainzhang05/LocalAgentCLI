"""Apply context-aware patch operations to a file."""

from __future__ import annotations

from dataclasses import dataclass

from localagentcli.tools.base import Tool, ToolResult


@dataclass(frozen=True)
class _PatchOperation:
    anchors: tuple[str, ...]
    old_lines: list[str]
    new_lines: list[str]


class PatchApplyTool(Tool):
    """Apply one or more diff-style replacement operations in a file."""

    @property
    def minimum_model_default_max_tokens(self) -> int:
        """Patch construction is easier for models with a moderate token budget."""
        return 2048

    @property
    def name(self) -> str:
        return "patch_apply"

    @property
    def description(self) -> str:
        return "Apply diff-style text replacements in an existing file."

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Target file path"},
                "old_text": {
                    "type": "string",
                    "description": "Legacy exact-match text to replace once.",
                },
                "new_text": {
                    "type": "string",
                    "description": "Legacy replacement text when old_text is used.",
                },
                "patch": {
                    "type": "string",
                    "description": (
                        "Patch operations using +/- lines with optional @@ anchors. "
                        "Example: @@ class Foo / @@ def bar / -old / +new"
                    ),
                },
            },
            "required": ["path"],
        }

    def execute(
        self,
        path: str,
        old_text: str | None = None,
        new_text: str | None = None,
        patch: str | None = None,
    ) -> ToolResult:
        started = self.started_at()
        try:
            target = self.resolve_path(path)
            if not target.exists():
                raise FileNotFoundError(f"File '{path}' not found")

            original = target.read_text(encoding="utf-8")
            if not (isinstance(patch, str) and patch.strip()) and old_text is not None:
                if new_text is None:
                    raise ValueError("new_text is required when old_text is provided")
                updated = self._apply_legacy_exact_replacement(original, old_text, new_text)
            else:
                operations = self._resolve_operations(old_text, new_text, patch)
                updated = self._apply_operations(original, operations)
            target.write_text(updated, encoding="utf-8")
            rel = self.relative_path(target)
            return ToolResult.success(
                f"Patched {rel}",
                output=updated,
                files_changed=[rel],
                duration=self.started_at() - started,
            )
        except Exception as exc:
            return ToolResult.error_result(
                f"Failed to patch {path}",
                str(exc),
                duration=self.started_at() - started,
            )

    def _apply_legacy_exact_replacement(self, content: str, old_text: str, new_text: str) -> str:
        occurrences = content.count(old_text)
        if occurrences == 0:
            raise ValueError("old_text did not match any content")
        if occurrences > 1:
            raise ValueError("old_text matched more than one location")
        return content.replace(old_text, new_text, 1)

    def _resolve_operations(
        self,
        old_text: str | None,
        new_text: str | None,
        patch: str | None,
    ) -> list[_PatchOperation]:
        if isinstance(patch, str) and patch.strip():
            operations = self._parse_patch_operations(patch)
            if operations:
                return operations

        if old_text is None or new_text is None:
            raise ValueError(
                "Provide either patch content or both old_text and new_text for compatibility."
            )
        return [
            _PatchOperation(
                anchors=(),
                old_lines=old_text.splitlines(),
                new_lines=new_text.splitlines(),
            )
        ]

    def _parse_patch_operations(self, patch: str) -> list[_PatchOperation]:
        operations: list[_PatchOperation] = []
        anchors: list[str] = []
        old_lines: list[str] = []
        new_lines: list[str] = []

        def flush() -> None:
            nonlocal anchors, old_lines, new_lines
            if old_lines or new_lines:
                operations.append(
                    _PatchOperation(
                        anchors=tuple(anchor for anchor in anchors if anchor),
                        old_lines=list(old_lines),
                        new_lines=list(new_lines),
                    )
                )
                anchors = []
                old_lines = []
                new_lines = []

        for raw in patch.splitlines():
            line = raw.rstrip("\n")
            if line.startswith("*** Begin Patch") or line.startswith("*** End Patch"):
                continue
            if line.startswith("*** Update File:"):
                continue
            if line.startswith("@@"):
                if old_lines or new_lines:
                    flush()
                marker = line.strip("@ ").strip()
                if marker:
                    anchors.append(marker)
                continue
            if line.startswith("-"):
                old_lines.append(line[1:])
                continue
            if line.startswith("+"):
                new_lines.append(line[1:])
                continue
            if line.startswith(" "):
                context = line[1:]
                old_lines.append(context)
                new_lines.append(context)
                continue
            if line.strip() == "":
                old_lines.append("")
                new_lines.append("")
                continue
            old_lines.append(line)
            new_lines.append(line)

        flush()
        return operations

    def _apply_operations(self, content: str, operations: list[_PatchOperation]) -> str:
        lines = content.splitlines()
        trailing_newline = content.endswith("\n")
        cursor = 0
        for operation in operations:
            lines, cursor = self._apply_operation(lines, operation, cursor)
        updated = "\n".join(lines)
        if trailing_newline:
            return f"{updated}\n"
        return updated

    def _apply_operation(
        self,
        lines: list[str],
        operation: _PatchOperation,
        cursor: int,
    ) -> tuple[list[str], int]:
        anchor_index = self._resolve_anchor_index(lines, operation.anchors, cursor)

        if not operation.old_lines:
            insertion_index = anchor_index + 1 if anchor_index >= 0 else max(cursor, 0)
            updated = [
                *lines[:insertion_index],
                *operation.new_lines,
                *lines[insertion_index:],
            ]
            return updated, insertion_index + len(operation.new_lines)

        start, match_mode = self._find_match_index(lines, operation.old_lines, anchor_index)
        end = start + len(operation.old_lines)

        replacement = operation.new_lines
        if match_mode == "indent_fuzzy":
            replacement = self._reindent_replacement(
                replacement,
                matched_lines=lines[start:end],
                expected_old_lines=operation.old_lines,
            )

        updated = [*lines[:start], *replacement, *lines[end:]]
        return updated, start + len(replacement)

    def _resolve_anchor_index(
        self,
        lines: list[str],
        anchors: tuple[str, ...],
        cursor: int,
    ) -> int:
        if not anchors:
            return max(cursor - 1, -1)
        index = max(cursor - 1, -1)
        for anchor in anchors:
            found = -1
            search_start = max(index + 1, 0)
            for candidate in range(search_start, len(lines)):
                if anchor in lines[candidate]:
                    found = candidate
                    break
            if found >= 0:
                index = found
        return index

    def _find_match_index(
        self,
        lines: list[str],
        expected_old_lines: list[str],
        anchor_index: int,
    ) -> tuple[int, str]:
        exact_matches = self._candidate_matches(lines, expected_old_lines, strip=False)
        if exact_matches:
            return self._choose_match(exact_matches, anchor_index), "exact"

        fuzzy_matches = self._candidate_matches(lines, expected_old_lines, strip=True)
        if fuzzy_matches:
            return self._choose_match(fuzzy_matches, anchor_index), "indent_fuzzy"

        raise ValueError("old_text did not match any content")

    def _candidate_matches(
        self,
        lines: list[str],
        expected: list[str],
        *,
        strip: bool,
    ) -> list[int]:
        if not expected:
            return []
        max_start = len(lines) - len(expected)
        if max_start < 0:
            return []

        matches: list[int] = []
        for start in range(0, max_start + 1):
            chunk = lines[start : start + len(expected)]
            if strip:
                equal = all(a.strip() == b.strip() for a, b in zip(chunk, expected, strict=True))
            else:
                equal = chunk == expected
            if equal:
                matches.append(start)
        return matches

    def _choose_match(self, matches: list[int], anchor_index: int) -> int:
        if len(matches) == 1:
            return matches[0]
        if anchor_index >= 0:
            ranked = sorted(matches, key=lambda index: abs(index - anchor_index))
            if len(ranked) >= 2 and abs(ranked[0] - anchor_index) == abs(ranked[1] - anchor_index):
                raise ValueError("old_text matched more than one location")
            return ranked[0]
        raise ValueError("old_text matched more than one location")

    def _reindent_replacement(
        self,
        replacement_lines: list[str],
        *,
        matched_lines: list[str],
        expected_old_lines: list[str],
    ) -> list[str]:
        base_indent = ""
        for expected, actual in zip(expected_old_lines, matched_lines, strict=True):
            if expected.strip():
                base_indent = actual[: len(actual) - len(actual.lstrip(" \t"))]
                break

        non_empty = [line for line in replacement_lines if line.strip()]
        if not non_empty:
            return replacement_lines
        common_indent = min(len(line) - len(line.lstrip(" \t")) for line in non_empty)

        adjusted: list[str] = []
        for line in replacement_lines:
            if not line.strip():
                adjusted.append("")
                continue
            trimmed = line[common_indent:] if common_indent > 0 else line
            adjusted.append(f"{base_indent}{trimmed}")
        return adjusted
