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
    class _FastCloseManager(MultiAgentManager):
        def _shutdown_agent_unlocked(self, agent):  # type: ignore[override]
            agent._stop_event.set()
            agent._queue.put(None)
            thread = agent._thread
            if thread is not None and thread.is_alive():
                thread.join(timeout=0.01)
            agent.status = "shutdown"
            agent.updated_at = datetime.now().isoformat()

    manager = _FastCloseManager(max_agents=2)

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
