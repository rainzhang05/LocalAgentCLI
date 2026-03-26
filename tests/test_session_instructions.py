"""Tests for repository instruction discovery and system prompt helpers."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from localagentcli.session.instructions import (
    build_conversation_model_messages,
    build_system_instructions,
    discover_workspace_instruction,
    sync_workspace_instruction,
)
from localagentcli.session.state import Message, Session


def _make_session(workspace: Path) -> Session:
    now = datetime(2025, 1, 15, 10, 0, 0)
    return Session(
        id="session-1",
        name=None,
        mode="agent",
        model="",
        provider="",
        workspace=str(workspace),
        created_at=now,
        updated_at=now,
    )


def test_discover_workspace_instruction_uses_repo_root(tmp_path: Path):
    repo_root = tmp_path / "repo"
    workspace = repo_root / "nested" / "project"
    workspace.mkdir(parents=True)
    (repo_root / ".git").mkdir()
    (repo_root / "AGENTS.md").write_text("Repo instructions.", encoding="utf-8")

    instruction = discover_workspace_instruction(str(workspace))

    assert instruction is not None
    assert instruction.content == "Repo instructions."
    assert instruction.path == str(repo_root / "AGENTS.md")


def test_sync_workspace_instruction_updates_session_metadata(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    agents_path = workspace / "AGENTS.md"
    agents_path.write_text("Use safe defaults.", encoding="utf-8")
    session = _make_session(workspace)

    changed = sync_workspace_instruction(session)

    assert changed is True
    assert session.metadata["workspace_instruction"] == "Use safe defaults."
    assert session.metadata["workspace_instruction_path"] == str(agents_path)


def test_build_system_instructions_places_agents_before_pinned(tmp_path: Path):
    session = _make_session(tmp_path)
    session.metadata["workspace_instruction"] = "Follow AGENTS.md."
    session.pinned_instructions.append("Keep answers concise.")

    assert build_system_instructions(session) == [
        "Follow AGENTS.md.",
        "Keep answers concise.",
    ]


def test_build_system_instructions_appends_long_horizon_memory_block(tmp_path: Path):
    session = _make_session(tmp_path)
    session.metadata["workspace_instruction"] = "Follow AGENTS.md."
    session.pinned_instructions.append("Keep answers concise.")
    session.metadata["long_horizon_memory"] = [
        {
            "kind": "summary",
            "content": "User prefers minimal diffs.",
            "source_timestamp": "",
            "updated_at": "",
        }
    ]

    instructions = build_system_instructions(session)

    assert instructions[0] == "Follow AGENTS.md."
    assert instructions[1] == "Keep answers concise."
    assert instructions[2].startswith("Long-horizon memory:\n")
    assert "User prefers minimal diffs." in instructions[2]


def test_build_conversation_model_messages_merges_system_layers(tmp_path: Path):
    session = _make_session(tmp_path)
    session.metadata["workspace_instruction"] = "Repo line."
    session.pinned_instructions.append("Pinned line.")
    now = session.created_at
    session.history.append(Message(role="system", content="History system.", timestamp=now))
    session.history.append(Message(role="user", content="Hi", timestamp=now))

    messages = build_conversation_model_messages(session)

    assert len(messages) == 2
    assert messages[0].role == "system"
    parts = messages[0].content.split("\n\n")
    assert parts[0] == "Repo line."
    assert parts[1] == "Pinned line."
    assert "<environment_context>" in parts[2]
    assert parts[3] == "History system."
    assert messages[1].role == "user"
    assert messages[1].content == "Hi"
