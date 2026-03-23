"""Tests for adaptive agent-mode task triage."""

from __future__ import annotations

from localagentcli.agents.triage import TaskTriageClassifier
from localagentcli.models.backends.base import GenerationResult, ModelMessage


class FakeTriageModel:
    """Minimal model stub for triage classifier tests."""

    def __init__(self, response: str = '{"classification":"single_step_task"}'):
        self._response = response
        self.calls: list[tuple[list, dict]] = []

    def generate(self, messages: list, **kwargs):
        self.calls.append((messages, kwargs))
        return GenerationResult(text=self._response)


class TestTaskTriageClassifier:
    def test_classifies_simple_question_as_direct_answer(self):
        classifier = TaskTriageClassifier(FakeTriageModel())

        result = classifier.classify("What is GitHub?", [])

        assert result.outcome == "direct_answer"

    def test_classifies_single_action_locally(self):
        classifier = TaskTriageClassifier(FakeTriageModel())

        result = classifier.classify("Create an output file", [])

        assert result.outcome == "single_step_task"

    def test_classifies_complex_request_locally(self):
        classifier = TaskTriageClassifier(FakeTriageModel())

        result = classifier.classify("Inspect the repo and then update the failing tests", [])

        assert result.outcome == "multi_step_task"

    def test_falls_back_to_model_for_ambiguous_prompt(self):
        model = FakeTriageModel('{"classification":"multi_step_task","reason":"needs stages"}')
        classifier = TaskTriageClassifier(model)

        result = classifier.classify("Please help with this task", [])

        assert result.outcome == "multi_step_task"
        assert model.calls

    def test_model_fallback_preserves_system_context_when_history_window_is_trimmed(self):
        model = FakeTriageModel('{"classification":"single_step_task"}')
        classifier = TaskTriageClassifier(model)
        context = [
            ModelMessage(role="system", content="workspace-instructions-and-env"),
            *[ModelMessage(role="user", content=f"message-{index}") for index in range(12)],
        ]

        classifier.classify("Please help with this task", context)

        sent_messages = model.calls[0][0]
        assert any(
            message.role == "system" and message.content == "workspace-instructions-and-env"
            for message in sent_messages[1:]
        )
