"""Task planning primitives for agent mode."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from localagentcli.models.abstraction import ModelAbstractionLayer
from localagentcli.models.backends.base import GenerationResult, ModelMessage

_PLANNING_PROMPT = (
    "You are planning work for an autonomous software engineering agent. "
    "Return strict JSON with the shape "
    '{"steps":[{"description":"..."}]}. '
    "Use the minimum number of concise, concrete steps needed. "
    "Do not include markdown fences or prose."
)

_REPLAN_PROMPT = (
    "You are updating an existing task plan for an autonomous engineering agent. "
    "Return strict JSON with the shape "
    '{"steps":[{"description":"..."}]}. '
    "Keep completed work out of the new steps and focus only on remaining work."
)


@dataclass
class PlanStep:
    """A single step in a task plan."""

    index: int
    description: str
    status: str = "pending"
    tool_calls: list[dict] | None = None
    result: str | None = None

    def to_dict(self) -> dict:
        """Serialize the step for session storage."""
        return {
            "index": self.index,
            "description": self.description,
            "status": self.status,
            "tool_calls": self.tool_calls,
            "result": self.result,
        }

    @classmethod
    def from_dict(cls, data: dict) -> PlanStep:
        """Deserialize a step from persisted session state."""
        return cls(
            index=int(data.get("index", 0)),
            description=str(data.get("description", "")),
            status=str(data.get("status", "pending")),
            tool_calls=data.get("tool_calls"),
            result=data.get("result"),
        )


@dataclass
class TaskPlan:
    """The task plan displayed and updated during agent execution."""

    task: str
    steps: list[PlanStep] = field(default_factory=list)
    status: str = "planning"

    def next_step(self) -> PlanStep | None:
        """Return the next pending step, if any."""
        for step in self.steps:
            if step.status == "pending":
                return step
        return None

    def get_step(self, index: int) -> PlanStep | None:
        """Find a step by its 1-based index."""
        for step in self.steps:
            if step.index == index:
                return step
        return None

    def update_step(self, index: int, status: str, result: str | None = None) -> None:
        """Update a step's status and optional result."""
        step = self.get_step(index)
        if step is None:
            raise KeyError(f"Plan step {index} not found")
        step.status = status
        if result is not None:
            step.result = result

    def add_step(self, description: str, after_index: int | None = None) -> None:
        """Insert a new pending step and renumber the plan."""
        insert_at = len(self.steps)
        if after_index is not None:
            for offset, step in enumerate(self.steps):
                if step.index == after_index:
                    insert_at = offset + 1
                    break
        self.steps.insert(insert_at, PlanStep(index=0, description=description))
        self._renumber()

    def remove_step(self, index: int) -> None:
        """Remove a step by index and renumber the plan."""
        self.steps = [step for step in self.steps if step.index != index]
        self._renumber()

    def to_dict(self) -> dict:
        """Serialize the plan for session persistence."""
        return {
            "task": self.task,
            "steps": [step.to_dict() for step in self.steps],
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: dict) -> TaskPlan:
        """Deserialize a task plan from session state."""
        plan = cls(
            task=str(data.get("task", "")),
            steps=[PlanStep.from_dict(step) for step in data.get("steps", [])],
            status=str(data.get("status", "planning")),
        )
        plan._renumber()
        return plan

    def _renumber(self) -> None:
        for index, step in enumerate(self.steps, start=1):
            step.index = index


class TaskPlanner:
    """Generate and revise plans using the active model."""

    def __init__(self, model: ModelAbstractionLayer):
        self._model = model

    def create_plan(
        self,
        task: str,
        context: list[ModelMessage],
        generation_options: dict[str, object] | None = None,
    ) -> TaskPlan:
        """Ask the model for an initial task plan."""
        options: dict[str, object] = {"temperature": 0.1, "max_tokens": 600}
        if generation_options:
            options.update(generation_options)
        result = self._model.generate(
            [
                ModelMessage(role="system", content=_PLANNING_PROMPT),
                *context[-8:],
                ModelMessage(role="user", content=f"Task: {task}"),
            ],
            **options,
        )
        return self._parse_plan_response(task, result)

    def revise_plan(
        self,
        task: str,
        plan: TaskPlan,
        observation: str,
        generation_options: dict[str, object] | None = None,
    ) -> TaskPlan:
        """Ask the model for a revised plan after a failed or denied step."""
        options: dict[str, object] = {"temperature": 0.1, "max_tokens": 600}
        if generation_options:
            options.update(generation_options)
        result = self._model.generate(
            [
                ModelMessage(role="system", content=_REPLAN_PROMPT),
                ModelMessage(
                    role="user",
                    content=(
                        f"Task: {task}\n\n"
                        f"Current plan:\n{self._format_plan(plan)}\n\n"
                        f"Observation:\n{observation}"
                    ),
                ),
            ],
            **options,
        )
        revised = self._parse_plan_response(task, result)
        revised.status = plan.status
        return revised

    def _parse_plan_response(self, task: str, result: GenerationResult) -> TaskPlan:
        payload = self._extract_json(result.text)
        if payload is None:
            return self._fallback_plan(task)

        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return self._fallback_plan(task)

        raw_steps = data.get("steps", [])
        if not isinstance(raw_steps, list):
            return self._fallback_plan(task)

        steps = []
        for index, step_data in enumerate(raw_steps, start=1):
            if not isinstance(step_data, dict):
                continue
            description = str(step_data.get("description", "")).strip()
            if not description:
                continue
            steps.append(PlanStep(index=index, description=description))

        if not steps:
            return self._fallback_plan(task)
        return TaskPlan(task=task, steps=steps, status="planning")

    def _fallback_plan(self, task: str) -> TaskPlan:
        heuristic_steps = [
            "Inspect the relevant workspace files and current project state.",
            "Implement the required changes for the task.",
            "Run the most relevant verification and summarize the outcome.",
        ]
        if re.search(r"\b(test|fix|debug)\b", task, flags=re.IGNORECASE):
            heuristic_steps[1] = (
                "Investigate the failure, apply the required fix, and update tests."
            )

        return TaskPlan(
            task=task,
            steps=[
                PlanStep(index=index, description=description)
                for index, description in enumerate(heuristic_steps, start=1)
            ],
            status="planning",
        )

    def _extract_json(self, text: str) -> str | None:
        text = text.strip()
        if not text:
            return None
        if text.startswith("{") and text.endswith("}"):
            return text
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if match:
            return match.group(0)
        return None

    def _format_plan(self, plan: TaskPlan) -> str:
        return "\n".join(f"{step.index}. [{step.status}] {step.description}" for step in plan.steps)
