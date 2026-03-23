"""Tests for AgentLoop message construction."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from localagentcli.agents.events import TaskComplete, ToolCallResult
from localagentcli.agents.loop import AgentLoop
from localagentcli.agents.planner import PlanStep, TaskPlan, TaskPlanner
from localagentcli.models.backends.base import GenerationResult, ModelMessage
from localagentcli.models.model_info import ModelInfo
from localagentcli.safety.approval import ApprovalManager
from localagentcli.safety.boundary import WorkspaceBoundary
from localagentcli.safety.layer import SafetyLayer
from localagentcli.safety.rollback import RollbackManager
from localagentcli.session.state import Session
from localagentcli.session.task_context import AGENT_TASK_RUNTIME_HEADING
from localagentcli.tools import create_default_tool_registry
from localagentcli.tools.base import ToolResult


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


class _LoopScriptedModel:
    def __init__(self, responses: list[GenerationResult], default_max_tokens: int = 4096):
        self._responses = list(responses)
        self.default_max_tokens = default_max_tokens
        self.calls: list[dict[str, object]] = []

    def generate(self, messages: list, **kwargs):
        self.calls.append(dict(kwargs))
        return self._responses.pop(0)

    def stream_generate(self, messages: list, **kwargs):
        raise AssertionError("not used")

    def model_info(self) -> ModelInfo:
        return ModelInfo(
            id="loop-scripted",
            default_max_tokens=self.default_max_tokens,
            capabilities={"tool_use": True, "reasoning": False},
        )


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


def test_build_messages_merges_transcript_system_content_into_primary_system(tmp_path: Path):
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
    transcript = [
        ModelMessage(role="system", content="workspace-instructions-and-env"),
        ModelMessage(role="user", content="prior user turn"),
    ]

    messages = loop._build_messages("Do work", plan, step, transcript, [], None)

    assert messages[0].role == "system"
    assert "workspace-instructions-and-env" in messages[0].content
    assert "Session instructions and environment context:" in messages[0].content
    assert all(message.role != "system" for message in messages[1:])


def test_build_messages_falls_back_to_session_instructions_when_transcript_has_no_system(
    tmp_path: Path,
):
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
        metadata={"workspace_instruction": "Follow AGENTS.md exactly."},
    )
    session.pinned_instructions.append("Keep edits minimal.")

    messages = loop._build_messages(
        "Do work",
        plan,
        step,
        [ModelMessage(role="user", content="prior user turn")],
        [],
        session,
    )

    assert "Follow AGENTS.md exactly." in messages[0].content
    assert "Keep edits minimal." in messages[0].content
    assert "<environment_context>" in messages[0].content


def test_build_messages_uses_enriched_step_prompt_sections(tmp_path: Path):
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

    content = messages[0].content
    assert "Execution rules:" in content
    assert "Output contract:" in content
    assert "Task objective:" in content
    assert "Plan status:" in content
    assert "Current step focus:" in content


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


def test_tool_payload_uses_model_aware_truncation(tmp_path: Path):
    model = _LoopRunModel(default_max_tokens=256)
    registry = create_default_tool_registry(tmp_path)
    approval = ApprovalManager()
    safety = SafetyLayer(
        approval,
        WorkspaceBoundary(tmp_path.resolve()),
        RollbackManager("session-1", tmp_path / ".cache"),
    )
    loop = AgentLoop(model, registry, TaskPlanner(model), safety)

    payload = json.loads(
        loop._tool_payload(
            "file_read",
            ToolResult.success(summary="Read file", output="x" * 8000),
        )
    )

    assert payload["output_truncated"] is True
    assert payload["output_original_chars"] == 8000
    assert payload["output_retained_chars"] < payload["output_original_chars"]
    assert "chars truncated" in payload["output"]


def test_loop_adapts_tool_definitions_for_small_models(tmp_path: Path):
    model = _LoopRunModel(default_max_tokens=1024)
    registry = create_default_tool_registry(tmp_path)
    approval = ApprovalManager()
    safety = SafetyLayer(
        approval,
        WorkspaceBoundary(tmp_path.resolve()),
        RollbackManager("session-1", tmp_path / ".cache"),
    )
    loop = AgentLoop(model, registry, TaskPlanner(model), safety)
    plan = TaskPlan(task="Inspect files", steps=[PlanStep(index=1, description="Inspect files")])

    events = list(loop.run("Inspect files", [], plan=plan))

    assert isinstance(events[-1], TaskComplete)
    tool_names = [definition["name"] for definition in model.calls[0]["tools"]]
    assert "file_read" in tool_names
    assert "patch_apply" not in tool_names


def test_unified_turn_loop_allows_many_tool_rounds_before_final_output(tmp_path: Path):
    (tmp_path / "notes.txt").write_text("alpha\n", encoding="utf-8")
    responses = [
        GenerationResult(
            text="",
            tool_calls=[
                {
                    "id": f"call_{index}",
                    "type": "function",
                    "function": {
                        "name": "file_read",
                        "arguments": '{"path":"notes.txt"}',
                    },
                }
            ],
        )
        for index in range(7)
    ]
    responses.append(GenerationResult(text="All checks complete."))

    model = _LoopScriptedModel(responses)
    registry = create_default_tool_registry(tmp_path)
    approval = ApprovalManager()
    safety = SafetyLayer(
        approval,
        WorkspaceBoundary(tmp_path.resolve()),
        RollbackManager("session-1", tmp_path / ".cache"),
    )
    loop = AgentLoop(
        model,
        registry,
        TaskPlanner(model),
        safety,
        max_consecutive_errors=2,
        max_step_rounds=16,
        unified_turn_loop=True,
    )
    plan = TaskPlan(task="Inspect", steps=[PlanStep(index=1, description="Inspect")])

    events = list(loop.run("Inspect", [], plan=plan))

    assert isinstance(events[-1], TaskComplete)
    assert sum(isinstance(event, ToolCallResult) for event in events) == 7
