"""Path-based multi-agent runtime manager.

This module provides a lightweight, in-process baseline for path-addressable
sub-agent lifecycle operations used by Slice 4 dynamic tool surfaces.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from queue import Empty, Queue
from threading import Event, RLock, Thread
from typing import Literal

from localagentcli.agents.agent_path import AgentPath, resolve_agent_reference

AgentStatus = Literal[
    "pending_init",
    "running",
    "waiting_input",
    "completed",
    "failed",
    "timed_out",
    "shutdown",
    "not_found",
]

_FINAL_STATUSES: set[str] = {"completed", "failed", "shutdown", "not_found"}


@dataclass
class ManagedAgent:
    """In-memory state for one spawned sub-agent."""

    path: AgentPath
    worker: Callable[[ManagedAgent, str], str]
    nickname: str | None = None
    role: str | None = None
    status: AgentStatus = "pending_init"
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    last_input: str = ""
    last_output: str = ""
    last_error: str = ""
    task_count: int = 0
    _queue: Queue[str | None] = field(default_factory=Queue, repr=False)
    _stop_event: Event = field(default_factory=Event, repr=False)
    _thread: Thread | None = field(default=None, repr=False)

    def to_summary(self) -> dict[str, object]:
        """Serialize a stable summary for metadata surfaces."""
        return {
            "path": self.path.as_str(),
            "name": self.path.name(),
            "status": self.status,
            "nickname": self.nickname or "",
            "role": self.role or "",
            "last_error": self.last_error,
            "task_count": self.task_count,
            "updated_at": self.updated_at,
        }


class MultiAgentManager:
    """Manage path-based sub-agent lifecycle in one runtime process."""

    def __init__(self, max_agents: int = 16):
        self._max_agents = max(1, max_agents)
        self._agents: dict[str, ManagedAgent] = {}
        self._lock = RLock()
        self._generated_name_counter = 0

    @property
    def max_agents(self) -> int:
        return self._max_agents

    def snapshot(self) -> dict[str, dict[str, object]]:
        """Return active-agent summaries keyed by canonical path."""
        with self._lock:
            return {
                path: agent.to_summary()
                for path, agent in sorted(self._agents.items(), key=lambda item: item[0])
            }

    def spawn_agent(
        self,
        task: str,
        *,
        worker: Callable[[ManagedAgent, str], str],
        current_agent_path: AgentPath | str | None = None,
        task_name: str | None = None,
        role: str | None = None,
        nickname: str | None = None,
    ) -> ManagedAgent:
        """Spawn a new sub-agent and enqueue its initial task."""
        cleaned = task.strip()
        if not cleaned:
            raise ValueError("message must not be empty")

        with self._lock:
            if len(self._agents) >= self._max_agents:
                raise ValueError(f"agent limit reached ({self._max_agents})")

            parent = (
                AgentPath.root()
                if current_agent_path is None
                else self._coerce_agent_path(current_agent_path)
            )
            if parent.is_root():
                _ = parent
            segment = task_name.strip() if isinstance(task_name, str) and task_name.strip() else ""
            if not segment:
                segment = self._next_generated_name()
            path = parent.join(segment)
            key = path.as_str()
            if key in self._agents:
                raise ValueError(f"agent path `{key}` already exists")

            agent = ManagedAgent(
                path=path,
                worker=worker,
                nickname=nickname,
                role=role,
            )
            self._start_agent_thread(agent)
            self._agents[key] = agent
            self._submit_input_unlocked(agent, cleaned)
            return agent

    def send_input(
        self,
        target_path: str,
        input_text: str,
        *,
        current_agent_path: AgentPath | str | None = None,
    ) -> ManagedAgent:
        """Queue additional input for a live sub-agent."""
        cleaned = input_text.strip()
        if not cleaned:
            raise ValueError("input_text must not be empty")

        path = resolve_agent_reference(current_agent_path, target_path)
        with self._lock:
            agent = self._agents.get(path.as_str())
            if agent is None:
                raise ValueError(f"live agent path `{path.as_str()}` not found")
            if agent.status == "shutdown":
                raise ValueError(f"agent path `{path.as_str()}` is closed")
            self._submit_input_unlocked(agent, cleaned)
            return agent

    def wait_for_targets(
        self,
        target_paths: list[str],
        *,
        current_agent_path: AgentPath | str | None = None,
        timeout_ms: int = 30000,
    ) -> tuple[dict[str, AgentStatus], bool]:
        """Wait until one-or-more targets reach a final status or timeout."""
        if not target_paths:
            raise ValueError("target_paths must be non-empty")
        if timeout_ms <= 0:
            raise ValueError("timeout_ms must be greater than zero")

        resolved = [resolve_agent_reference(current_agent_path, target) for target in target_paths]

        immediate: dict[str, AgentStatus] = {}
        with self._lock:
            for path in resolved:
                agent = self._agents.get(path.as_str())
                if agent is None:
                    immediate[path.as_str()] = "not_found"
                elif self._is_final(agent.status):
                    immediate[path.as_str()] = agent.status
        if immediate:
            return immediate, False

        deadline = time.monotonic() + (timeout_ms / 1000.0)
        while time.monotonic() < deadline:
            found: dict[str, AgentStatus] = {}
            with self._lock:
                for path in resolved:
                    agent = self._agents.get(path.as_str())
                    if agent is None:
                        found[path.as_str()] = "not_found"
                    elif self._is_final(agent.status):
                        found[path.as_str()] = agent.status
            if found:
                return found, False
            time.sleep(0.05)

        return {}, True

    def close_agent(
        self,
        target_path: str,
        *,
        current_agent_path: AgentPath | str | None = None,
    ) -> tuple[ManagedAgent, AgentStatus]:
        """Close one sub-agent and return its previous status."""
        path = resolve_agent_reference(current_agent_path, target_path)
        with self._lock:
            agent = self._agents.get(path.as_str())
            if agent is None:
                raise ValueError(f"live agent path `{path.as_str()}` not found")
            previous = agent.status
            self._shutdown_agent_unlocked(agent)
            return agent, previous

    def resume_agent(
        self,
        target_path: str,
        *,
        current_agent_path: AgentPath | str | None = None,
        input_override: str | None = None,
    ) -> ManagedAgent:
        """Resume a closed sub-agent and optionally queue input immediately."""
        path = resolve_agent_reference(current_agent_path, target_path)
        with self._lock:
            agent = self._agents.get(path.as_str())
            if agent is None:
                raise ValueError(f"live agent path `{path.as_str()}` not found")

            if agent.status == "shutdown":
                agent._stop_event = Event()
                agent._queue = Queue()
                agent.status = "waiting_input"
                agent.updated_at = datetime.now().isoformat()
                self._start_agent_thread(agent)

            if input_override is not None:
                cleaned = input_override.strip()
                if not cleaned:
                    raise ValueError("input_override must not be empty")
                self._submit_input_unlocked(agent, cleaned)
            return agent

    def shutdown(self) -> None:
        """Shut down all managed sub-agents."""
        with self._lock:
            for agent in self._agents.values():
                self._shutdown_agent_unlocked(agent)

    def _coerce_agent_path(self, value: AgentPath | str) -> AgentPath:
        if isinstance(value, AgentPath):
            return value
        return AgentPath.from_string(value)

    def _next_generated_name(self) -> str:
        self._generated_name_counter += 1
        return f"agent_{self._generated_name_counter}"

    def _submit_input_unlocked(self, agent: ManagedAgent, text: str) -> None:
        agent.status = "pending_init"
        agent.last_input = text
        agent.updated_at = datetime.now().isoformat()
        agent._queue.put(text)

    def _start_agent_thread(self, agent: ManagedAgent) -> None:
        def run_loop() -> None:
            while not agent._stop_event.is_set():
                try:
                    payload = agent._queue.get(timeout=0.1)
                except Empty:
                    continue
                if payload is None:
                    break

                with self._lock:
                    agent.status = "running"
                    agent.updated_at = datetime.now().isoformat()
                try:
                    output = agent.worker(agent, payload)
                    with self._lock:
                        agent.last_output = str(output)
                        agent.last_error = ""
                        agent.task_count += 1
                        agent.status = "completed"
                        agent.updated_at = datetime.now().isoformat()
                except Exception as exc:  # pragma: no cover - defensive
                    with self._lock:
                        agent.last_error = str(exc)
                        agent.status = "failed"
                        agent.updated_at = datetime.now().isoformat()

            with self._lock:
                if agent.status != "failed":
                    agent.status = "shutdown"
                agent.updated_at = datetime.now().isoformat()

        thread = Thread(
            target=run_loop,
            name=f"localagentcli-subagent-{agent.path.name()}",
            daemon=True,
        )
        agent._thread = thread
        thread.start()

    def _shutdown_agent_unlocked(self, agent: ManagedAgent) -> None:
        agent._stop_event.set()
        agent._queue.put(None)
        thread = agent._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        agent.status = "shutdown"
        agent.updated_at = datetime.now().isoformat()

    @staticmethod
    def _is_final(status: str) -> bool:
        return status in _FINAL_STATUSES
