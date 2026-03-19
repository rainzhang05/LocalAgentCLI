"""Event types emitted by the agent loop."""

from __future__ import annotations

from dataclasses import dataclass, field

from localagentcli.agents.planner import PlanStep, TaskPlan
from localagentcli.tools.base import ToolResult


@dataclass
class AgentEvent:
    """Base type for shell-rendered agent events."""

    type: str = field(init=False, default="agent_event")


@dataclass
class PlanGenerated(AgentEvent):
    """Initial plan created for a new task."""

    plan: TaskPlan
    type: str = field(init=False, default="plan_generated")


@dataclass
class StepStarted(AgentEvent):
    """A plan step is about to execute."""

    step: PlanStep
    type: str = field(init=False, default="step_started")


@dataclass
class ToolCallRequested(AgentEvent):
    """A tool call was proposed by the model."""

    tool_name: str
    arguments: dict
    requires_approval: bool
    risk_level: str = "normal"
    warnings: list[str] = field(default_factory=list)
    type: str = field(init=False, default="tool_call_requested")


@dataclass
class ToolCallResult(AgentEvent):
    """A tool call finished."""

    tool_name: str
    result: ToolResult
    type: str = field(init=False, default="tool_call_result")


@dataclass
class ReasoningOutput(AgentEvent):
    """Reasoning emitted by the model during task execution."""

    text: str
    type: str = field(init=False, default="reasoning_output")


@dataclass
class PlanUpdated(AgentEvent):
    """The plan or step status changed."""

    plan: TaskPlan
    changes: str
    type: str = field(init=False, default="plan_updated")


@dataclass
class TaskComplete(AgentEvent):
    """The task finished successfully."""

    summary: str
    plan: TaskPlan
    type: str = field(init=False, default="task_complete")


@dataclass
class TaskFailed(AgentEvent):
    """The task failed or was stopped."""

    reason: str
    plan: TaskPlan
    type: str = field(init=False, default="task_failed")
