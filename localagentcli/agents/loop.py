"""Iterative agent loop for task execution with tools."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Generator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from localagentcli.agents.events import (
    AgentEvent,
    GuardianReviewCompleted,
    GuardianReviewStarted,
    PhaseChanged,
    PlanGenerated,
    PlanUpdated,
    ReasoningOutput,
    StepStarted,
    TaskComplete,
    TaskFailed,
    TaskStopped,
    TaskTimedOut,
    ToolCallRequested,
    ToolCallResult,
)
from localagentcli.agents.planner import PlanStep, TaskPlan, TaskPlanner
from localagentcli.agents.profiles import build_generation_profile
from localagentcli.agents.recovery import (
    FailureClass,
    FailureContext,
    classify_model_failure,
    classify_tool_failure,
    failure_class_hint,
    failure_class_label,
    should_replan_after_failure,
    update_failure_counters,
)
from localagentcli.agents.truncation import truncate_for_model_output
from localagentcli.guardian import (
    GuardianReviewRequest,
    GuardianReviewResult,
    areview_with_guardian,
    review_with_guardian,
)
from localagentcli.models.abstraction import ModelAbstractionLayer
from localagentcli.models.backends.base import GenerationResult, ModelMessage
from localagentcli.models.model_info import ModelInfo
from localagentcli.safety.layer import SafetyLayer
from localagentcli.session.context_diff import ContextDiffTracker, render_context_diff_for_prompt
from localagentcli.session.environment_context import get_environment_context_xml
from localagentcli.session.instructions import build_system_instructions
from localagentcli.session.state import Session
from localagentcli.session.task_context import (
    AGENT_TASK_RUNTIME_HEADING,
    build_turn_context_snapshot,
    format_agent_task_runtime_section,
)
from localagentcli.session.usage import update_session_usage_budget
from localagentcli.tools.base import Tool, ToolResult
from localagentcli.tools.registry import ToolRegistry
from localagentcli.tools.router import ToolRouter

# Bounded fan-out for read-only parallel batches (I/O-bound tools still benefit on 1-CPU hosts).
_PARALLEL_READ_ONLY_MAX_WORKERS = 16


@dataclass
class _AsyncStepDone:
    """Internal marker emitted at the end of _arun_step_async."""

    summary: str | None
    new_messages: list[ModelMessage]
    errors: int
    failure_kind: str = ""
    last_model_error: str = ""
    failure_context: FailureContext | None = None


@dataclass
class _GuardianContext:
    """Execution context provided to guardian review requests."""

    task: str
    step_index: int
    step_description: str
    transcript: list[ModelMessage]
    conversation: list[ModelMessage]


_STEP_PROMPT = """You are LocalAgentCLI operating in agent mode.

Execution rules:
- Work on the current step only; do not skip ahead.
- Use available tools whenever inspection, edits, or verification is needed.
- Never guess file contents, command output, or test outcomes.
- Prefer minimal, reversible, repository-consistent changes.
- If a tool call is denied, fails, or times out, adapt and continue when possible.

Output contract:
- If more execution is needed, continue by using tools.
- When the current step is complete, return a concise plain-text summary of
    the result (no markdown fences, no JSON object wrapper).
"""


class AgentLoop:
    """Drive plan execution until the task completes, fails, or is stopped."""

    def __init__(
        self,
        model: ModelAbstractionLayer,
        tools: ToolRegistry | ToolRouter,
        planner: TaskPlanner,
        safety: SafetyLayer,
        max_consecutive_errors: int = 5,
        max_step_rounds: int = 24,
        unified_turn_loop: bool = True,
        approvals_reviewer: str = "user",
    ):
        self._model = model
        self._tools = tools
        self._planner = planner
        self._safety = safety
        self._max_consecutive_errors = max_consecutive_errors
        self._max_step_rounds = max(1, max_step_rounds)
        self._unified_turn_loop = unified_turn_loop
        self._approvals_reviewer = approvals_reviewer
        self._stop_requested = False
        self._approval_wait: asyncio.Future[bool] | None = None
        self._async_tool_batch: tuple[list[ModelMessage], FailureContext | None] | None = None
        self._sync_tool_failure_context: FailureContext | None = None
        self._guardian_context: _GuardianContext | None = None

    def stop(self) -> None:
        """Request that the loop stop at the next safe point."""
        self._stop_requested = True
        if self._approval_wait is not None and not self._approval_wait.done():
            self._approval_wait.cancel()

    def set_tools(self, tools: ToolRegistry | ToolRouter) -> None:
        """Replace the active tool routing surface between tasks."""
        self._tools = tools

    def supply_tool_approval(self, approved: bool) -> None:
        """Resume async loop after an approval decision (used by AgentController)."""
        fut = self._approval_wait
        if fut is not None and not fut.done():
            fut.set_result(approved)

    async def _await_tool_approval(self) -> bool:
        loop = asyncio.get_running_loop()
        self._approval_wait = loop.create_future()
        try:
            return await self._approval_wait
        finally:
            self._approval_wait = None

    def run(
        self,
        task: str,
        context: list[ModelMessage],
        plan: TaskPlan | None = None,
        generation_options: dict[str, object] | None = None,
        planning_options: dict[str, object] | None = None,
        inactivity_timeout: int | None = None,
        session: Session | None = None,
    ) -> Generator[AgentEvent, bool, None]:
        """Execute the full understand/plan/execute/observe loop."""
        options = build_generation_profile(
            phase="step",
            base_config=generation_options,
            model_info=self._resolve_model_info(),
        )

        if plan is None:
            yield PhaseChanged(phase="planning", summary="Prepared adaptive execution plan.")
            plan = self._bootstrap_plan(task)
        else:
            yield PhaseChanged(phase="planning", summary="Prepared execution plan.")
        plan.status = "executing"
        transcript = list(context)
        last_activity = time.monotonic()
        yield PlanGenerated(plan)
        yield PhaseChanged(phase="executing", summary="Executing plan.")

        while not self._stop_requested:
            if inactivity_timeout and (time.monotonic() - last_activity) > inactivity_timeout:
                plan.status = "timed_out"
                yield TaskTimedOut(
                    reason="Agent task timed out due to inactivity.",
                    plan=plan,
                )
                return
            step = plan.next_step()
            if step is None:
                plan.status = "completed"
                summary = self._summarize_plan(plan)
                yield TaskComplete(summary=summary, plan=plan)
                return

            step.status = "in_progress"
            yield StepStarted(step=step)

            (
                step_summary,
                new_messages,
                errors,
                failure_kind,
                last_model_error,
                failure_context,
            ) = yield from self._run_step(
                task,
                plan,
                step,
                transcript,
                options,
                session,
            )
            transcript.extend(new_messages)
            last_activity = time.monotonic()

            if self._stop_requested:
                break

            if step_summary is None:
                if (
                    failure_kind in {"tool_retry_budget_exhausted", "model_retry_budget_exhausted"}
                    and failure_context is not None
                    and should_replan_after_failure(failure_context.failure_class)
                ):
                    replan_reason = self._build_replan_reason(step, failure_context)
                    yield PhaseChanged(
                        phase="replanning",
                        summary=(
                            "Replanning after "
                            f"{failure_class_label(failure_context.failure_class)} "
                            f"failures in step {step.index}."
                        ),
                        step_index=step.index,
                        step_description=step.description,
                    )
                    revised = self._planner.revise_plan(
                        task,
                        plan,
                        replan_reason,
                        generation_options=planning_options,
                    )
                    self._preserve_completed_steps(plan, revised)
                    plan = revised
                    plan.status = "executing"
                    yield PlanUpdated(
                        plan=plan,
                        changes=(
                            f"Replanned after {failure_class_label(failure_context.failure_class)} "
                            f"failure in step {step.index}."
                        ),
                    )
                    yield PhaseChanged(phase="executing", summary="Continuing with revised plan.")
                    continue

                if failure_kind in {"model_error_threshold", "model_retry_budget_exhausted"}:
                    detail = (
                        f"Model errors prevented step {step.index} from completing "
                        f"after {errors} attempt(s)."
                    )
                    if last_model_error:
                        detail = f"{detail} Last model error: {last_model_error}"
                    plan.update_step(step.index, "failed", "Repeated model errors.")
                    plan.status = "failed"
                    yield PhaseChanged(
                        phase="failed",
                        summary=detail,
                        step_index=step.index,
                        step_description=step.description,
                    )
                    yield PlanUpdated(
                        plan=plan,
                        changes=(
                            f"Step {step.index} failed after repeated model errors "
                            "reached the retry threshold."
                        ),
                    )
                    reason = (
                        f"Failed while executing step {step.index}: {step.description} "
                        "(model error threshold reached)."
                    )
                    if last_model_error:
                        reason = f"{reason} Last model error: {last_model_error}"
                    yield TaskFailed(
                        reason=reason,
                        failure_type=failure_context.failure_class.value
                        if failure_context is not None
                        else FailureClass.MODEL_TRANSIENT.value,
                        plan=plan,
                    )
                    return

                if (
                    failure_kind in {"tool_error_threshold", "tool_retry_budget_exhausted"}
                    and self._unified_turn_loop
                ):
                    failure_label = (
                        failure_class_label(failure_context.failure_class)
                        if failure_context is not None
                        else "tool"
                    )
                    plan.update_step(step.index, "failed", "Repeated tool failures.")
                    plan.status = "failed"
                    yield PhaseChanged(
                        phase="failed",
                        summary=(f"Step {step.index} reached the {failure_label} retry threshold."),
                        step_index=step.index,
                        step_description=step.description,
                    )
                    yield PlanUpdated(
                        plan=plan,
                        changes=(
                            f"Step {step.index} failed after repeated tool failures "
                            "reached the retry threshold."
                        ),
                    )
                    yield TaskFailed(
                        reason=(
                            f"Failed while executing step {step.index}: {step.description} "
                            f"({failure_label} failure threshold reached)."
                        ),
                        failure_type=failure_context.failure_class.value
                        if failure_context is not None
                        else None,
                        plan=plan,
                    )
                    return

                if self._unified_turn_loop:
                    plan.update_step(
                        step.index, "failed", "Unified turn loop could not complete step."
                    )
                    plan.status = "failed"
                    yield PhaseChanged(
                        phase="failed",
                        summary=(
                            f"Unified turn loop could not complete step {step.index} "
                            f"within {self._max_step_rounds} rounds."
                        ),
                        step_index=step.index,
                        step_description=step.description,
                    )
                    yield PlanUpdated(
                        plan=plan,
                        changes=(
                            f"Step {step.index} failed after unified turn-loop "
                            "budget was exhausted."
                        ),
                    )
                    yield TaskFailed(
                        reason=(
                            f"Failed while executing step {step.index}: {step.description} "
                            f"(unified turn-loop budget exhausted)."
                        ),
                        failure_type=FailureClass.UNKNOWN.value,
                        plan=plan,
                    )
                    return

                if errors >= self._max_consecutive_errors:
                    replan_reason = (
                        self._build_replan_reason(step, failure_context)
                        if failure_context is not None
                        else f"Step {step.index} encountered repeated tool failures."
                    )
                    yield PhaseChanged(
                        phase="replanning",
                        summary=f"Replanning after repeated failures in step {step.index}.",
                        step_index=step.index,
                        step_description=step.description,
                    )
                    revised = self._planner.revise_plan(
                        task,
                        plan,
                        replan_reason,
                        generation_options=planning_options,
                    )
                    self._preserve_completed_steps(plan, revised)
                    plan = revised
                    plan.status = "executing"
                    yield PlanUpdated(
                        plan=plan,
                        changes=f"Replanned after repeated failures in step {step.index}.",
                    )
                    yield PhaseChanged(phase="executing", summary="Continuing with revised plan.")
                    continue

                plan.update_step(step.index, "failed", "Step did not complete successfully.")
                plan.status = "failed"
                yield PhaseChanged(
                    phase="failed",
                    summary=f"Failed while executing step {step.index}.",
                    step_index=step.index,
                    step_description=step.description,
                )
                yield PlanUpdated(
                    plan=plan,
                    changes=f"Step {step.index} failed: {step.description}",
                )
                yield TaskFailed(
                    reason=f"Failed while executing step {step.index}: {step.description}",
                    failure_type=failure_context.failure_class.value
                    if failure_context is not None
                    else None,
                    plan=plan,
                )
                return

            plan.update_step(step.index, "completed", step_summary)
            yield PlanUpdated(
                plan=plan,
                changes=f"Completed step {step.index}: {step.description}",
            )

        if plan.next_step() is not None:
            current = next(
                (step for step in plan.steps if step.status == "in_progress"),
                None,
            )
            if current is not None:
                current.status = "skipped"
                current.result = "Stopped by user."
        plan.status = "stopped"
        yield TaskStopped(reason="Task stopped by user.", plan=plan)

    async def arun(
        self,
        task: str,
        context: list[ModelMessage],
        plan: TaskPlan | None = None,
        generation_options: dict[str, object] | None = None,
        planning_options: dict[str, object] | None = None,
        inactivity_timeout: int | None = None,
        session: Session | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """Async execute loop (non-blocking model I/O, tools via asyncio.to_thread)."""
        options = build_generation_profile(
            phase="step",
            base_config=generation_options,
            model_info=self._resolve_model_info(),
        )

        if plan is None:
            yield PhaseChanged(phase="planning", summary="Prepared adaptive execution plan.")
            plan = self._bootstrap_plan(task)
        else:
            yield PhaseChanged(phase="planning", summary="Prepared execution plan.")
        plan.status = "executing"
        transcript = list(context)
        last_activity = time.monotonic()
        yield PlanGenerated(plan)
        yield PhaseChanged(phase="executing", summary="Executing plan.")

        while not self._stop_requested:
            if inactivity_timeout and (time.monotonic() - last_activity) > inactivity_timeout:
                plan.status = "timed_out"
                yield TaskTimedOut(
                    reason="Agent task timed out due to inactivity.",
                    plan=plan,
                )
                return
            step = plan.next_step()
            if step is None:
                plan.status = "completed"
                summary = self._summarize_plan(plan)
                yield TaskComplete(summary=summary, plan=plan)
                return

            step.status = "in_progress"
            yield StepStarted(step=step)

            step_summary: str | None = None
            new_messages: list[ModelMessage] = []
            errors = 0
            failure_kind = ""
            last_model_error = ""
            failure_context: FailureContext | None = None
            async for piece in self._arun_step_async(
                task, plan, step, transcript, options, session
            ):
                if isinstance(piece, _AsyncStepDone):
                    step_summary = piece.summary
                    new_messages = piece.new_messages
                    errors = piece.errors
                    failure_kind = piece.failure_kind
                    last_model_error = piece.last_model_error
                    failure_context = piece.failure_context
                    break
                yield piece
            transcript.extend(new_messages)
            last_activity = time.monotonic()

            if self._stop_requested:
                break

            if step_summary is None:
                if (
                    failure_kind in {"tool_retry_budget_exhausted", "model_retry_budget_exhausted"}
                    and failure_context is not None
                    and should_replan_after_failure(failure_context.failure_class)
                ):
                    replan_reason = self._build_replan_reason(step, failure_context)
                    yield PhaseChanged(
                        phase="replanning",
                        summary=(
                            "Replanning after "
                            f"{failure_class_label(failure_context.failure_class)} "
                            f"failures in step {step.index}."
                        ),
                        step_index=step.index,
                        step_description=step.description,
                    )
                    revised = await self._planner.arevise_plan(
                        task,
                        plan,
                        replan_reason,
                        generation_options=planning_options,
                    )
                    self._preserve_completed_steps(plan, revised)
                    plan = revised
                    plan.status = "executing"
                    yield PlanUpdated(
                        plan=plan,
                        changes=(
                            f"Replanned after {failure_class_label(failure_context.failure_class)} "
                            f"failure in step {step.index}."
                        ),
                    )
                    yield PhaseChanged(phase="executing", summary="Continuing with revised plan.")
                    continue

                if failure_kind in {"model_error_threshold", "model_retry_budget_exhausted"}:
                    detail = (
                        f"Model errors prevented step {step.index} from completing "
                        f"after {errors} attempt(s)."
                    )
                    if last_model_error:
                        detail = f"{detail} Last model error: {last_model_error}"
                    plan.update_step(step.index, "failed", "Repeated model errors.")
                    plan.status = "failed"
                    yield PhaseChanged(
                        phase="failed",
                        summary=detail,
                        step_index=step.index,
                        step_description=step.description,
                    )
                    yield PlanUpdated(
                        plan=plan,
                        changes=(
                            f"Step {step.index} failed after repeated model errors "
                            "reached the retry threshold."
                        ),
                    )
                    reason = (
                        f"Failed while executing step {step.index}: {step.description} "
                        "(model error threshold reached)."
                    )
                    if last_model_error:
                        reason = f"{reason} Last model error: {last_model_error}"
                    yield TaskFailed(
                        reason=reason,
                        failure_type=failure_context.failure_class.value
                        if failure_context is not None
                        else FailureClass.MODEL_TRANSIENT.value,
                        plan=plan,
                    )
                    return

                if (
                    failure_kind in {"tool_error_threshold", "tool_retry_budget_exhausted"}
                    and self._unified_turn_loop
                ):
                    failure_label = (
                        failure_class_label(failure_context.failure_class)
                        if failure_context is not None
                        else "tool"
                    )
                    plan.update_step(step.index, "failed", "Repeated tool failures.")
                    plan.status = "failed"
                    yield PhaseChanged(
                        phase="failed",
                        summary=(f"Step {step.index} reached the {failure_label} retry threshold."),
                        step_index=step.index,
                        step_description=step.description,
                    )
                    yield PlanUpdated(
                        plan=plan,
                        changes=(
                            f"Step {step.index} failed after repeated tool failures "
                            "reached the retry threshold."
                        ),
                    )
                    yield TaskFailed(
                        reason=(
                            f"Failed while executing step {step.index}: {step.description} "
                            f"({failure_label} failure threshold reached)."
                        ),
                        failure_type=failure_context.failure_class.value
                        if failure_context is not None
                        else None,
                        plan=plan,
                    )
                    return

                if self._unified_turn_loop:
                    plan.update_step(
                        step.index, "failed", "Unified turn loop could not complete step."
                    )
                    plan.status = "failed"
                    yield PhaseChanged(
                        phase="failed",
                        summary=(
                            f"Unified turn loop could not complete step {step.index} "
                            f"within {self._max_step_rounds} rounds."
                        ),
                        step_index=step.index,
                        step_description=step.description,
                    )
                    yield PlanUpdated(
                        plan=plan,
                        changes=(
                            f"Step {step.index} failed after unified turn-loop "
                            "budget was exhausted."
                        ),
                    )
                    yield TaskFailed(
                        reason=(
                            f"Failed while executing step {step.index}: {step.description} "
                            f"(unified turn-loop budget exhausted)."
                        ),
                        failure_type=FailureClass.UNKNOWN.value,
                        plan=plan,
                    )
                    return

                if errors >= self._max_consecutive_errors:
                    replan_reason = (
                        self._build_replan_reason(step, failure_context)
                        if failure_context is not None
                        else f"Step {step.index} encountered repeated tool failures."
                    )
                    yield PhaseChanged(
                        phase="replanning",
                        summary=f"Replanning after repeated failures in step {step.index}.",
                        step_index=step.index,
                        step_description=step.description,
                    )
                    revised = await self._planner.arevise_plan(
                        task,
                        plan,
                        replan_reason,
                        generation_options=planning_options,
                    )
                    self._preserve_completed_steps(plan, revised)
                    plan = revised
                    plan.status = "executing"
                    yield PlanUpdated(
                        plan=plan,
                        changes=f"Replanned after repeated failures in step {step.index}.",
                    )
                    yield PhaseChanged(phase="executing", summary="Continuing with revised plan.")
                    continue

                plan.update_step(step.index, "failed", "Step did not complete successfully.")
                plan.status = "failed"
                yield PhaseChanged(
                    phase="failed",
                    summary=f"Failed while executing step {step.index}.",
                    step_index=step.index,
                    step_description=step.description,
                )
                yield PlanUpdated(
                    plan=plan,
                    changes=f"Step {step.index} failed: {step.description}",
                )
                yield TaskFailed(
                    reason=f"Failed while executing step {step.index}: {step.description}",
                    failure_type=failure_context.failure_class.value
                    if failure_context is not None
                    else None,
                    plan=plan,
                )
                return

            plan.update_step(step.index, "completed", step_summary)
            yield PlanUpdated(
                plan=plan,
                changes=f"Completed step {step.index}: {step.description}",
            )

        if plan.next_step() is not None:
            current = next(
                (s for s in plan.steps if s.status == "in_progress"),
                None,
            )
            if current is not None:
                current.status = "skipped"
                current.result = "Stopped by user."
        plan.status = "stopped"
        yield TaskStopped(reason="Task stopped by user.", plan=plan)

    async def _arun_step_async(
        self,
        task: str,
        plan: TaskPlan,
        step: PlanStep,
        transcript: list[ModelMessage],
        options: dict[str, object],
        session: Session | None,
    ) -> AsyncIterator[AgentEvent | _AsyncStepDone]:
        """Mirror _run_step: model rounds with tool events, then emit _AsyncStepDone."""
        conversation: list[ModelMessage] = []
        failure_counters: dict[FailureClass, int] = {}
        last_attempt_count = 0
        failure_kind = ""
        last_model_error = ""
        failure_context: FailureContext | None = None

        for _ in range(self._max_step_rounds):
            if self._stop_requested:
                yield _AsyncStepDone(
                    None,
                    conversation,
                    last_attempt_count,
                    failure_kind,
                    last_model_error,
                    failure_context,
                )
                return

            model_info = self._resolve_model_info()

            result = await self._model.agenerate(
                self._build_messages(task, plan, step, transcript, conversation, session),
                tools=self._tools.get_tool_definitions(model_info),
                tool_choice="auto",
                **options,
            )
            self._record_usage_budget(session, result.usage)

            if result.finish_reason == "error":
                error_detail = self._model_error_detail(result)
                if error_detail:
                    last_model_error = error_detail
                failure_context = classify_model_failure(last_model_error)
                budget_state = update_failure_counters(failure_counters, failure_context)
                retry_budget = min(budget_state.retry_budget, self._max_consecutive_errors)
                last_attempt_count = budget_state.attempt
                if result.usage.get("error"):
                    conversation.append(
                        ModelMessage(
                            role="assistant",
                            content="",
                            metadata={"error": result.usage["error"]},
                        )
                    )
                if budget_state.attempt >= retry_budget:
                    failure_kind = "model_retry_budget_exhausted"
                    break
                yield PhaseChanged(
                    phase="retrying",
                    summary=(
                        "Retrying step "
                        f"{step.index} after {failure_class_label(failure_context.failure_class)} "
                        f"({budget_state.attempt}/{retry_budget})."
                    ),
                    step_index=step.index,
                    step_description=step.description,
                )
                continue

            if result.reasoning.strip():
                yield ReasoningOutput(text=result.reasoning.strip())

            assistant_message = ModelMessage(role="assistant", content=result.text or "")
            if result.tool_calls:
                assistant_message.metadata["tool_calls"] = result.tool_calls
                conversation.append(assistant_message)
                self._async_tool_batch = None
                self._guardian_context = _GuardianContext(
                    task=task,
                    step_index=step.index,
                    step_description=step.description,
                    transcript=list(transcript),
                    conversation=list(conversation),
                )
                try:
                    async for event in self._ahandle_tool_calls_async(result):
                        yield event
                finally:
                    self._guardian_context = None
                batch: tuple[list[ModelMessage], FailureContext | None] = (
                    self._async_tool_batch or ([], None)
                )
                tool_messages, tool_failure_context = batch
                self._async_tool_batch = None
                conversation.extend(tool_messages)
                if tool_failure_context is not None:
                    failure_context = tool_failure_context
                    budget_state = update_failure_counters(failure_counters, failure_context)
                    retry_budget = min(budget_state.retry_budget, self._max_consecutive_errors)
                    last_attempt_count = budget_state.attempt
                    if budget_state.attempt >= retry_budget:
                        failure_kind = "tool_retry_budget_exhausted"
                        break
                    yield PhaseChanged(
                        phase="retrying",
                        summary=(
                            "Retrying step "
                            f"{step.index} after "
                            f"{failure_class_label(failure_context.failure_class)} "
                            f"({budget_state.attempt}/{retry_budget})."
                        ),
                        step_index=step.index,
                        step_description=step.description,
                    )
                else:
                    failure_counters.clear()
                    last_attempt_count = 0
                    failure_context = None
                continue

            text = (result.text or "").strip()
            if text:
                conversation.append(assistant_message)
                yield _AsyncStepDone(
                    text,
                    conversation,
                    last_attempt_count,
                    failure_kind,
                    last_model_error,
                    failure_context,
                )
                return

            if conversation:
                yield _AsyncStepDone(
                    self._summarize_observations(conversation),
                    conversation,
                    last_attempt_count,
                    failure_kind,
                    last_model_error,
                    failure_context,
                )
                return

        if not failure_kind:
            failure_kind = "round_budget_exhausted"
        yield _AsyncStepDone(
            None,
            conversation,
            last_attempt_count,
            failure_kind,
            last_model_error,
            failure_context,
        )

    async def _ahandle_tool_calls_async(
        self,
        result: GenerationResult,
    ) -> AsyncIterator[AgentEvent]:
        messages: list[ModelMessage] = []
        failure_context: FailureContext | None = None
        try:
            if self._parallel_read_only_batch_eligible(result):
                prepared: list[tuple[str, str, dict, Tool]] = []
                for raw_call in result.tool_calls:
                    call_id, tool_name, arguments, parse_error = self._normalize_tool_call(raw_call)
                    tool = self._tools.get_tool(tool_name) if tool_name else None
                    if tool is None or parse_error is not None:
                        raise RuntimeError("parallel read-only batch invariant violated")
                    decision = self._safety.check_and_approve(tool, arguments)
                    if not decision.approved:
                        raise RuntimeError("parallel read-only batch invariant violated")
                    resolved = tool.name
                    prepared.append((call_id, resolved, arguments, tool))
                    yield ToolCallRequested(
                        tool_name=resolved,
                        arguments=arguments,
                        requires_approval=False,
                        risk_level=decision.risk_level.value,
                        warnings=decision.warnings,
                        risk_reason=decision.risk_reason,
                        rollback_summary=decision.rollback_summary,
                    )

                max_workers = min(len(prepared), _PARALLEL_READ_ONLY_MAX_WORKERS)
                sem = asyncio.Semaphore(max_workers)

                async def run_one(tool: Tool, arguments: dict) -> ToolResult:
                    async with sem:
                        return await asyncio.to_thread(self._execute_tool_safely, tool, arguments)

                tool_results = await asyncio.gather(
                    *[run_one(tool, arguments) for _cid, _n, arguments, tool in prepared]
                )

                for (call_id, resolved_name, _arguments, _tool), tool_result in zip(
                    prepared, tool_results, strict=True
                ):
                    yield ToolCallResult(
                        tool_name=resolved_name,
                        result=tool_result,
                        rollback_entries=len(self._safety.rollback.get_history()),
                    )
                    messages.append(
                        ModelMessage(
                            role="tool",
                            content=self._tool_payload(resolved_name, tool_result),
                            metadata={"tool_call_id": call_id, "tool_name": resolved_name},
                        )
                    )
                    if tool_result.status != "success" and failure_context is None:
                        failure_context = classify_tool_failure(tool_result)
                    if tool_result.status in {"denied", "error", "timeout"}:
                        yield PhaseChanged(
                            phase="recovering",
                            summary=(
                                f"Recovering after {tool_result.status} tool result: "
                                f"{resolved_name}."
                            ),
                        )
            else:
                for raw_call in result.tool_calls:
                    call_id, tool_name, arguments, parse_error = self._normalize_tool_call(raw_call)
                    tool = self._tools.get_tool(tool_name) if tool_name else None
                    if tool is None:
                        tool_result = ToolResult.error_result(
                            f"Unknown tool '{tool_name or 'unknown'}'",
                            parse_error or "The requested tool is not registered.",
                        )
                        yield ToolCallResult(
                            tool_name=tool_name or "unknown",
                            result=tool_result,
                            rollback_entries=len(self._safety.rollback.get_history()),
                        )
                        messages.append(
                            ModelMessage(
                                role="tool",
                                content=self._tool_payload(tool_name or "unknown", tool_result),
                                metadata={
                                    "tool_call_id": call_id,
                                    "tool_name": tool_name or "unknown",
                                },
                            )
                        )
                        if failure_context is None:
                            failure_context = classify_tool_failure(tool_result)
                        continue
                    if parse_error is not None:
                        tool_result = ToolResult.error_result(
                            f"Invalid arguments for tool '{tool_name}'",
                            parse_error,
                        )
                        yield ToolCallResult(
                            tool_name=tool_name or "unknown",
                            result=tool_result,
                            rollback_entries=len(self._safety.rollback.get_history()),
                        )
                        messages.append(
                            ModelMessage(
                                role="tool",
                                content=self._tool_payload(tool_name or "unknown", tool_result),
                                metadata={
                                    "tool_call_id": call_id,
                                    "tool_name": tool_name or "unknown",
                                },
                            )
                        )
                        if failure_context is None:
                            failure_context = classify_tool_failure(tool_result)
                        continue

                    resolved_tool_name = tool.name
                    decision = self._safety.check_and_approve(tool, arguments)
                    requires_approval = decision.requires_approval
                    route_to_guardian = self._should_route_approval_to_guardian(
                        resolved_tool_name,
                        requires_approval,
                    )
                    if decision.blocked:
                        tool_result = ToolResult.error_result(
                            f"Blocked tool '{resolved_tool_name}'",
                            decision.reason or "The requested action violated a safety rule.",
                        )
                        yield ToolCallResult(
                            tool_name=resolved_tool_name,
                            result=tool_result,
                            rollback_entries=len(self._safety.rollback.get_history()),
                        )
                        yield PhaseChanged(
                            phase="recovering",
                            summary=f"Recovering after blocked tool call: {resolved_tool_name}.",
                        )
                        messages.append(
                            ModelMessage(
                                role="tool",
                                content=self._tool_payload(resolved_tool_name, tool_result),
                                metadata={
                                    "tool_call_id": call_id,
                                    "tool_name": resolved_tool_name,
                                },
                            )
                        )
                        if failure_context is None:
                            failure_context = classify_tool_failure(tool_result)
                        continue

                    request = ToolCallRequested(
                        tool_name=resolved_tool_name,
                        arguments=arguments,
                        requires_approval=requires_approval and not route_to_guardian,
                        risk_level=decision.risk_level.value,
                        warnings=decision.warnings,
                        risk_reason=decision.risk_reason,
                        rollback_summary=decision.rollback_summary,
                    )
                    guardian_review: GuardianReviewResult | None = None
                    if route_to_guardian:
                        yield PhaseChanged(
                            phase="waiting_approval",
                            summary=f"Guardian reviewing action: {resolved_tool_name}.",
                        )
                        yield request
                        guardian_request = self._build_guardian_request(
                            tool_name=resolved_tool_name,
                            arguments=arguments,
                            risk_level=decision.risk_level.value,
                            risk_reason=decision.risk_reason,
                            warnings=decision.warnings,
                        )
                        yield GuardianReviewStarted(
                            tool_name=resolved_tool_name,
                            action_summary=guardian_request.action_summary(),
                        )
                        guardian_review = await areview_with_guardian(
                            self._model,
                            guardian_request,
                        )
                        yield GuardianReviewCompleted(
                            tool_name=resolved_tool_name,
                            approved=guardian_review.approved,
                            risk_level=guardian_review.risk_level,
                            risk_score=guardian_review.risk_score,
                            rationale=guardian_review.rationale,
                            evidence=guardian_review.evidence,
                            failure=guardian_review.failure,
                        )
                        approved = guardian_review.approved
                    elif requires_approval:
                        yield PhaseChanged(
                            phase="waiting_approval",
                            summary=f"Waiting for approval: {resolved_tool_name}.",
                        )
                        yield request
                        approved = await self._await_tool_approval()
                    else:
                        yield request
                        approved = True

                    if approved:
                        tool_result = await asyncio.to_thread(
                            self._execute_tool_safely, tool, arguments
                        )
                    else:
                        if guardian_review is not None:
                            denial_payload = {
                                "tool_name": resolved_tool_name,
                                "risk_level": guardian_review.risk_level,
                                "risk_score": guardian_review.risk_score,
                                "rationale": guardian_review.rationale,
                                "evidence": guardian_review.evidence,
                                "failure": guardian_review.failure,
                            }
                            tool_result = ToolResult.denied(
                                f"Guardian denied tool '{resolved_tool_name}'",
                                output=json.dumps(denial_payload, ensure_ascii=False),
                            )
                        else:
                            tool_result = ToolResult.denied(
                                f"User denied tool '{tool_name}'",
                                output=json.dumps(arguments, indent=2, sort_keys=True),
                            )

                    yield ToolCallResult(
                        tool_name=resolved_tool_name,
                        result=tool_result,
                        rollback_entries=len(self._safety.rollback.get_history()),
                    )
                    messages.append(
                        ModelMessage(
                            role="tool",
                            content=self._tool_payload(resolved_tool_name, tool_result),
                            metadata={
                                "tool_call_id": call_id,
                                "tool_name": resolved_tool_name,
                            },
                        )
                    )
                    if tool_result.status != "success" and failure_context is None:
                        failure_context = classify_tool_failure(tool_result)
                    if tool_result.status in {"denied", "error", "timeout"}:
                        yield PhaseChanged(
                            phase="recovering",
                            summary=(
                                f"Recovering after {tool_result.status} tool result: "
                                f"{resolved_tool_name}."
                            ),
                        )
        finally:
            self._async_tool_batch = (messages, failure_context)

    def _run_step(
        self,
        task: str,
        plan: TaskPlan,
        step: PlanStep,
        transcript: list[ModelMessage],
        options: dict[str, object],
        session: Session | None,
    ) -> Generator[
        AgentEvent,
        bool,
        tuple[str | None, list[ModelMessage], int, str, str, FailureContext | None],
    ]:
        conversation: list[ModelMessage] = []
        failure_counters: dict[FailureClass, int] = {}
        last_attempt_count = 0
        failure_kind = ""
        last_model_error = ""
        failure_context: FailureContext | None = None

        for _ in range(self._max_step_rounds):
            if self._stop_requested:
                return (
                    None,
                    conversation,
                    last_attempt_count,
                    failure_kind,
                    last_model_error,
                    failure_context,
                )

            model_info = self._resolve_model_info()

            result = self._model.generate(
                self._build_messages(task, plan, step, transcript, conversation, session),
                tools=self._tools.get_tool_definitions(model_info),
                tool_choice="auto",
                **options,
            )
            self._record_usage_budget(session, result.usage)

            if result.finish_reason == "error":
                error_detail = self._model_error_detail(result)
                if error_detail:
                    last_model_error = error_detail
                failure_context = classify_model_failure(last_model_error)
                budget_state = update_failure_counters(failure_counters, failure_context)
                retry_budget = min(budget_state.retry_budget, self._max_consecutive_errors)
                last_attempt_count = budget_state.attempt
                if result.usage.get("error"):
                    conversation.append(
                        ModelMessage(
                            role="assistant",
                            content="",
                            metadata={"error": result.usage["error"]},
                        )
                    )
                if budget_state.attempt >= retry_budget:
                    failure_kind = "model_retry_budget_exhausted"
                    break
                yield PhaseChanged(
                    phase="retrying",
                    summary=(
                        "Retrying step "
                        f"{step.index} after {failure_class_label(failure_context.failure_class)} "
                        f"({budget_state.attempt}/{retry_budget})."
                    ),
                    step_index=step.index,
                    step_description=step.description,
                )
                continue

            if result.reasoning.strip():
                yield ReasoningOutput(text=result.reasoning.strip())

            assistant_message = ModelMessage(role="assistant", content=result.text or "")
            if result.tool_calls:
                assistant_message.metadata["tool_calls"] = result.tool_calls
                conversation.append(assistant_message)
                self._sync_tool_failure_context = None
                self._guardian_context = _GuardianContext(
                    task=task,
                    step_index=step.index,
                    step_description=step.description,
                    transcript=list(transcript),
                    conversation=list(conversation),
                )
                try:
                    tool_messages, had_error = yield from self._handle_tool_calls(result)
                finally:
                    self._guardian_context = None
                tool_failure_context = self._sync_tool_failure_context
                conversation.extend(tool_messages)
                if tool_failure_context is not None or had_error:
                    if tool_failure_context is None:
                        tool_failure_context = FailureContext(
                            FailureClass.UNKNOWN,
                            "Unknown tool failure.",
                        )
                    failure_context = tool_failure_context
                    budget_state = update_failure_counters(failure_counters, failure_context)
                    retry_budget = min(budget_state.retry_budget, self._max_consecutive_errors)
                    last_attempt_count = budget_state.attempt
                    if budget_state.attempt >= retry_budget:
                        failure_kind = "tool_retry_budget_exhausted"
                        break
                    yield PhaseChanged(
                        phase="retrying",
                        summary=(
                            "Retrying step "
                            f"{step.index} after "
                            f"{failure_class_label(failure_context.failure_class)} "
                            f"({budget_state.attempt}/{retry_budget})."
                        ),
                        step_index=step.index,
                        step_description=step.description,
                    )
                else:
                    failure_counters.clear()
                    last_attempt_count = 0
                    failure_context = None
                continue

            text = (result.text or "").strip()
            if text:
                conversation.append(assistant_message)
                return (
                    text,
                    conversation,
                    last_attempt_count,
                    failure_kind,
                    last_model_error,
                    failure_context,
                )

            if conversation:
                return (
                    self._summarize_observations(conversation),
                    conversation,
                    last_attempt_count,
                    failure_kind,
                    last_model_error,
                    failure_context,
                )

        if not failure_kind:
            failure_kind = "round_budget_exhausted"
        return (
            None,
            conversation,
            last_attempt_count,
            failure_kind,
            last_model_error,
            failure_context,
        )

    def _handle_tool_calls(
        self,
        result: GenerationResult,
    ) -> Generator[AgentEvent, bool, tuple[list[ModelMessage], bool]]:
        self._sync_tool_failure_context = None
        if self._parallel_read_only_batch_eligible(result):
            return (yield from self._handle_tool_calls_parallel_read_only(result))
        return (yield from self._handle_tool_calls_sequential(result))

    def _parallel_read_only_batch_eligible(self, result: GenerationResult) -> bool:
        raw_calls = result.tool_calls
        if len(raw_calls) < 2:
            return False
        for raw_call in raw_calls:
            _call_id, tool_name, arguments, parse_error = self._normalize_tool_call(raw_call)
            if parse_error is not None:
                return False
            tool = self._tools.get_tool(tool_name) if tool_name else None
            if tool is None or not tool.is_read_only:
                return False
            decision = self._safety.check_and_approve(tool, arguments)
            if not decision.approved:
                return False
        return True

    def _execute_tool_safely(self, tool: Tool, arguments: dict) -> ToolResult:
        """Run pre/post hooks and tool execution (used on worker threads for read-only tools)."""
        self._safety.pre_action(tool, arguments)
        tool_result = tool.execute(**arguments)
        self._safety.post_action(tool, arguments, tool_result)
        return tool_result

    def _handle_tool_calls_parallel_read_only(
        self,
        result: GenerationResult,
    ) -> Generator[AgentEvent, bool, tuple[list[ModelMessage], bool]]:
        messages: list[ModelMessage] = []
        had_error = False
        failure_context: FailureContext | None = None
        prepared: list[tuple[str, str, dict, Tool]] = []

        for raw_call in result.tool_calls:
            call_id, tool_name, arguments, parse_error = self._normalize_tool_call(raw_call)
            tool = self._tools.get_tool(tool_name) if tool_name else None
            if tool is None or parse_error is not None:
                raise RuntimeError("parallel read-only batch invariant violated")
            decision = self._safety.check_and_approve(tool, arguments)
            if not decision.approved:
                raise RuntimeError("parallel read-only batch invariant violated")
            resolved = tool.name
            prepared.append((call_id, resolved, arguments, tool))
            yield ToolCallRequested(
                tool_name=resolved,
                arguments=arguments,
                requires_approval=False,
                risk_level=decision.risk_level.value,
                warnings=decision.warnings,
                risk_reason=decision.risk_reason,
                rollback_summary=decision.rollback_summary,
            )

        max_workers = min(len(prepared), _PARALLEL_READ_ONLY_MAX_WORKERS)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(self._execute_tool_safely, tool, arguments)
                for _call_id, _name, arguments, tool in prepared
            ]
            tool_results = [f.result() for f in futures]

        for (call_id, resolved_name, _arguments, _tool), tool_result in zip(
            prepared, tool_results, strict=True
        ):
            rollback_entries = len(self._safety.rollback.get_history())
            yield ToolCallResult(
                tool_name=resolved_name,
                result=tool_result,
                rollback_entries=rollback_entries,
            )
            messages.append(
                ModelMessage(
                    role="tool",
                    content=self._tool_payload(resolved_name, tool_result),
                    metadata={"tool_call_id": call_id, "tool_name": resolved_name},
                )
            )
            had_error = had_error or tool_result.status != "success"
            if tool_result.status != "success" and failure_context is None:
                failure_context = classify_tool_failure(tool_result)
            if tool_result.status in {"denied", "error", "timeout"}:
                yield PhaseChanged(
                    phase="recovering",
                    summary=(
                        f"Recovering after {tool_result.status} tool result: {resolved_name}."
                    ),
                )

        self._sync_tool_failure_context = failure_context
        return messages, had_error

    def _handle_tool_calls_sequential(
        self,
        result: GenerationResult,
    ) -> Generator[AgentEvent, bool, tuple[list[ModelMessage], bool]]:
        messages: list[ModelMessage] = []
        had_error = False
        failure_context: FailureContext | None = None

        for raw_call in result.tool_calls:
            call_id, tool_name, arguments, parse_error = self._normalize_tool_call(raw_call)
            requires_approval = False
            approved = True

            tool = self._tools.get_tool(tool_name) if tool_name else None
            if tool is None:
                tool_result = ToolResult.error_result(
                    f"Unknown tool '{tool_name or 'unknown'}'",
                    parse_error or "The requested tool is not registered.",
                )
            elif parse_error is not None:
                tool_result = ToolResult.error_result(
                    f"Invalid arguments for tool '{tool_name}'",
                    parse_error,
                )
            else:
                resolved_tool_name = tool.name
                decision = self._safety.check_and_approve(tool, arguments)
                requires_approval = decision.requires_approval
                route_to_guardian = self._should_route_approval_to_guardian(
                    resolved_tool_name,
                    requires_approval,
                )
                if decision.blocked:
                    tool_result = ToolResult.error_result(
                        f"Blocked tool '{resolved_tool_name}'",
                        decision.reason or "The requested action violated a safety rule.",
                    )
                    yield ToolCallResult(
                        tool_name=resolved_tool_name,
                        result=tool_result,
                        rollback_entries=len(self._safety.rollback.get_history()),
                    )
                    yield PhaseChanged(
                        phase="recovering",
                        summary=f"Recovering after blocked tool call: {resolved_tool_name}.",
                    )
                    messages.append(
                        ModelMessage(
                            role="tool",
                            content=self._tool_payload(resolved_tool_name, tool_result),
                            metadata={
                                "tool_call_id": call_id,
                                "tool_name": resolved_tool_name,
                            },
                        )
                    )
                    if failure_context is None:
                        failure_context = classify_tool_failure(tool_result)
                    continue

                request = ToolCallRequested(
                    tool_name=resolved_tool_name,
                    arguments=arguments,
                    requires_approval=requires_approval and not route_to_guardian,
                    risk_level=decision.risk_level.value,
                    warnings=decision.warnings,
                    risk_reason=decision.risk_reason,
                    rollback_summary=decision.rollback_summary,
                )
                guardian_review: GuardianReviewResult | None = None
                if route_to_guardian:
                    yield PhaseChanged(
                        phase="waiting_approval",
                        summary=f"Guardian reviewing action: {resolved_tool_name}.",
                    )
                    yield request
                    guardian_request = self._build_guardian_request(
                        tool_name=resolved_tool_name,
                        arguments=arguments,
                        risk_level=decision.risk_level.value,
                        risk_reason=decision.risk_reason,
                        warnings=decision.warnings,
                    )
                    yield GuardianReviewStarted(
                        tool_name=resolved_tool_name,
                        action_summary=guardian_request.action_summary(),
                    )
                    guardian_review = review_with_guardian(self._model, guardian_request)
                    yield GuardianReviewCompleted(
                        tool_name=resolved_tool_name,
                        approved=guardian_review.approved,
                        risk_level=guardian_review.risk_level,
                        risk_score=guardian_review.risk_score,
                        rationale=guardian_review.rationale,
                        evidence=guardian_review.evidence,
                        failure=guardian_review.failure,
                    )
                    approved = guardian_review.approved
                elif requires_approval:
                    yield PhaseChanged(
                        phase="waiting_approval",
                        summary=f"Waiting for approval: {resolved_tool_name}.",
                    )
                    approved = bool((yield request))
                else:
                    yield request

                if approved:
                    self._safety.pre_action(tool, arguments)
                    tool_result = tool.execute(**arguments)
                    self._safety.post_action(tool, arguments, tool_result)
                else:
                    if guardian_review is not None:
                        denial_payload = {
                            "tool_name": resolved_tool_name,
                            "risk_level": guardian_review.risk_level,
                            "risk_score": guardian_review.risk_score,
                            "rationale": guardian_review.rationale,
                            "evidence": guardian_review.evidence,
                            "failure": guardian_review.failure,
                        }
                        tool_result = ToolResult.denied(
                            f"Guardian denied tool '{resolved_tool_name}'",
                            output=json.dumps(denial_payload, ensure_ascii=False),
                        )
                    else:
                        tool_result = ToolResult.denied(
                            f"User denied tool '{tool_name}'",
                            output=json.dumps(arguments, indent=2, sort_keys=True),
                        )

            rollback_entries = len(self._safety.rollback.get_history())
            yield ToolCallResult(
                tool_name=tool_name or "unknown",
                result=tool_result,
                rollback_entries=rollback_entries,
            )
            messages.append(
                ModelMessage(
                    role="tool",
                    content=self._tool_payload(tool_name or "unknown", tool_result),
                    metadata={"tool_call_id": call_id, "tool_name": tool_name or "unknown"},
                )
            )
            had_error = had_error or tool_result.status != "success"
            if tool_result.status != "success" and failure_context is None:
                failure_context = classify_tool_failure(tool_result)
            if tool_result.status in {"denied", "error", "timeout"}:
                recovery_target = tool_name or "unknown"
                yield PhaseChanged(
                    phase="recovering",
                    summary=(
                        f"Recovering after {tool_result.status} tool result: {recovery_target}."
                    ),
                )

        self._sync_tool_failure_context = failure_context
        return messages, had_error

    def _should_route_approval_to_guardian(self, tool_name: str, requires_approval: bool) -> bool:
        """Whether this approval should be reviewed by guardian."""
        if not requires_approval:
            return False
        if self._approvals_reviewer != "guardian_subagent":
            return False
        if tool_name in {"shell_execute", "file_write", "patch_apply"}:
            return True
        if tool_name.startswith("mcp__"):
            return True
        return False

    def _build_guardian_request(
        self,
        *,
        tool_name: str,
        arguments: dict,
        risk_level: str,
        risk_reason: str | None,
        warnings: list[str],
    ) -> GuardianReviewRequest:
        """Build a guardian request from the current loop context."""
        context = self._guardian_context
        if context is None:
            return GuardianReviewRequest(
                tool_name=tool_name,
                arguments=arguments,
                risk_level=risk_level,
                risk_reason=risk_reason or "",
                warnings=list(warnings),
            )

        transcript_tail = [*context.transcript, *context.conversation][-8:]
        return GuardianReviewRequest(
            tool_name=tool_name,
            arguments=arguments,
            risk_level=risk_level,
            risk_reason=risk_reason or "",
            warnings=list(warnings),
            task=context.task,
            step_index=context.step_index,
            step_description=context.step_description,
            transcript_tail=transcript_tail,
        )

    def _build_messages(
        self,
        task: str,
        plan: TaskPlan,
        step: PlanStep,
        transcript: list[ModelMessage],
        conversation: list[ModelMessage],
        session: Session | None,
    ) -> list[ModelMessage]:
        plan_text = "\n".join(
            f"{plan_step.index}. [{plan_step.status}] {plan_step.description}"
            for plan_step in plan.steps
        )
        content = (
            f"{_STEP_PROMPT}\n"
            f"Task objective:\n{task}\n\n"
            f"Plan status:\n{plan_text}\n\n"
            f"Current step focus:\n{step.index}. {step.description}"
        )
        if session is not None:
            runtime = format_agent_task_runtime_section(session)
            if runtime:
                content = f"{content}\n\n{AGENT_TASK_RUNTIME_HEADING}\n{runtime}"

        context_updates: str | None = None
        if session is not None:
            baseline = session.metadata.get("context_diff_baseline")
            tracker = ContextDiffTracker(baseline if isinstance(baseline, dict) else None)
            current_snapshot = build_turn_context_snapshot(session)
            diff = tracker.compute(current_snapshot)
            session.metadata["last_context_diff"] = diff.to_dict()
            session.metadata["context_diff_baseline"] = tracker.baseline or current_snapshot
            context_updates = render_context_diff_for_prompt(diff)

        transcript_system: list[str] = []
        transcript_messages: list[ModelMessage] = []
        for message in transcript:
            if message.role == "system":
                text = message.content.strip()
                if text:
                    transcript_system.append(text)
                continue
            transcript_messages.append(message)

        if session is not None:
            env_xml = get_environment_context_xml(session.workspace)
            if not transcript_system:
                transcript_system.extend(build_system_instructions(session))
                if env_xml.strip():
                    transcript_system.append(env_xml)
            elif env_xml.strip() and not any(
                "<environment_context>" in existing for existing in transcript_system
            ):
                transcript_system.append(env_xml)

        system_parts = [content]
        if context_updates:
            system_parts.append("Context updates since previous turn:\n" + context_updates)
        if transcript_system:
            system_parts.append(
                "Session instructions and environment context:\n" + "\n\n".join(transcript_system)
            )
        system = ModelMessage(role="system", content="\n\n".join(system_parts))
        return [system, *transcript_messages, *conversation]

    def _normalize_tool_call(
        self,
        raw_call: dict,
    ) -> tuple[str, str | None, dict, str | None]:
        function = raw_call.get("function", raw_call)
        call_id = str(raw_call.get("id", ""))
        tool_name = function.get("name")
        raw_arguments = function.get("arguments", {})

        if isinstance(raw_arguments, dict):
            return call_id, tool_name, raw_arguments, None
        if isinstance(raw_arguments, str):
            try:
                parsed = json.loads(raw_arguments) if raw_arguments.strip() else {}
            except json.JSONDecodeError as exc:
                return call_id, tool_name, {}, f"Could not parse arguments: {exc}"
            if not isinstance(parsed, dict):
                return call_id, tool_name, {}, "Parsed tool arguments were not an object."
            return call_id, tool_name, parsed, None
        return call_id, tool_name, {}, "Tool arguments must be a JSON object."

    def _tool_payload(self, tool_name: str, result: ToolResult) -> str:
        model_info = self._resolve_model_info()
        truncated_output = truncate_for_model_output(result.output, model_info)
        payload = {
            "tool": tool_name,
            "status": result.status,
            "summary": result.summary,
            "output": truncated_output.text,
            "output_truncated": truncated_output.was_truncated,
            "output_original_chars": truncated_output.original_chars,
            "output_retained_chars": truncated_output.retained_chars,
            "error": result.error,
            "exit_code": result.exit_code,
            "files_changed": result.files_changed,
        }
        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def _record_usage_budget(session: Session | None, usage: object) -> None:
        if session is None:
            return
        update_session_usage_budget(session, usage, source="agent_step")

    def _summarize_observations(self, conversation: list[ModelMessage]) -> str:
        tool_messages = [msg for msg in conversation if msg.role == "tool"]
        if not tool_messages:
            return "Step completed."
        latest = tool_messages[-1].content
        return f"Step completed after tool execution. Latest observation: {latest[:240]}"

    def _build_replan_reason(self, step: PlanStep, failure_context: FailureContext) -> str:
        detail = failure_context.detail.strip()
        if len(detail) > 240:
            detail = f"{detail[:237]}..."
        return (
            f"Step {step.index} encountered {failure_class_label(failure_context.failure_class)} "
            f"failure. Detail: {detail or 'N/A'}. "
            f"Recovery guidance: {failure_class_hint(failure_context.failure_class)}"
        )

    @staticmethod
    def _model_error_detail(result: GenerationResult) -> str:
        usage = result.usage if isinstance(result.usage, dict) else {}
        raw = usage.get("error")
        if isinstance(raw, str):
            return raw.strip()
        return ""

    def _summarize_plan(self, plan: TaskPlan) -> str:
        completed = [step for step in plan.steps if step.status == "completed"]
        if not completed:
            return "Task completed."
        lines = [
            f"{step.index}. {step.description}: {step.result or 'Completed.'}" for step in completed
        ]
        return "\n".join(lines)

    def _preserve_completed_steps(self, current: TaskPlan, revised: TaskPlan) -> None:
        completed = [step for step in current.steps if step.status == "completed"]
        pending = [step for step in revised.steps if step.status == "pending"]
        revised.steps = [
            *[
                PlanStep(
                    index=index,
                    description=step.description,
                    status=step.status,
                    tool_calls=step.tool_calls,
                    result=step.result,
                )
                for index, step in enumerate(completed, start=1)
            ],
            *[
                PlanStep(index=0, description=step.description, status="pending")
                for step in pending
            ],
        ]
        revised._renumber()

    def _bootstrap_plan(self, task: str) -> TaskPlan:
        """Create an initial execution plan without a separate model planning turn."""
        return TaskPlan(
            task=task,
            steps=[PlanStep(index=1, description=task)],
            status="planning",
        )

    def _resolve_model_info(self) -> ModelInfo:
        """Best-effort model info lookup for standalone loop usage in tests and adapters."""
        resolver = getattr(self._model, "model_info", None)
        if callable(resolver):
            try:
                info = resolver()
            except Exception:
                info = None
            if isinstance(info, ModelInfo):
                return info
        return ModelInfo(id="unknown-model")
