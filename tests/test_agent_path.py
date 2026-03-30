"""Tests for multi-agent path validation and resolution."""

from __future__ import annotations

import pytest

from localagentcli.agents.agent_path import AgentPath, resolve_agent_reference


def test_root_has_expected_name():
    root = AgentPath.root()
    assert root.as_str() == AgentPath.ROOT
    assert root.name() == "root"
    assert root.is_root() is True


def test_join_builds_child_paths():
    root = AgentPath.root()
    child = root.join("researcher")
    assert child.as_str() == "/root/researcher"
    assert child.name() == "researcher"


def test_resolve_supports_relative_and_absolute_references():
    current = AgentPath.from_string("/root/researcher")
    assert current.resolve("worker") == AgentPath.from_string("/root/researcher/worker")
    assert current.resolve("/root/other") == AgentPath.from_string("/root/other")


def test_invalid_names_and_paths_are_rejected():
    with pytest.raises(
        ValueError,
        match="agent_name must use only lowercase letters, digits, and underscores",
    ):
        AgentPath.root().join("BadName")

    with pytest.raises(ValueError, match="absolute agent paths must start with `/root`"):
        AgentPath.from_string("/not-root")

    with pytest.raises(ValueError, match=r"agent_name `..` is reserved"):
        AgentPath.root().resolve("../sibling")


def test_rejects_absolute_path_with_trailing_slash():
    with pytest.raises(ValueError, match="absolute agent path must not end with `/`"):
        AgentPath.from_string("/root/test/")


def test_rejects_relative_path_with_trailing_slash():
    with pytest.raises(ValueError, match="relative agent path must not end with `/`"):
        AgentPath.root().resolve("worker/")


def test_resolve_agent_reference_defaults_to_root_and_rejects_root_target():
    with pytest.raises(ValueError, match="root is not a spawned agent"):
        resolve_agent_reference(None, "/root")


@pytest.mark.parametrize(
    ("current", "reference", "expected"),
    [
        ("/root", "researcher", "/root/researcher"),
        ("/root/researcher", "writer", "/root/researcher/writer"),
        ("/root/researcher", "/root/reviewer", "/root/reviewer"),
    ],
)
def test_resolve_agent_reference(current: str, reference: str, expected: str):
    assert resolve_agent_reference(current, reference) == AgentPath.from_string(expected)


def test_resolve_agent_reference_accepts_agent_path_inputs():
    current = AgentPath.from_string("/root/researcher")
    resolved = resolve_agent_reference(current, "worker")
    assert resolved == AgentPath.from_string("/root/researcher/worker")


def test_resolve_agent_reference_can_allow_root_when_explicitly_requested():
    assert resolve_agent_reference("/root/researcher", "/root", allow_root=True) == AgentPath.root()
