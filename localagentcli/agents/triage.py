"""Task triage for adaptive agent-mode execution."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Literal

from localagentcli.models.abstraction import ModelAbstractionLayer
from localagentcli.models.backends.base import ModelMessage

TaskTriageOutcome = Literal["direct_answer", "single_step_task", "multi_step_task"]

_TRIAGE_PROMPT = (
    "Classify the user's latest request for an autonomous CLI agent. "
    "Return strict JSON with the shape "
    '{"classification":"direct_answer|single_step_task|multi_step_task","reason":"..."} . '
    "Use direct_answer for simple knowledge questions or explanations that need no tools or plan. "
    "Use single_step_task for one concrete action. "
    "Use multi_step_task for multi-file, multi-tool, or clearly staged work."
)

_DIRECT_PREFIXES = (
    "what is",
    "who is",
    "what are",
    "who are",
    "explain",
    "describe",
    "define",
    "summarize",
    "tell me",
    "how does",
    "why does",
    "what model are you",
)
_DIRECT_WORDS = {"what", "who", "when", "where", "why", "how"}
_MULTI_STEP_HINTS = (
    "implement",
    "refactor",
    "debug",
    "fix",
    "review",
    "audit",
    "analyze",
    "investigate",
)
_SINGLE_STEP_HINTS = (
    "create",
    "update",
    "edit",
    "modify",
    "write",
    "add",
    "remove",
    "replace",
    "rename",
    "inspect",
    "read",
    "show",
    "list",
    "run tests",
    "run the tests",
)


@dataclass(frozen=True)
class TaskTriage:
    """Classification result for agent-mode plain text input."""

    outcome: TaskTriageOutcome
    reason: str = ""


class TaskTriageClassifier:
    """Classify prompts into direct-answer, single-step, or multi-step execution."""

    def __init__(self, model: ModelAbstractionLayer):
        self._model = model

    def classify(
        self,
        task: str,
        context: list[ModelMessage],
        generation_options: dict[str, object] | None = None,
    ) -> TaskTriage:
        """Classify a task using heuristics with a low-cost model fallback."""
        heuristic = self._classify_heuristically(task)
        if heuristic is not None:
            return heuristic
        return self._classify_with_model(task, context, generation_options or {})

    def _classify_heuristically(self, task: str) -> TaskTriage | None:
        normalized = " ".join(task.strip().split())
        lowered = normalized.lower()
        word_count = len(lowered.split())

        if not lowered:
            return TaskTriage("direct_answer", "empty input")

        if re.fullmatch(r"\s*\d+\s*([+\-*/]\s*\d+\s*)+\??", lowered):
            return TaskTriage("direct_answer", "simple arithmetic")

        if lowered.endswith("?") or lowered.startswith(_DIRECT_PREFIXES):
            if not any(hint in lowered for hint in (*_MULTI_STEP_HINTS, *_SINGLE_STEP_HINTS)):
                return TaskTriage("direct_answer", "question-like prompt")

        if word_count <= 8 and lowered.split()[0] in _DIRECT_WORDS:
            return TaskTriage("direct_answer", "short factual question")

        if lowered.startswith(("explain ", "describe ", "summarize ", "define ")):
            return TaskTriage("direct_answer", "explanatory prompt")

        if (
            "\n" in task
            or len(normalized) > 160
            or re.search(r"\b(and|then|after|before|also)\b", lowered)
            or any(hint in lowered for hint in _MULTI_STEP_HINTS)
            or sum(1 for hint in _SINGLE_STEP_HINTS if hint in lowered) >= 2
        ):
            return TaskTriage("multi_step_task", "complex or staged request")

        if any(hint in lowered for hint in _SINGLE_STEP_HINTS):
            return TaskTriage("single_step_task", "single concrete action")

        return None

    def _classify_with_model(
        self,
        task: str,
        context: list[ModelMessage],
        generation_options: dict[str, object],
    ) -> TaskTriage:
        options: dict[str, object] = {
            "temperature": 0.0,
        }
        options.update(generation_options)
        result = self._model.generate(
            [
                ModelMessage(role="system", content=_TRIAGE_PROMPT),
                *context[-6:],
                ModelMessage(role="user", content=task),
            ],
            **options,
        )
        payload = self._extract_json(result.text)
        if payload is None:
            return TaskTriage("single_step_task", "triage fallback")
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            return TaskTriage("single_step_task", "triage fallback")
        classification_raw = str(parsed.get("classification", "single_step_task")).strip()
        if classification_raw == "direct_answer":
            classification: TaskTriageOutcome = "direct_answer"
        elif classification_raw == "multi_step_task":
            classification = "multi_step_task"
        else:
            classification = "single_step_task"
        return TaskTriage(classification, str(parsed.get("reason", "")).strip())

    async def aclassify(
        self,
        task: str,
        context: list[ModelMessage],
        generation_options: dict[str, object] | None = None,
    ) -> TaskTriage:
        """Async triage using heuristics with a low-cost model fallback."""
        heuristic = self._classify_heuristically(task)
        if heuristic is not None:
            return heuristic
        return await self._aclassify_with_model(task, context, generation_options or {})

    async def _aclassify_with_model(
        self,
        task: str,
        context: list[ModelMessage],
        generation_options: dict[str, object],
    ) -> TaskTriage:
        options: dict[str, object] = {
            "temperature": 0.0,
        }
        options.update(generation_options)
        result = await self._model.agenerate(
            [
                ModelMessage(role="system", content=_TRIAGE_PROMPT),
                *context[-6:],
                ModelMessage(role="user", content=task),
            ],
            **options,
        )
        payload = self._extract_json(result.text)
        if payload is None:
            return TaskTriage("single_step_task", "triage fallback")
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            return TaskTriage("single_step_task", "triage fallback")
        classification_raw = str(parsed.get("classification", "single_step_task")).strip()
        if classification_raw == "direct_answer":
            classification: TaskTriageOutcome = "direct_answer"
        elif classification_raw == "multi_step_task":
            classification = "multi_step_task"
        else:
            classification = "single_step_task"
        return TaskTriage(classification, str(parsed.get("reason", "")).strip())

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
