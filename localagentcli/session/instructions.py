"""Workspace instruction discovery and system-prompt helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from localagentcli.models.backends.base import ModelMessage
from localagentcli.session.memory import (
    LONG_HORIZON_MEMORY_KEY,
    render_long_horizon_memory_instruction,
)
from localagentcli.session.state import Message, Session

AGENTS_FILENAME = "AGENTS.md"
WORKSPACE_INSTRUCTION_KEY = "workspace_instruction"
WORKSPACE_INSTRUCTION_PATH_KEY = "workspace_instruction_path"
WORKSPACE_INSTRUCTION_MTIME_KEY = "workspace_instruction_mtime_ns"


@dataclass(frozen=True)
class WorkspaceInstruction:
    """Resolved repository-level instruction file."""

    path: str
    content: str
    mtime_ns: int


def discover_workspace_instruction(workspace: str) -> WorkspaceInstruction | None:
    """Return the repository-root AGENTS.md for a workspace when present."""
    root = _instruction_search_root(workspace)
    if root is None:
        return None

    path = root / AGENTS_FILENAME
    if not path.is_file():
        return None

    content = path.read_text(encoding="utf-8").strip()
    if not content:
        return None

    return WorkspaceInstruction(
        path=str(path),
        content=content,
        mtime_ns=path.stat().st_mtime_ns,
    )


def sync_workspace_instruction(session: Session) -> bool:
    """Refresh cached workspace instructions in session metadata."""
    detected = discover_workspace_instruction(session.workspace)
    metadata = session.metadata

    if detected is None:
        if not _has_workspace_instruction(metadata):
            return False
        metadata.pop(WORKSPACE_INSTRUCTION_KEY, None)
        metadata.pop(WORKSPACE_INSTRUCTION_PATH_KEY, None)
        metadata.pop(WORKSPACE_INSTRUCTION_MTIME_KEY, None)
        return True

    if (
        metadata.get(WORKSPACE_INSTRUCTION_PATH_KEY) == detected.path
        and metadata.get(WORKSPACE_INSTRUCTION_MTIME_KEY) == detected.mtime_ns
        and metadata.get(WORKSPACE_INSTRUCTION_KEY) == detected.content
    ):
        return False

    metadata[WORKSPACE_INSTRUCTION_PATH_KEY] = detected.path
    metadata[WORKSPACE_INSTRUCTION_MTIME_KEY] = detected.mtime_ns
    metadata[WORKSPACE_INSTRUCTION_KEY] = detected.content
    return True


def build_system_instructions(session: Session) -> list[str]:
    """Return repository instructions followed by user-pinned instructions."""
    instructions: list[str] = []

    repo_instruction = session.metadata.get(WORKSPACE_INSTRUCTION_KEY)
    if isinstance(repo_instruction, str) and repo_instruction.strip():
        instructions.append(repo_instruction.strip())

    instructions.extend(
        instruction.strip()
        for instruction in session.pinned_instructions
        if isinstance(instruction, str) and instruction.strip()
    )

    long_horizon_block = render_long_horizon_memory_instruction(
        session.metadata.get(LONG_HORIZON_MEMORY_KEY, [])
    )
    if long_horizon_block:
        instructions.append(long_horizon_block)
    return instructions


def build_instruction_messages(session: Session) -> list[Message]:
    """Represent active system instructions as session messages for token estimation."""
    timestamp = datetime.now()
    return [
        Message(role="system", content=instruction, timestamp=timestamp)
        for instruction in build_system_instructions(session)
    ]


def build_conversation_model_messages(session: Session) -> list[ModelMessage]:
    """Assemble model input: workspace + pinned instructions, then non-system history.

    History entries with role ``system`` are folded into the leading system message
    in order (after workspace and pinned text). Other roles are passed through.
    """
    from localagentcli.session.environment_context import get_environment_context_xml

    system_parts = build_system_instructions(session)
    system_parts.append(get_environment_context_xml(session.workspace))
    conversation: list[ModelMessage] = []

    for message in session.history:
        if message.role == "system":
            system_parts.append(message.content)
            continue
        conversation.append(
            ModelMessage(
                role=message.role,
                content=message.content,
                metadata=dict(message.metadata),
            )
        )

    if system_parts:
        return [ModelMessage(role="system", content="\n\n".join(system_parts)), *conversation]
    return conversation


def _instruction_search_root(workspace: str) -> Path | None:
    try:
        resolved = Path(workspace).expanduser().resolve()
    except Exception:
        return None

    if not resolved.exists():
        return None

    workspace_root = resolved if resolved.is_dir() else resolved.parent
    repo_root = _find_repo_root(workspace_root)
    if repo_root is not None:
        return repo_root
    return workspace_root


def _find_repo_root(start: Path) -> Path | None:
    for candidate in (start, *start.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def _has_workspace_instruction(metadata: dict) -> bool:
    return any(
        key in metadata
        for key in (
            WORKSPACE_INSTRUCTION_KEY,
            WORKSPACE_INSTRUCTION_PATH_KEY,
            WORKSPACE_INSTRUCTION_MTIME_KEY,
        )
    )
