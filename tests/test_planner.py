"""Tests for agent planning primitives."""

from __future__ import annotations

from localagentcli.agents.planner import PlanStep, TaskPlan, TaskPlanner
from localagentcli.models.backends.base import GenerationResult, ModelMessage


class FakePlannerModel:
    """Minimal model stub for planner tests."""

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls: list[tuple[list, dict]] = []

    def generate(self, messages: list, **kwargs):
        self.calls.append((messages, kwargs))
        text = self._responses.pop(0)
        return GenerationResult(text=text)


class TestTaskPlan:
    def test_next_step_and_updates(self):
        plan = TaskPlan(
            task="demo",
            steps=[
                PlanStep(index=1, description="First"),
                PlanStep(index=2, description="Second"),
            ],
            status="planning",
        )

        assert plan.next_step().index == 1

        plan.update_step(1, "completed", "done")
        assert plan.get_step(1).result == "done"
        assert plan.next_step().index == 2

    def test_add_and_remove_step_renumbers(self):
        plan = TaskPlan(task="demo", steps=[PlanStep(index=1, description="First")])

        plan.add_step("Second", after_index=1)
        plan.add_step("Inserted", after_index=1)
        plan.remove_step(2)

        assert [step.index for step in plan.steps] == [1, 2]
        assert [step.description for step in plan.steps] == ["First", "Second"]

    def test_roundtrip(self):
        original = TaskPlan(
            task="demo",
            steps=[PlanStep(index=1, description="First", status="completed", result="done")],
            status="completed",
        )

        restored = TaskPlan.from_dict(original.to_dict())

        assert restored.task == "demo"
        assert restored.steps[0].description == "First"
        assert restored.steps[0].result == "done"
        assert restored.status == "completed"


class TestTaskPlanner:
    def test_create_plan_parses_model_json(self):
        planner = TaskPlanner(
            FakePlannerModel(['{"steps":[{"description":"Inspect"},{"description":"Edit"}]}'])
        )

        plan = planner.create_plan("Refactor auth", [])

        assert [step.description for step in plan.steps] == ["Inspect", "Edit"]
        assert plan.status == "planning"

    def test_create_plan_falls_back_when_model_output_is_invalid(self):
        planner = TaskPlanner(FakePlannerModel(["not json"]))

        plan = planner.create_plan("Fix failing tests", [])

        assert len(plan.steps) == 3
        assert "Investigate" in plan.steps[1].description

    def test_create_plan_preserves_system_context_when_windowing_history(self):
        model = FakePlannerModel(['{"steps":[{"description":"Inspect"}]}'])
        planner = TaskPlanner(model)
        context = [
            ModelMessage(role="system", content="workspace-instructions-and-env"),
            *[ModelMessage(role="user", content=f"message-{index}") for index in range(12)],
        ]

        planner.create_plan("Refactor auth", context)

        sent_messages = model.calls[0][0]
        assert any(
            message.role == "system" and message.content == "workspace-instructions-and-env"
            for message in sent_messages[1:]
        )
