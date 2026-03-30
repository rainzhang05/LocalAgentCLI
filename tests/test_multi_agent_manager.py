"""Tests for path-based multi-agent manager baseline behavior."""

from __future__ import annotations

import time
from datetime import datetime

import pytest

from localagentcli.agents.multi_agent import MultiAgentManager


def test_spawn_wait_send_close_resume_round_trip():
    manager = MultiAgentManager(max_agents=4)

    def worker(_agent, prompt: str) -> str:
        return prompt.upper()

    agent = manager.spawn_agent(
        "hello",
        worker=worker,
        current_agent_path="/root",
        task_name="worker",
    )
    assert agent.path.as_str() == "/root/worker"

    statuses, timed_out = manager.wait_for_targets(
        ["worker"],
        current_agent_path="/root",
        timeout_ms=1000,
    )
    assert timed_out is False
    assert statuses["/root/worker"] == "completed"

    updated = manager.send_input("worker", "next", current_agent_path="/root")
    assert updated.path.as_str() == "/root/worker"

    statuses, timed_out = manager.wait_for_targets(
        ["worker"],
        current_agent_path="/root",
        timeout_ms=1000,
    )
    assert timed_out is False
    assert statuses["/root/worker"] == "completed"

    closed, previous = manager.close_agent("worker", current_agent_path="/root")
    assert closed.status == "shutdown"
    assert previous in {"pending_init", "running", "completed", "failed", "shutdown"}

    resumed = manager.resume_agent(
        "worker",
        current_agent_path="/root",
        input_override="again",
    )
    assert resumed.path.as_str() == "/root/worker"

    statuses, timed_out = manager.wait_for_targets(
        ["worker"],
        current_agent_path="/root",
        timeout_ms=1000,
    )
    assert timed_out is False
    assert statuses["/root/worker"] == "completed"

    manager.shutdown()


def test_wait_can_timeout_when_target_is_not_final():
    manager = MultiAgentManager(max_agents=2)

    def worker(_agent, prompt: str) -> str:
        time.sleep(0.25)
        return prompt

    agent = manager.spawn_agent("slow", worker=worker, current_agent_path="/root")

    statuses, timed_out = manager.wait_for_targets(
        [agent.path.name()],
        current_agent_path="/root",
        timeout_ms=20,
    )

    assert statuses == {}
    assert timed_out is True
    manager.shutdown()


def test_send_input_raises_for_unknown_target():
    manager = MultiAgentManager(max_agents=2)
    with pytest.raises(ValueError, match="live agent path `/root/missing` not found"):
        manager.send_input("missing", "hello", current_agent_path="/root")


def test_agent_limit_is_enforced():
    manager = MultiAgentManager(max_agents=1)

    def worker(_agent, prompt: str) -> str:
        return prompt

    manager.spawn_agent("first", worker=worker, current_agent_path="/root")
    with pytest.raises(ValueError, match="agent limit reached"):
        manager.spawn_agent("second", worker=worker, current_agent_path="/root")

    manager.shutdown()


def test_resume_ignores_stale_thread_shutdown_from_previous_generation():
    manager = MultiAgentManager(max_agents=2)

    def worker(_agent, prompt: str) -> str:
        if prompt == "slow":
            time.sleep(0.2)
        return prompt

    manager.spawn_agent(
        "slow",
        worker=worker,
        current_agent_path="/root",
        task_name="worker",
    )
    manager.close_agent("worker", current_agent_path="/root")

    # Simulate the close/race window where stale worker state can briefly read
    # as completed even though the agent is closed (stop event already set).
    agent = manager._agents["/root/worker"]
    assert agent._stop_event.is_set() is True
    agent.status = "completed"
    agent.updated_at = datetime.now().isoformat()

    manager.resume_agent(
        "worker",
        current_agent_path="/root",
        input_override="fast",
    )
    statuses, timed_out = manager.wait_for_targets(
        ["worker"],
        current_agent_path="/root",
        timeout_ms=1000,
    )
    assert timed_out is False
    assert statuses["/root/worker"] == "completed"

    time.sleep(0.25)
    snapshot = manager.snapshot()
    assert snapshot["/root/worker"]["status"] == "completed"

    manager.shutdown()


def test_load_snapshot_rehydrates_non_final_entries_as_shutdown_and_allows_resume():
    manager = MultiAgentManager(max_agents=4)

    def worker(_agent, prompt: str) -> str:
        return prompt.upper()

    count = manager.load_snapshot(
        {
            "/root/researcher": {
                "path": "/root/researcher",
                "name": "researcher",
                "status": "running",
                "nickname": "researcher",
                "role": "analysis",
                "last_error": "",
                "task_count": 3,
                "updated_at": datetime.now().isoformat(),
            },
            "/root/reviewer": {
                "path": "/root/reviewer",
                "name": "reviewer",
                "status": "completed",
                "nickname": "reviewer",
                "role": "review",
                "last_error": "",
                "task_count": 1,
                "updated_at": datetime.now().isoformat(),
            },
        },
        worker=worker,
    )

    assert count == 2
    snapshot = manager.snapshot()
    assert snapshot["/root/researcher"]["status"] == "shutdown"
    assert snapshot["/root/reviewer"]["status"] == "completed"

    with pytest.raises(ValueError, match="is closed"):
        manager.send_input("researcher", "next", current_agent_path="/root")

    manager.resume_agent("researcher", current_agent_path="/root", input_override="next")
    statuses, timed_out = manager.wait_for_targets(
        ["researcher"],
        current_agent_path="/root",
        timeout_ms=1000,
    )
    assert timed_out is False
    assert statuses["/root/researcher"] == "completed"

    manager.shutdown()


def test_clear_shuts_down_and_removes_all_agents():
    manager = MultiAgentManager(max_agents=4)

    def worker(_agent, prompt: str) -> str:
        return prompt

    manager.spawn_agent("one", worker=worker, current_agent_path="/root", task_name="one")
    manager.spawn_agent("two", worker=worker, current_agent_path="/root", task_name="two")

    removed = manager.clear()

    assert removed == 2
    assert manager.snapshot() == {}
