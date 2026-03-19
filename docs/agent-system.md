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
| Precondition | Any model | Model must support tool use |

---

## Chat Mode

### Behavior

In chat mode, user input is sent directly to the model as a conversation message. The model responds, and the response is streamed to the terminal. No tools are invoked. No plans are generated.

### Features

1. **Streaming responses**: All output is streamed token-by-token. There is no batch mode.
2. **Session history**: The full conversation history is maintained and sent with each request (subject to context limits).
3. **Context auto-compaction**: When conversation history approaches the model's context window, older messages are automatically summarized. The summary replaces the original messages while preserving key information.
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

In agent mode, user input first goes through an internal triage pass that uses the full effective context: pinned instructions, repository instructions from `AGENTS.md`, compacted history, and recent messages. The triage result determines one of three execution paths:

1. **`direct_answer`**: simple factual or explanatory prompts skip planning and are answered immediately through the normal model stream
2. **`single_step_task`**: one concrete action gets a single synthesized step and executes without a separate planner round-trip
3. **`multi_step_task`**: complex or staged work uses the planner and full iterative agent loop

The loop continues until the task is complete, fails, or the user intervenes.

### Core Principles

1. **Adaptive planning**: Trivial requests in agent mode do not incur planning overhead. Plans are shown only for `single_step_task` and `multi_step_task` paths.
2. **Multi-step execution**: Tasks are broken into discrete steps. Each step may involve one or more tool calls.
3. **Iterative reasoning**: After each step, the agent reasons about the result and decides the next action. This reasoning is visible to the user.
4. **Subtask decomposition**: Complex tasks are broken into smaller subtasks. Each subtask has its own mini-plan.
5. **Repository defaults honored**: When `AGENTS.md` is present at the active repository root, its contents are included automatically alongside user-pinned instructions for planning and execution.

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
- It examines the current workspace state only when the resulting task path requires it

#### 2. Generate Plan
- `single_step_task`: the controller synthesizes one executable step locally and begins execution immediately
- `multi_step_task`: the planner generates the minimum number of structured steps needed for the task
- Each step describes the action, the tool(s) to use, and the expected outcome
- The plan is displayed before or as execution begins
- There is no separate plan-review pause; only tool approvals can pause execution

#### 3. Execute Step
- The agent selects the next step from the plan
- It constructs the tool call(s) needed for the step
- Tool calls are routed through the Safety Layer for approval
- The tool executes and returns a `ToolResult`
- Multiple tools may be batched in a single step if they are independent

#### 4. Observe Results
- The agent examines the tool output
- It checks for errors or unexpected results
- The observation is displayed inline in the activity log

#### 5. Update Plan
- Based on the observation, the agent may:
  - Proceed to the next step (no changes)
  - Modify remaining steps
  - Add new steps
  - Retry the current step (with modifications)
  - Abort the task (with explanation)
- Plan updates are displayed to the user

### Agent Capabilities

| Capability | Description |
|---|---|
| Batching | Multiple independent tool calls in a single step |
| Dynamic planning | Plan is updated after each step based on observations |
| Model-controlled retries | The model decides when and how to retry failed steps |
| No fixed step limit | There is no hard limit on the number of steps. System safeguards (timeout, user interrupt) apply instead |
| Subtask decomposition | Complex steps are broken into smaller sub-steps |
| Direct-answer fast path | Simple prompts in agent mode can bypass planning entirely while still using full session context |

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
        - PlanGenerated: initial plan
        - StepStarted: beginning a step
        - ToolCallRequested: tool call pending approval
        - ToolCallResult: tool output
        - PlanUpdated: plan was modified
        - TaskComplete: task finished
        - TaskFailed: task could not be completed
        """

    def stop(self) -> None:
        """Stop the running agent loop. Preserves current state."""

    def approve_action(self) -> None:
        """Approve the pending tool call."""

    def deny_action(self) -> None:
        """Deny the pending tool call. Agent re-plans."""
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

    def run(self, task: str, context: list[Message]) -> Iterator[AgentEvent]:
        """Execute the agent loop until completion or interruption.

        1. Receive a synthesized or planned TaskPlan
        2. Send task + context + current step to the model with tool definitions
        3. If tool call: route through safety, execute, observe
        4. Feed observations back to the model
        5. Repeat until the plan completes or fails
        """
```

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
    status: str  # "planning", "executing", "completed", "failed"

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

class PlanGenerated(AgentEvent):
    plan: TaskPlan

class StepStarted(AgentEvent):
    step: PlanStep

class ToolCallRequested(AgentEvent):
    tool_name: str
    arguments: dict
    requires_approval: bool

class ToolCallResult(AgentEvent):
    tool_name: str
    result: ToolResult

class ReasoningOutput(AgentEvent):
    text: str

class PlanUpdated(AgentEvent):
    plan: TaskPlan
    changes: str  # Description of what changed

class TaskComplete(AgentEvent):
    summary: str
    plan: TaskPlan

class TaskFailed(AgentEvent):
    reason: str
    plan: TaskPlan
```

Direct-answer fast-path responses are not wrapped in `AgentEvent` objects. They stream normalized `StreamChunk` events directly and are persisted in session history with metadata marking the response as `agent_task="direct_answer"` and `fast_path=True`.

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
2. **Inactivity timeout**: If the agent has not made progress for a configurable duration, the task fails with a clear notification.
3. **Resource limits**: If a single tool call exceeds resource limits (e.g., shell command timeout), it is killed and the agent is notified.
4. **Error accumulation**: If the agent encounters repeated errors (configurable threshold, default 5 consecutive failures), it replans once around the failure and eventually terminates with `TaskFailed` if progress still cannot be made.
