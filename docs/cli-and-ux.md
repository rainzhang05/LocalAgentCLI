# LocalAgentCLI — CLI and UX

This document defines the terminal user interface: visual style, UI elements, input handling, streaming behavior, interrupt handling, and cross-platform UX requirements.

---

## Design Principles

1. **Minimal**: No unnecessary chrome. Every pixel of terminal space serves a purpose.
2. **Terminal-native**: Works in any terminal emulator. No GUI dependencies. Uses standard ANSI escape codes for formatting.
3. **Modern formatting**: Rich text (bold, colors, panels) via a library like `rich` or `prompt_toolkit`. Not plain monochrome.
4. **Consistent across platforms**: The same visual experience on macOS, Linux, and Windows terminals.

---

## UI Elements

### Status Header

A persistent single-line header at the top of the terminal showing current state:

```
LocalAgent | mode: agent | model: codellama-7b (gguf) | workspace: ~/project
```

**Contents:**
- Application name
- Current mode (chat / agent)
- Active model name and backend type (or provider name for remote models)
- Workspace path (abbreviated with `~`)

**Update behavior**: The header updates immediately when mode, model, or workspace changes. It does not scroll with output.

### Prompt Line

The input prompt where the user types:

```
> _
```

**Behavior:**
- Single `>` character followed by a space
- Supports multi-line input (Shift+Enter or `\` continuation)
- History navigation (Up/Down arrows cycle through previous inputs)
- Live slash-command menu for `/` commands. Typing `/` shows all commands below the prompt, typing more characters filters the list, Up/Down selects a command, and Enter accepts it.
- Tab still triggers command completion for users who prefer the traditional terminal workflow.

### Streaming Output

Model responses are rendered token-by-token as they arrive:

```
> explain quicksort

Quicksort is a divide-and-conquer sorting algorithm that works by selecting
a 'pivot' element from the array and partitioning the other elements into
two sub-arrays...█
```

**Rules:**
- Streaming is always enabled. There is no batch-mode output.
- The cursor (block `█`) advances as tokens arrive
- Markdown formatting in model output is rendered in real time (bold, code blocks, lists)
- Code blocks are syntax-highlighted using the detected language

### Reasoning Panel

When the model emits reasoning/thinking tokens, they are displayed in a visually distinct panel above the response:

```
┌─ Reasoning ──────────────────────────────────────────┐
│ The user wants an explanation of quicksort.           │
│ I should cover the algorithm, complexity, and a       │
│ simple example...                                     │
└──────────────────────────────────────────────────────┘

Quicksort is a divide-and-conquer sorting algorithm...
```

**Behavior:**
- The reasoning panel is collapsible (shown by default, can be hidden via config)
- If reasoning is long, the panel scrolls independently of the main output
- Reasoning text is styled differently (dimmed or italic) to distinguish it from the response

### Inline Activity Logs

Tool calls, approvals, and system events are displayed inline between user input and model output:

```
> refactor the auth module

┌─ Plan ───────────────────────────────────────────────┐
│ 1. Read current auth module                          │
│ 2. Identify session-based patterns                   │
│ 3. Replace with JWT implementation                   │
│ 4. Update tests                                      │
└──────────────────────────────────────────────────────┘

  ✓ file_read: src/auth.py (auto-approved)
  ⟳ patch_apply: src/auth.py
    Replace session token logic with JWT...
    [Enter] Approve  |  [d] Deny  |  [v] View diff

```

**Activity log markers:**
- `✓` — completed action (green)
- `⟳` — pending approval (yellow)
- `✗` — failed or denied action (red)
- `ℹ` — informational message (blue)

---

## Input Handling

### Routing Rules

| Input | Action |
|---|---|
| `/command [args]` | Route to Command Router |
| Plain text | Send to model (chat or agent mode) |
| Empty input (just Enter) | Ignored |
| Ctrl+C | Interrupt current operation |
| Ctrl+D | Exit (same as `/exit`) |

### Command Menu and Completion

When the user types `/`:
1. Show all available commands in a menu directly under the prompt
2. Filter the list live as more characters are typed (e.g., `/mo` narrows to `/mode` and `/models`)
3. If a command prefix includes subcommands (e.g., `/models `), show the matching subcommands
4. Up/Down arrows move through the visible options without leaving the input line
5. Enter accepts the highlighted command; Tab also works as an alternate completion key

### Interactive Model Picker

Typing `/models` with no subcommand opens a layered picker backed by the same prompt-toolkit menu system:
1. Choose the local runtime family (`PyTorch / Safetensors`, `MLX` when supported, or `GGUF`)
2. Choose a curated model family (for example `GPT-OSS`, `Qwen`, or `Gemma`)
3. Choose the exact Hugging Face repo from the curated list
4. Start the download immediately and set the installed model as the active local model for the current session

The picker must be keyboard-first:
- Up/Down arrows navigate options
- Typing filters the current layer
- Enter accepts the current choice
- Back/Cancel options are always available inside the picker

### Input History

- Up/Down arrows cycle through previous inputs (both commands and prompts)
- History is per-session and persisted with the session
- Maximum history size: 1000 entries

---

## Streaming Behavior

### Requirements

1. All model output is streamed. There is no configuration to disable streaming.
2. Tokens are rendered as soon as they are received — no buffering.
3. Markdown is rendered progressively. A code block that hasn't been closed yet is still displayed with partial syntax highlighting.
4. If the model is generating and the user scrolls up, generation continues in the background. Scrolling back down resumes live output.

### Streaming Implementation

```python
# localagentcli/shell/streaming.py

class StreamRenderer:
    def __init__(self, console):
        self._console = console
        self._buffer = ""

    def render_chunk(self, chunk: StreamChunk) -> None:
        """Render a single stream chunk to the terminal.

        - Regular text: append and render inline
        - Reasoning text: route to reasoning panel
        - Tool call: route to activity log
        - Done: finalize output (add newline, flush)
        """

    def render_reasoning(self, text: str) -> None:
        """Append text to the reasoning panel."""

    def render_activity(self, event: AgentEvent) -> None:
        """Render an agent event (tool call, approval, etc.) in the activity log."""

    def finalize(self) -> None:
        """Called when generation is complete. Flush buffers, add trailing newline."""
```

---

## Interrupt Handling

### Ctrl+C Behavior

| State | Behavior |
|---|---|
| Idle (waiting for input) | Ignored (standard terminal behavior) |
| Model generating (chat mode) | Stop generation immediately. Display partial output. Return to prompt. |
| Agent executing (agent mode) | Pause agent loop. Display current state. Prompt: "Continue / Stop / Modify?" |
| Tool executing | Kill the tool subprocess. Return timeout/cancelled result to agent. |
| Approval prompt displayed | Cancel the prompt. Treat as deny. |

### Graceful Shutdown

Ctrl+C during generation does not crash the application. It:
1. Sends a cancellation signal to the model/provider
2. Captures any partial output already received
3. Displays the partial output (marked as interrupted)
4. Returns to the input prompt

---

## Error Display

Errors are displayed inline with clear formatting:

```
✗ Error: Model 'nonexistent' not found in registry.
  Available models: codellama-7b, mistral-7b
  Use /models list to see all installed models.
```

**Error format:**
- Red `✗` prefix
- Error type and message on the first line
- Helpful context on subsequent lines (available options, suggestions)
- Never show raw stack traces to the user (log them at debug level)

---

## First-Run Experience

When LocalAgentCLI is launched for the first time (no `config.toml` exists):

1. Display a welcome banner:
```
Welcome to LocalAgent CLI

Let's get you set up. This will only take a moment.
```

2. Run the `/setup` wizard automatically
3. After setup, display a brief usage guide:
```
You're all set! Here's how to get started:

  Just type naturally to start a conversation or task.
  Use /help to see all available commands.
  Use /mode chat for conversation, /mode agent for tasks.

>
```

---

## Suggested Libraries

| Library | Purpose |
|---|---|
| `prompt_toolkit` | Input handling, history, tab completion, key bindings |
| `rich` | Rich text rendering, panels, tables, syntax highlighting, progress bars |
| `click` | CLI entry point and argument parsing (for the `localagentcli` command itself) |

### ShellUI Class

```python
# localagentcli/shell/ui.py

class ShellUI:
    def __init__(self, session: Session, config: ConfigManager):
        self._session = session
        self._config = config
        self._renderer = StreamRenderer(console)
        self._prompt = PromptSession(history=FileHistory(...))

    def run(self) -> None:
        """Main input loop.

        1. Display status header
        2. Show prompt
        3. Read input
        4. Route to command or model
        5. Render output (streaming)
        6. Repeat
        """

    def display_status_header(self) -> None:
        """Render the status header line."""

    def read_input(self) -> str:
        """Read user input with history and tab completion."""

    def handle_interrupt(self) -> None:
        """Handle Ctrl+C based on current state."""
```

---

## Cross-Platform Notes

| Concern | Approach |
|---|---|
| ANSI color support | Use `rich` which auto-detects terminal capabilities |
| Unicode characters (✓, ✗, ⟳) | Fall back to ASCII (`[OK]`, `[FAIL]`, `[...]`) on terminals that don't support Unicode |
| Key bindings | `prompt_toolkit` handles platform differences |
| Terminal width | Auto-detect and adapt layout. Minimum supported width: 80 columns |
| Windows cmd.exe | `rich` enables VT processing on Windows 10+. Legacy terminals get plain text |
