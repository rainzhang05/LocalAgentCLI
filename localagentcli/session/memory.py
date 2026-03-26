"""Long-horizon memory helpers for session persistence and prompt context."""

from __future__ import annotations

from datetime import datetime

from localagentcli.session.state import Session

LONG_HORIZON_MEMORY_KEY = "long_horizon_memory"


def extract_session_memory_entries(session: Session, max_items: int = 12) -> list[dict[str, str]]:
    """Extract durable memory candidates from session history.

    Current extraction is intentionally conservative:
    - system summaries produced by compaction (`is_summary=True`)
    - assistant messages explicitly tagged with `metadata.memory_candidate=True`
    """
    entries: list[dict[str, str]] = []
    seen: set[str] = set()

    for message in reversed(session.history):
        content = message.content.strip()
        if not content:
            continue

        if message.is_summary:
            kind = "summary"
        elif message.role == "assistant" and bool(message.metadata.get("memory_candidate", False)):
            kind = str(message.metadata.get("memory_kind", "insight") or "insight")
        else:
            continue

        normalized = " ".join(content.split())
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)

        entries.append(
            {
                "kind": kind,
                "content": content,
                "source_timestamp": message.timestamp.isoformat(),
                "updated_at": datetime.now().isoformat(),
            }
        )
        if len(entries) >= max_items:
            break

    return entries


def merge_long_horizon_memory(
    session: Session,
    persisted_entries: list[dict[str, str]],
    max_items: int = 8,
) -> None:
    """Merge persisted workspace memory into session metadata with de-duplication."""
    existing = session.metadata.get(LONG_HORIZON_MEMORY_KEY, [])
    merged: list[dict[str, str]] = []
    seen: set[str] = set()

    for collection in (persisted_entries, existing):
        if not isinstance(collection, list):
            continue
        for entry in collection:
            if not isinstance(entry, dict):
                continue
            content = str(entry.get("content", "") or "").strip()
            if not content:
                continue
            normalized = " ".join(content.split())
            if normalized in seen:
                continue
            seen.add(normalized)
            merged.append(
                {
                    "kind": str(entry.get("kind", "memory") or "memory"),
                    "content": content,
                    "source_timestamp": str(entry.get("source_timestamp", "") or ""),
                    "updated_at": str(entry.get("updated_at", "") or ""),
                }
            )
            if len(merged) >= max_items:
                session.metadata[LONG_HORIZON_MEMORY_KEY] = merged
                return

    session.metadata[LONG_HORIZON_MEMORY_KEY] = merged


def render_long_horizon_memory_instruction(
    metadata_value: object,
    max_items: int = 5,
) -> str:
    """Render memory metadata into one compact system-instruction block."""
    if not isinstance(metadata_value, list):
        return ""

    lines: list[str] = []
    for entry in metadata_value:
        if not isinstance(entry, dict):
            continue
        content = str(entry.get("content", "") or "").strip()
        if not content:
            continue
        lines.append(f"- {content}")
        if len(lines) >= max_items:
            break

    if not lines:
        return ""
    return "Long-horizon memory:\n" + "\n".join(lines)
