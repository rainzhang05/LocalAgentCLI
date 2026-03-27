"""Structured turn-context snapshot diff helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha1
from typing import Any, Mapping, cast


@dataclass(frozen=True)
class ContextDiff:
    """Delta between two turn-context snapshots."""

    initial: bool
    changes: dict[str, Any]
    previous_fingerprint: str
    current_fingerprint: str

    @property
    def has_changes(self) -> bool:
        """Whether this diff carries any model-visible update."""
        return self.initial or bool(self.changes)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the diff for session metadata/debugging."""
        return {
            "initial": self.initial,
            "changes": self.changes,
            "previous_fingerprint": self.previous_fingerprint,
            "current_fingerprint": self.current_fingerprint,
        }


class ContextDiffTracker:
    """Stateful turn-context baseline tracker."""

    def __init__(self, baseline: Mapping[str, Any] | None = None):
        self._baseline = _as_plain_mapping(baseline)

    @property
    def baseline(self) -> dict[str, Any] | None:
        """Return a deep-copied baseline snapshot."""
        if self._baseline is None:
            return None
        return _as_plain_required(self._baseline)

    def set_baseline(self, baseline: Mapping[str, Any] | None) -> None:
        """Replace the baseline snapshot."""
        self._baseline = _as_plain_mapping(baseline)

    def compute(self, current: Mapping[str, Any]) -> ContextDiff:
        """Compute delta from current baseline and advance baseline."""
        current_plain = _as_plain_required(current)
        diff = self.diff(self._baseline, current_plain)
        self._baseline = current_plain
        return diff

    @staticmethod
    def diff(previous: Mapping[str, Any] | None, current: Mapping[str, Any]) -> ContextDiff:
        """Compute a structured delta without mutating state."""
        previous_plain = _as_plain_mapping(previous)
        current_plain = _as_plain_required(current)
        if previous_plain is None:
            changes = _as_plain_required(current_plain)
            previous_fingerprint = ""
            initial = True
        else:
            raw = _diff_values(previous_plain, current_plain)
            changes = raw if isinstance(raw, dict) else {}
            previous_fingerprint = _fingerprint(previous_plain)
            initial = False
        return ContextDiff(
            initial=initial,
            changes=changes,
            previous_fingerprint=previous_fingerprint,
            current_fingerprint=_fingerprint(current_plain),
        )


def render_context_diff_for_prompt(
    diff: ContextDiff,
    *,
    max_items: int = 12,
    max_line_chars: int = 160,
) -> str | None:
    """Render concise, model-visible change notes for one turn."""
    if not diff.has_changes:
        return None

    lines: list[str] = []
    if diff.initial:
        lines.append("- initial_context: established")

    updates = _flatten_change_lines(diff.changes)
    if not updates and diff.initial:
        lines.append("- no_field_level_changes: baseline only")
    else:
        hidden_count = max(len(updates) - max_items, 0)
        for line in updates[:max_items]:
            lines.append(_truncate_line(line, max_line_chars))
        if hidden_count > 0:
            lines.append(f"- … {hidden_count} more updates omitted")

    if not lines:
        return None
    return "\n".join(lines)


def _as_plain_mapping(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise TypeError("Context snapshots must be mappings.")
    return _as_plain_required(value)


def _as_plain_required(value: Mapping[str, Any]) -> dict[str, Any]:
    copied = _deep_copy_jsonable(dict(value))
    if isinstance(copied, dict):
        return cast(dict[str, Any], copied)
    raise TypeError("Context snapshot serialization must produce a mapping.")


def _deep_copy_jsonable(value: Any) -> Any:
    """Best-effort deep copy while keeping JSON-friendly structure."""
    return json.loads(json.dumps(value, sort_keys=True, default=str))


def _fingerprint(value: Any) -> str:
    serialized = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return sha1(serialized.encode("utf-8")).hexdigest()


def _diff_values(previous: Any, current: Any) -> Any | None:
    if isinstance(previous, dict) and isinstance(current, dict):
        delta: dict[str, Any] = {}
        for key in sorted(set(previous) | set(current)):
            if key not in previous:
                delta[key] = {"added": _deep_copy_jsonable(current[key])}
                continue
            if key not in current:
                delta[key] = {"removed": _deep_copy_jsonable(previous[key])}
                continue
            child = _diff_values(previous[key], current[key])
            if child is not None:
                delta[key] = child
        return delta or None

    if previous != current:
        return {
            "before": _deep_copy_jsonable(previous),
            "after": _deep_copy_jsonable(current),
        }
    return None


def _flatten_change_lines(changes: dict[str, Any]) -> list[str]:
    lines: list[str] = []

    def walk(prefix: str, node: Any) -> None:
        if isinstance(node, dict) and set(node.keys()) <= {"before", "after"}:
            before = _short_repr(node.get("before"))
            after = _short_repr(node.get("after"))
            lines.append(f"- {prefix}: {before} -> {after}")
            return

        if isinstance(node, dict) and set(node.keys()) <= {"added"}:
            lines.append(f"- {prefix}: +{_short_repr(node.get('added'))}")
            return

        if isinstance(node, dict) and set(node.keys()) <= {"removed"}:
            lines.append(f"- {prefix}: -{_short_repr(node.get('removed'))}")
            return

        if isinstance(node, dict):
            for key, value in node.items():
                child_prefix = f"{prefix}.{key}" if prefix else str(key)
                walk(child_prefix, value)
            return

        lines.append(f"- {prefix}: {_short_repr(node)}")

    walk("", changes)
    return [line for line in lines if line.strip() != "-"]


def _short_repr(value: Any, *, max_chars: int = 72) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    if len(text) <= max_chars:
        return text
    return f"{text[: max_chars - 3]}..."


def _truncate_line(line: str, max_chars: int) -> str:
    if len(line) <= max_chars:
        return line
    return f"{line[: max_chars - 3]}..."
