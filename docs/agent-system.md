# LocalAgentCLI — Agent System

This document covers both execution modes (Chat and Agent), the agent loop, planning, and mode switching. For the tools used by agents, see [tool-system.md](tool-system.md). For safety controls during agent execution, see [safety-and-permissions.md](safety-and-permissions.md).

---

## Dual-Mode System

LocalAgentCLI operates in one of two modes at any time. The default mode is **Agent**.

| Aspect | Chat Mode | Agent Mode |
|---|---|---|
| Purpose | Conversational interaction | Autonomous task execution |
| Input handling | Plain text → model → response | Plain text → triage → direct answer or task execution |
| Tool usage | Not invoked automatically | Invoked as part of the agent loop |
| Approval prompts | None (no actions taken) | Yes, per safety rules |
| Context management | Auto-compaction + summaries | Auto-compaction + task state |
| Switch command | `/mode chat` | `/mode agent` |
| Precondition | Any model | Target must report trusted tool-use readiness |

---

## Chat Mode

### Behavior

In chat mode, user input is sent directly to the model as a conversation message. The model responds, and the response is streamed to the terminal. No tools are invoked. No plans are generated.

### Features

1. **Streaming responses**: All output is streamed token-by-token. There is no batch mode.
2. **Session history**: The full conversation history is maintained and sent with each request (subject to context limits).
3. **Context auto-compaction**: When estimated context use approaches the effective window (including a reserved slice for the next model reply), older messages are automatically summarized. The summary replaces the original messages while preserving key information. Details: [session-and-config.md](session-and-config.md#context-management).
4. **Pinned instructions**: Users can pin system-level instructions that survive compaction. Pinned instructions are always included at the top of the context.
5. **Repository instructions**: If the active workspace belongs to a repository whose root contains `AGENTS.md`, that file is automatically loaded and prepended to the system prompt as the default repository instruction set.
6. **Secondary output display**: If the model emits reasoning/thinking, tool-call metadata, provider notifications, or similar secondary events, they are rendered separately from the primary assistant response and preserved in session metadata.
7. **Summaries**: The system can generate a summary of the conversation on demand or automatically at session save.

### ChatController

```python
# localagentcli/agents/chat.py

class ChatController:
    def __init__(self, model: ModelAbstractionLayer, session: Session):
        self._model = model
        self._session = session

    def handle_input(self, user_input: str) -> Iterator[StreamChunk]:
        """Process user input in chat mode.

        1. Append user message to session history
        2. Check if compaction is needed; compact if so
        3. Build message list (pinned instructions + history)
        4. Call model.stream_generate()
        5. Yield chunks to caller
        6. Append assistant response to session history
        """

    def compact_if_needed(self) -> None:
        """Check token count against context limit. Summarize if needed."""

    def pin_instruction(self, instruction: str) -> None:
        """Add a pinned instruction that persists across compaction."""

    def unpin_instruction(self, index: int) -> None:
        """Remove a pinned instruction by index."""
```

---

## Agent Mode

### Behavior

In agent mode, user input first flows through the shared runtime submission/event layer and then through an internal triage pass that uses the full effective context: pinned instructions, repository instructions from `AGENTS.md`, compacted history, and recent messages. The triage result determines one of three execution paths:

1. **`direct_answer`**: simple factual or explanatory prompts skip planning and are answered immediately through the normal model stream
2. **`single_step_task`**: one concrete action gets a single synthesized step and executes without a separate planner round-trip
3. **`multi_step_task`**: complex or staged work starts with an adaptive bootstrap step and uses the full iterative agent loop

The selected route is surfaced immediately in the shell activity stream and persisted in session metadata so the prompt toolbar and `/status` can describe the current or last task without re-parsing history.

When recent-history windowing trims long transcripts during triage/planning, the runtime preserves a leading system context message so workspace instructions and environment context are not accidentally dropped from model-visible input.

The loop continues until the task is complete, fails, or the user intervenes.

### Entry Requirements

- Local targets can enter agent mode only when the current readiness assessment still reports trusted tool use
- Remote targets can enter agent mode only when `tool_use` is supported and its tier is `verified`, `inferred`, or `configured`
- Remote targets in `legacy fallback` or `unknown` state are rejected until discovery is refreshed and the user reselects a live model
- Rejections include readiness posture (`ready`, `degraded`, `blocked`), tradeoff
    messaging (`chat available` vs `agent blocked`), the tool-use verdict/tier,
    and concrete next steps instead of only a raw boolean failure

### Core Principles

1. **Adaptive planning**: Trivial requests in agent mode do not incur planning overhead. Planned execution paths start from a local bootstrap plan (no dedicated model planning round-trip before first execution).
2. **Multi-step execution**: Tasks are broken into discrete steps. Each step may involve one or more tool calls.
3. **Iterative reasoning**: After each step, the agent reasons about the result and decides the next action. This reasoning is visible to the user through the same dimmed `Details` lane used by chat-mode secondary output.
4. **Subtask decomposition**: Complex tasks are broken into smaller subtasks. Each subtask has its own mini-plan.
5. **Repository defaults honored**: When `AGENTS.md` is present at the active repository root, its contents are included automatically alongside user-pinned instructions for planning and execution.
6. **Task-state visibility**: Agent route, phase, current step, pending approval, wait reason, retry count, last error, approval mode, and rollback availability are persisted in `session.metadata["agent_task_state"]` and reused by the prompt toolbar and `/status`.
7. **Model-aware generation profiles**: Triage, planning, and step execution each use phase-specific generation profiles derived from shared config and `ModelInfo.default_max_tokens`; this avoids hardcoded loop token limits and keeps controller and loop behavior aligned.
8. **Structured step briefings**: Step execution prompts use a fixed structure (execution rules, output contract, task objective, plan status, current step focus) before layered system/session context, reducing ambiguity during long tool-using turns.
9. **Model-adapted tool exposure**: Per-round tool definitions are filtered by active `ModelInfo` (tool-use capability gates, required capability tags, minimum token-budget thresholds) before the model receives tool schemas.
10. **Unified turn loop budget**: Each step can span multiple model↔tool rounds in one continuous execution loop up to a configurable round budget, reducing dependence on separate replanning calls for in-step progress.
11. **Failure classification in unified loops**: When repeated model errors or tool failures hit the retry threshold first, the loop reports an explicit threshold failure reason; round-budget exhaustion is reserved for steps that neither converge nor trip a retry threshold.

### Runtime Phase Contract

Planned agent work carries one visible phase at a time. The current phase is rendered in the activity stream and persisted with the task snapshot.

| Phase | Meaning |
|---|---|
| `planning` | The controller is triaging or building the initial task plan |
| `executing` | The agent is actively running or streaming the next step |
| `waiting_approval` | A tool call is paused for explicit approval |
| `retrying` | The loop is retrying the current step after a model or tool failure |
| `replanning` | The planner is revising the remaining plan after a denial or repeated failure |
| `recovering` | The loop is handling a blocked, denied, cancelled, or failed tool result before continuing |
| `stopped` | The task ended because the user explicitly stopped it |
| `timed_out` | The task ended because the inactivity timeout fired |
| `completed` | The task finished successfully |
| `failed` | The task exhausted recovery and terminated unsuccessfully |

### Agent Loop

```
┌──────────────────┐
│  Understand Task │  ← Parse user input, gather context, triage
└────────┬─────────┘
         ▼
┌──────────────────┐
│  Generate Plan   │  ← Only when triage requires planned execution
└────────┬─────────┘
         ▼
┌──────────────────┐
│  Execute Step    │  ← Call tools (with safety approval)
└────────┬─────────┘
         ▼
┌──────────────────┐
│ Observe Results  │  ← Read tool output, check for errors
└────────┬─────────┘
         ▼
┌──────────────────┐
│  Update Plan     │  ← Adjust remaining steps based on results
└────────┬─────────┘
         │
         ├──── Task complete? ──→ Done (report results)
         │
         └──── More steps? ──→ Back to "Execute Step"
```

### Detailed Loop Steps

#### 1. Understand Task
- The agent receives the user's input text
- It builds the effective model context from pinned instructions, repository instructions, compacted history, and recent turns
- It classifies the request as `direct_answer`, `single_step_task`, or `multi_step_task`
- The chosen route is recorded in session metadata before execution begins so the shell can surface it immediately
- It examines the current workspace state only when the resulting task path requires it

#### 2. Generate Plan
- `single_step_task`: the controller synthesizes one executable bootstrap step locally and begins execution immediately
- `multi_step_task`: the controller also synthesizes a bootstrap execution step; the model plans/refines adaptively while executing tools in-step
- Planner model calls are reserved for recovery/replanning paths after denials or repeated failures
- Each active step still describes the action focus and expected outcome
- The visible phase moves from `planning` to `executing` once the plan is ready
- The plan is displayed before or as execution begins
- There is no separate plan-review pause; only tool approvals can pause execution
- Before an approval prompt is shown, the renderer flushes any pending secondary detail so reasoning and warnings do not appear mid-prompt

#### 3. Execute Step
- The agent selects the next step from the plan
- It constructs the tool call(s) needed for the step
- Tool definitions sent to the model are adapted for the active model capabilities/token budget before each round
- Tool calls are routed through the Safety Layer for approval
- If approval is required, the visible phase becomes `waiting_approval` until the user approves, denies, or cancels the prompt
- The tool executes and returns a `ToolResult`
- Multiple tools may be batched in a single step if they are independent
- When every call in a multi-call batch is read-only and auto-approved, those calls may run in parallel; `ToolCallRequested` events are still emitted in order before any `ToolCallResult`, and tool messages keep the same `tool_call_id` ordering the model supplied

#### 4. Observe Results
- The agent examines the tool output
- It checks for errors or unexpected results
- The observation is displayed inline in the activity log
- Successful modifying actions update rollback history immediately, and the shell surfaces a concise undo affordance once that history exists

#### 5. Update Plan
- Based on the observation, the agent may:
  - Proceed to the next step (no changes)
  - Modify remaining steps
  - Add new steps
  - Retry the current step (with modifications)
  - Abort the task (with explanation)
- Recovery after denials, blocked actions, cancelled tools, and timeouts is surfaced as `recovering`
- Revisions to the remaining plan are surfaced as `replanning` when replanning is enabled for the loop mode
- Plan updates are displayed to the user

### Agent Capabilities

| Capability | Description |
|---|---|
| Batching | Multiple independent tool calls in a single step |
| Dynamic planning | Plan is updated after each step based on observations; planner calls are recovery-focused |
| Model-controlled retries | The model decides when and how to retry failed steps |
| No fixed step limit | There is no hard limit on the number of steps. System safeguards (timeout, user interrupt) apply instead |
| Subtask decomposition | Complex steps are broken into smaller sub-steps |
| Direct-answer fast path | Simple prompts in agent mode can bypass planning entirely while still using full session context |
| Task-state persistence | Route, phase, step, pending tool, approval mode, and rollback count are persisted for the toolbar and `/status` |
| No separate initial planner turn | First execution starts from a local bootstrap step; planner model calls are deferred to recovery/replanning |
| Unified multi-round step execution | A single step can run multiple model/tool rounds before finalizing, bounded by a configurable round limit |

### AgentController

```python
# localagentcli/agents/controller.py

class AgentController:
    def __init__(self, model: ModelAbstractionLayer, session: Session,
                 tool_registry: ToolRegistry, safety: SafetyLayer):
        self._model = model
        self._session = session
        self._tools = tool_registry
        self._safety = safety
        self._loop = AgentLoop(model, tool_registry, safety)

    def dispatch_input(self, task_input: str) -> AgentDispatch:
        """Triage one plain-text input and return the correct execution path.

        - direct_answer -> stream chunks immediately
        - single_step_task -> create a one-step TaskPlan locally
        - multi_step_task -> start the planner + agent loop
        """

    def handle_task(self, task_input: str) -> Iterator[AgentEvent]:
        """Compatibility wrapper for planned execution paths.

        Yields AgentEvent objects for the UI to render:
        - TaskRouted: triage route selected
        - PhaseChanged: high-level state transition
        - PlanGenerated: initial plan
        - StepStarted: beginning a step
        - ToolCallRequested: tool call pending approval
        - ToolCallResult: tool output
        - PlanUpdated: plan was modified
        - TaskComplete: task finished
        - TaskStopped: task stopped by user or cancelled prompt
        - TaskTimedOut: inactivity timeout fired
        - TaskFailed: task could not be completed
        """

    def stop(self) -> None:
        """Stop the running agent loop and record a non-failure stop state."""

    def approve_action(self) -> None:
        """Approve the pending tool call."""

    def deny_action(self) -> None:
        """Deny the pending tool call. Agent enters recovery and may re-plan."""
```

### AgentLoop

```python
# localagentcli/agents/loop.py

class AgentLoop:
    def __init__(self, model: ModelAbstractionLayer,
                 tools: ToolRegistry, safety: SafetyLayer):
        self._model = model
        self._tools = tools
        self._safety = safety

    def run(
        self,
        task: str,
        context: list[ModelMessage],
        ...,
        session: Session | None = None,
    ) -> Iterator[AgentEvent]:
        """Execute the agent loop until completion or interruption.

        1. Receive a synthesized or planned TaskPlan
        2. Send task + context + current step to the model with tool definitions
        3. If tool call: route through safety, execute, observe
        4. Feed observations back to the model
        5. Repeat until the plan completes or fails
        """
```

**Runtime task status in step prompts:** When `AgentController` runs the loop, it passes the active `Session`. For each step, the first system message includes the usual task/plan/step instructions plus an **Agent task status (runtime):** block when `session.metadata["agent_task_state"]` exists, `session.mode == "agent"`, and the recorded task is **active**. The block lists fields such as route, phase, step index and description, pending tool (if any), approval mode, rollback count, and a truncated summary so the model sees up-to-date execution state alongside the transcript. Formatting lives in `localagentcli/session/task_context.py`.

The step system message also merges any system-role context already present in the transcript (for example repository instructions and environment context produced by shared conversation assembly). If that upstream system context is absent, the loop falls back to session-derived workspace/pinned instructions plus a freshly generated `<environment_context>` block.

Independent of transcript/session layering, each step now starts with a deterministic execution brief:
- execution rules (tool-first, no hallucinated outputs, minimal reversible edits)
- output contract (continue with tools until done; final response is concise plain text)
- task objective / plan status / current step focus sections

Tool observations injected back into the loop now use model-aware adaptive truncation (instead of a fixed byte slice), preserving both prefix/suffix context and explicit truncation metadata (`output_truncated`, original and retained character counts).

### TaskPlan

```python
# localagentcli/agents/planner.py

@dataclass
class PlanStep:
    index: int
    description: str
    status: str  # "pending", "in_progress", "completed", "failed", "skipped"
    tool_calls: list[dict] | None = None
    result: str | None = None

@dataclass
class TaskPlan:
    task: str
    steps: list[PlanStep]
    status: str  # "planning", "executing", "completed", "failed", "stopped", "timed_out"

    def next_step(self) -> PlanStep | None:
        """Return the next pending step, or None if all are done."""

    def update_step(self, index: int, status: str, result: str) -> None:
        """Update a step's status and result."""

    def add_step(self, description: str, after_index: int | None = None) -> None:
        """Insert a new step into the plan."""

    def remove_step(self, index: int) -> None:
        """Remove a step from the plan."""
```

---

## Agent Events

The agent loop communicates with the Shell UI via `AgentEvent` objects:

```python
@dataclass
class AgentEvent:
    type: str  # Event type identifier

class TaskRouted(AgentEvent):
    route: str  # "direct_answer" | "single_step_task" | "multi_step_task"
    reason: str

class PhaseChanged(AgentEvent):
    phase: str
    summary: str
    step_index: int | None
    step_description: str | None

class PlanGenerated(AgentEvent):
    plan: TaskPlan

class StepStarted(AgentEvent):
    step: PlanStep

class ToolCallRequested(AgentEvent):
    tool_name: str
    arguments: dict
    requires_approval: bool
    risk_reason: str | None
    rollback_summary: str | None

class ToolCallResult(AgentEvent):
    tool_name: str
    result: ToolResult
    rollback_entries: int

class ReasoningOutput(AgentEvent):
    text: str

class PlanUpdated(AgentEvent):
    plan: TaskPlan
    changes: str  # Description of what changed

class TaskComplete(AgentEvent):
    summary: str
    plan: TaskPlan

class TaskStopped(AgentEvent):
    reason: str
    plan: TaskPlan | None

class TaskTimedOut(AgentEvent):
    reason: str
    plan: TaskPlan

class TaskFailed(AgentEvent):
    reason: str
    plan: TaskPlan
```

Rendering rules:
- `TaskRouted`, `PhaseChanged`, `PlanGenerated`, `PlanUpdated`, `StepStarted`, and `ToolCallResult` use the shared status/activity grammar in the shell renderer
- `ReasoningOutput` is treated as secondary detail and flows into the dimmed `Details` lane instead of a dedicated reasoning panel
- `ToolCallRequested` renders a concise inline summary in the primary lane, while risk reasons, rollback availability, and supporting warnings are shown through the quieter `Details` lane before approval is requested
- Successful file-modifying `ToolCallResult` events can surface `Undo available: N change(s). Use /agent undo.` without interrupting the main flow
- `TaskComplete` renders a quiet success line followed by the final summary body
- `TaskStopped` and `TaskTimedOut` render warning-style non-failure outcomes
- `TaskFailed` renders a failure line

Direct-answer fast-path responses are not wrapped in `AgentEvent` objects. They stream normalized `StreamChunk` events directly, but the controller still records route and phase in `agent_task_state` before streaming starts and persists the final response with metadata marking `agent_task="direct_answer"` and `fast_path=True`.

---

## Mode Switching

### `/mode chat`
- Switches to chat mode immediately
- If an agent task is running, it is stopped first (with confirmation)
- Session history is preserved

### `/mode agent`
- Switches to agent mode
- **Precondition check**: Validates `tool_use` on the active target. For remote providers, this check is based on the selected remote model id discovered from the provider's model list, not just the provider type. If the active target lacks tool support:
  - Refuses the switch
  - Displays: `"Cannot enter agent mode: the active model ({model_name}) does not support tool use. Use /set to switch to a tool-capable target."`
- Session history is preserved

---

## System Safeguards

Even though there is no fixed step limit, the system protects against runaway agents:

1. **User interrupt (Ctrl+C)**: Immediately stops the current task and returns control to the prompt.
2. **Inactivity timeout**: If the agent has not made progress for a configurable duration, the task ends with `TaskTimedOut` and a clear warning.
3. **Resource limits**: If a single tool call exceeds resource limits (e.g., shell command timeout), it is killed and the agent is notified.
4. **Error accumulation**: If the agent encounters repeated errors (configurable threshold, default 5 consecutive failures), it enters recovery and may replan around the failure. In unified-loop execution, repeated model/tool failures now terminate with explicit threshold reasons, while separate round-budget exhaustion remains a distinct failure path.
