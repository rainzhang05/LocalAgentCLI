"""Tests for AgentLoop message construction."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from localagentcli.agents.events import TaskComplete
from localagentcli.agents.loop import AgentLoop
from localagentcli.agents.planner import PlanStep, TaskPlan, TaskPlanner
from localagentcli.models.backends.base import GenerationResult
from localagentcli.models.model_info import ModelInfo
from localagentcli.safety.approval import ApprovalManager
from localagentcli.safety.boundary import WorkspaceBoundary
from localagentcli.safety.layer import SafetyLayer
from localagentcli.safety.rollback import RollbackManager
from localagentcli.session.state import Session
from localagentcli.session.task_context import AGENT_TASK_RUNTIME_HEADING
from localagentcli.tools import create_default_tool_registry


class _LoopModel:
    def generate(self, messages: list, **kwargs):
        return GenerationResult(text="ok")

    def stream_generate(self, messages: list, **kwargs):
        raise AssertionError("not used")

    def model_info(self) -> ModelInfo:
        return ModelInfo(id="loop-test", default_max_tokens=2048)


class _LoopRunModel:
    def __init__(self, default_max_tokens: int):
        self.default_max_tokens = default_max_tokens
        self.calls: list[dict[str, object]] = []

    def generate(self, messages: list, **kwargs):
        self.calls.append(dict(kwargs))
        return GenerationResult(text="step complete")

    def stream_generate(self, messages: list, **kwargs):
        raise AssertionError("not used")

    def model_info(self) -> ModelInfo:
        return ModelInfo(id="loop-run", default_max_tokens=self.default_max_tokens)


def _session_agent(tmp_path: Path, metadata: dict) -> Session:
    now = datetime(2025, 1, 15, 10, 0, 0)
    return Session(
        id="session-1",
        name=None,
        mode="agent",
        model="m",
        provider="p",
        workspace=str(tmp_path),
        created_at=now,
        updated_at=now,
        metadata=metadata,
    )


def test_build_messages_without_session_has_no_runtime_block(tmp_path: Path):
    registry = create_default_tool_registry(tmp_path)
    approval = ApprovalManager()
    safety = SafetyLayer(
        approval,
        WorkspaceBoundary(tmp_path.resolve()),
        RollbackManager("session-1", tmp_path / ".cache"),
    )
    loop = AgentLoop(_LoopModel(), registry, TaskPlanner(_LoopModel()), safety)
    plan = TaskPlan(task="Do work", steps=[PlanStep(index=1, description="Step one")])
    step = plan.steps[0]

    messages = loop._build_messages("Do work", plan, step, [], [], None)

    assert messages[0].role == "system"
    assert AGENT_TASK_RUNTIME_HEADING not in messages[0].content


def test_build_messages_includes_runtime_block_when_session_active(tmp_path: Path):
    registry = create_default_tool_registry(tmp_path)
    approval = ApprovalManager()
    safety = SafetyLayer(
        approval,
        WorkspaceBoundary(tmp_path.resolve()),
        RollbackManager("session-1", tmp_path / ".cache"),
    )
    loop = AgentLoop(_LoopModel(), registry, TaskPlanner(_LoopModel()), safety)
    plan = TaskPlan(task="Do work", steps=[PlanStep(index=1, description="Step one")])
    step = plan.steps[0]
    session = _session_agent(
        tmp_path,
        metadata={
            "agent_task_state": {
                "active": True,
                "phase": "executing",
                "pending_tool": "",
                "summary": "Running step.",
            }
        },
    )

    messages = loop._build_messages("Do work", plan, step, [], [], session)

    assert AGENT_TASK_RUNTIME_HEADING in messages[0].content
    assert "phase: executing" in messages[0].content
    assert "summary: Running step." in messages[0].content


def test_run_uses_model_default_max_tokens_when_generation_options_not_provided(
    tmp_path: Path,
):
    model = _LoopRunModel(default_max_tokens=777)
    registry = create_default_tool_registry(tmp_path)
    approval = ApprovalManager()
    safety = SafetyLayer(
        approval,
        WorkspaceBoundary(tmp_path.resolve()),
        RollbackManager("session-1", tmp_path / ".cache"),
    )
    loop = AgentLoop(model, registry, TaskPlanner(model), safety)
    plan = TaskPlan(task="Do work", steps=[PlanStep(index=1, description="Step one")])

    events = list(loop.run("Do work", [], plan=plan))

    assert isinstance(events[-1], TaskComplete)
    assert model.calls
    assert model.calls[0]["max_tokens"] == 777
    assert model.calls[0]["temperature"] == 0.2
