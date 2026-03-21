"""Iterative agent loop for task execution with tools."""

from __future__ import annotations

import json
import time
from collections.abc import Generator

from localagentcli.agents.events import (
    AgentEvent,
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
from localagentcli.models.abstraction import ModelAbstractionLayer
from localagentcli.models.backends.base import GenerationResult, ModelMessage
from localagentcli.safety.layer import SafetyLayer
from localagentcli.session.state import Session
from localagentcli.session.task_context import (
    AGENT_TASK_RUNTIME_HEADING,
    format_agent_task_runtime_section,
)
from localagentcli.tools.base import ToolResult
from localagentcli.tools.registry import ToolRegistry
from localagentcli.tools.router import ToolRouter

_STEP_PROMPT = (
    "You are LocalAgentCLI operating in agent mode. "
    "Work on the current step using the available tools when needed. "
    "Do not guess file contents or command output. "
    "Once the current step is complete, respond with a concise plain-text summary of the result. "
    "If a tool call is denied or fails, adjust and continue if possible."
)


class AgentLoop:
    """Drive plan execution until the task completes, fails, or is stopped."""

    def __init__(
        self,
        model: ModelAbstractionLayer,
        tools: ToolRegistry | ToolRouter,
        planner: TaskPlanner,
        safety: SafetyLayer,
        max_consecutive_errors: int = 5,
    ):
        self._model = model
        self._tools = tools
        self._planner = planner
        self._safety = safety
        self._max_consecutive_errors = max_consecutive_errors
        self._stop_requested = False

    def stop(self) -> None:
        """Request that the loop stop at the next safe point."""
        self._stop_requested = True

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
        options: dict[str, object] = {"temperature": 0.1, "max_tokens": 1200}
        if generation_options:
            options.update(generation_options)

        if plan is None:
            yield PhaseChanged(phase="planning", summary="Planning task.")
            plan = self._planner.create_plan(
                task,
                context,
                generation_options=planning_options,
            )
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

            step_summary, new_messages, errors = yield from self._run_step(
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
                if errors >= self._max_consecutive_errors:
                    yield PhaseChanged(
                        phase="replanning",
                        summary=f"Replanning after repeated failures in step {step.index}.",
                        step_index=step.index,
                        step_description=step.description,
                    )
                    revised = self._planner.revise_plan(
                        task,
                        plan,
                        f"Step {step.index} encountered repeated tool failures.",
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

    def _run_step(
        self,
        task: str,
        plan: TaskPlan,
        step: PlanStep,
        transcript: list[ModelMessage],
        options: dict[str, object],
        session: Session | None,
    ) -> Generator[AgentEvent, bool, tuple[str | None, list[ModelMessage], int]]:
        conversation: list[ModelMessage] = []
        consecutive_errors = 0

        for _ in range(self._max_consecutive_errors + 1):
            if self._stop_requested:
                return None, conversation, consecutive_errors

            result = self._model.generate(
                self._build_messages(task, plan, step, transcript, conversation, session),
                tools=self._tools.get_tool_definitions(),
                tool_choice="auto",
                **options,
            )

            if result.finish_reason == "error":
                consecutive_errors += 1
                if result.usage.get("error"):
                    conversation.append(
                        ModelMessage(
                            role="assistant",
                            content="",
                            metadata={"error": result.usage["error"]},
                        )
                    )
                continue

            if result.reasoning.strip():
                yield ReasoningOutput(text=result.reasoning.strip())

            assistant_message = ModelMessage(role="assistant", content=result.text or "")
            if result.tool_calls:
                assistant_message.metadata["tool_calls"] = result.tool_calls
                conversation.append(assistant_message)
                tool_messages, had_error = yield from self._handle_tool_calls(result)
                conversation.extend(tool_messages)
                if had_error:
                    consecutive_errors += 1
                else:
                    consecutive_errors = 0
                continue

            text = (result.text or "").strip()
            if text:
                conversation.append(assistant_message)
                return text, conversation, consecutive_errors

            if conversation:
                return self._summarize_observations(conversation), conversation, consecutive_errors

        return None, conversation, consecutive_errors

    def _handle_tool_calls(
        self,
        result: GenerationResult,
    ) -> Generator[AgentEvent, bool, tuple[list[ModelMessage], bool]]:
        messages: list[ModelMessage] = []
        had_error = False

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
                    had_error = True
                    continue

                request = ToolCallRequested(
                    tool_name=resolved_tool_name,
                    arguments=arguments,
                    requires_approval=requires_approval,
                    risk_level=decision.risk_level.value,
                    warnings=decision.warnings,
                    risk_reason=decision.risk_reason,
                    rollback_summary=decision.rollback_summary,
                )
                if requires_approval:
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
            if tool_result.status in {"denied", "error", "timeout"}:
                recovery_target = tool_name or "unknown"
                yield PhaseChanged(
                    phase="recovering",
                    summary=(
                        f"Recovering after {tool_result.status} tool result: {recovery_target}."
                    ),
                )

        return messages, had_error

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
            f"{_STEP_PROMPT}\n\n"
            f"Task:\n{task}\n\n"
            f"Plan:\n{plan_text}\n\n"
            f"Current step:\n{step.index}. {step.description}"
        )
        if session is not None:
            runtime = format_agent_task_runtime_section(session)
            if runtime:
                content = f"{content}\n\n{AGENT_TASK_RUNTIME_HEADING}\n{runtime}"
        system = ModelMessage(role="system", content=content)
        return [system, *transcript, *conversation]

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
        payload = {
            "tool": tool_name,
            "status": result.status,
            "summary": result.summary,
            "output": result.output[:4000],
            "error": result.error,
            "exit_code": result.exit_code,
            "files_changed": result.files_changed,
        }
        return json.dumps(payload, ensure_ascii=False)

    def _summarize_observations(self, conversation: list[ModelMessage]) -> str:
        tool_messages = [msg for msg in conversation if msg.role == "tool"]
        if not tool_messages:
            return "Step completed."
        latest = tool_messages[-1].content
        return f"Step completed after tool execution. Latest observation: {latest[:240]}"

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
