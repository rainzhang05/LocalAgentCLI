# LocalAgentCLI — Agent System

This document covers both execution modes (Chat and Agent), the agent loop, planning, and mode switching. For the tools used by agents, see [tool-system.md](tool-system.md). For safety controls during agent execution, see [safety-and-permissions.md](safety-and-permissions.md).

---

## Dual-Mode System

LocalAgentCLI operates in one of two modes at any time. The default mode is **Agent**.

| Aspect | Chat Mode | Agent Mode |
|---|---|---|
| Purpose | Conversational interaction | Autonomous task execution |
| Input handling | Plain text → model → response | Plain text → task → plan → execute |
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
5. **Reasoning display**: If the model emits reasoning/thinking tokens, they are displayed in a scrollable panel above the response. This panel is visually distinct from the response output.
6. **Summaries**: The system can generate a summary of the conversation on demand or automatically at session save.

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

In agent mode, user input is interpreted as a **task**. The agent analyzes the task, generates a plan, and executes it step-by-step using tools. Each step's result is observed and used to update the plan. The loop continues until the task is complete or the user intervenes.

### Core Principles

1. **Explicit plan shown**: Before executing, the agent displays its plan to the user. The plan shows the high-level steps the agent intends to take.
2. **Multi-step execution**: Tasks are broken into discrete steps. Each step may involve one or more tool calls.
3. **Iterative reasoning**: After each step, the agent reasons about the result and decides the next action. This reasoning is visible to the user.
4. **Subtask decomposition**: Complex tasks are broken into smaller subtasks. Each subtask has its own mini-plan.

### Agent Loop

```
┌──────────────────┐
│  Understand Task │  ← Parse user input, gather context
└────────┬─────────┘
         ▼
┌──────────────────┐
│  Generate Plan   │  ← Break task into steps
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
- It examines the current workspace state (file listing, git status) if relevant
- It determines what the user wants to accomplish

#### 2. Generate Plan
- The model generates a structured plan with numbered steps
- Each step describes the action, the tool(s) to use, and the expected outcome
- The plan is displayed to the user before execution begins
- The user can approve, modify, or reject the plan

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

    def handle_task(self, task_input: str) -> Iterator[AgentEvent]:
        """Process a task in agent mode.

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

        1. Send task + context to model with tool definitions
        2. Model responds with plan or tool calls
        3. If tool call: route through safety, execute, observe
        4. Feed observation back to model
        5. Repeat until model indicates completion
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

---

## Mode Switching

### `/mode chat`
- Switches to chat mode immediately
- If an agent task is running, it is stopped first (with confirmation)
- Session history is preserved

### `/mode agent`
- Switches to agent mode
- **Precondition check**: Calls `model.supports_tools()`. If `False`:
  - Refuses the switch
  - Displays: `"Cannot enter agent mode: the active model ({model_name}) does not support tool use. Use /models use <name> to switch to a tool-capable model."`
- Session history is preserved

---

## System Safeguards

Even though there is no fixed step limit, the system protects against runaway agents:

1. **User interrupt (Ctrl+C)**: Immediately pauses the agent loop. The user can review state and choose to continue, modify, or stop.
2. **Inactivity timeout**: If the agent has not made progress (no tool calls, no new reasoning) for a configurable duration, it is paused with a notification.
3. **Resource limits**: If a single tool call exceeds resource limits (e.g., shell command timeout), it is killed and the agent is notified.
4. **Error accumulation**: If the agent encounters repeated errors (configurable threshold, default 5 consecutive failures), it pauses and asks the user for guidance.
