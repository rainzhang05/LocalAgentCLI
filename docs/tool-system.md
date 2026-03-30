# LocalAgentCLI — Tool System

This document defines the tool system: the tools available to agents, their behavior, output schema, registration pattern, and implementation guidance. For how tools integrate with the agent loop, see [agent-system.md](agent-system.md). For approval rules around tool execution, see [safety-and-permissions.md](safety-and-permissions.md). For MCP stdio servers and discovered tools, see [mcp.md](mcp.md).

---

## Overview

Tools are discrete operations that the agent can invoke during task execution. Each tool performs a specific action (read a file, run a command, apply a patch) and returns a structured result. Tools are the primary way agents interact with the filesystem, shell, and version control.

All tool executions are routed through the Safety Layer before running.

The runtime now builds turn-scoped tool inventory through `ToolRouter`, which
can merge:
- built-in Python tools
- callback-backed dynamic tools
- MCP-backed tools discovered from configured `mcp_servers` (see [mcp.md](mcp.md))

When `features.mcp_tool_inventory_refresh = true`, agent dispatch refreshes the
active tool router at turn boundaries so newly available MCP tools can be
picked up without restarting the process.

When `features.multi_agent_path_routing = true`, runtime also registers a
feature-gated baseline multi-agent dynamic tool surface:
- `spawn_agent` (spawn a path-addressable sub-agent under `/root/...`)
- `send_input` (queue additional input for a target path)
- `wait_agent` (wait for one-or-more targets to reach final status)
- `close_agent` (close a target agent and report previous status)
- `resume_agent` (resume a closed target, optionally with immediate input)

This baseline uses validated path semantics (`AgentPath`) and a shared
reference resolver (`resolve_agent_reference(...)`) for relative/absolute
target addressing. It is intentionally feature-gated and incrementally scoped.

When SQLite session persistence is enabled, active-agent snapshots are also
stored in a dedicated session table and restored on session load. Rehydrated
entries are metadata-only: non-final in-flight states are normalized to
`shutdown` until explicitly resumed (for example via `resume_agent`) so runtime
never pretends old worker threads are still live after restart.

### Parameter schema rules

Every tool’s `parameters_schema` must follow a small JSON Schema subset checked by `localagentcli/tools/schema.py` before the model sees the tool:

- Top-level `"type"` must be the string `"object"`.
- `"properties"`, if present, maps argument names to objects that each include a non-empty string `"type"` (for example `"string"`, `"integer"`, `"boolean"`).
- `"required"`, if present, must be a list of strings and every name must exist in `"properties"`.

`Tool.definition()` validates once per instance and raises `ValueError` if the schema is invalid. `ToolRouter.register_dynamic_tool` rejects invalid `DynamicToolSpec.parameters_schema` at registration time with the same rules.

### Parallel read-only batches

When the model returns **two or more** tool calls in one assistant turn, `AgentLoop` may run them concurrently **only if every call** in that batch is read-only (`Tool.is_read_only` is true), passes safety checks without requiring approval, and is not blocked. In that case the loop emits all `ToolCallRequested` events first (in model call order), then executes tools on a bounded thread pool, then emits `ToolCallResult` events and `role="tool"` messages in the same order. If any call in the batch is not eligible (for example a write tool, an unknown tool, a parse error, or a call that needs approval including high-risk reads), the **entire** batch is handled sequentially with the usual interleaved request, execute, and result flow.

### Model-aware tool adaptation

Before each model round, tool definitions are adapted using active `ModelInfo`:

- if `capabilities.tool_use` is explicitly `False`, no tools are exposed
- tools may declare `required_model_capabilities` (for example `("reasoning",)`) and are hidden when missing
- tools may declare `minimum_model_default_max_tokens` and are hidden for small-budget models

This adaptation is applied by `ToolRegistry.get_tool_definitions(model_info=...)` and `ToolRouter.get_tool_definitions(model_info=...)`.

---

## Core Tools

### File Operations

#### `file_read`
- **Purpose**: Read the contents of a file
- **Arguments**: `path` (str, required), `offset` (int, optional — line to start from), `limit` (int, optional — max lines to read)
- **Behavior**: Reads the file at `path` relative to the workspace root. Returns the file contents as text. If `offset`/`limit` are provided, returns only that range.
- **Safety**: Auto-approved (read-only)
- **Errors**: File not found, permission denied, binary file (returns size instead of content)

#### `file_search`
- **Purpose**: Search for files matching a pattern or containing specific text
- **Arguments**: `pattern` (str, required — glob or regex), `path` (str, optional — directory to search in, defaults to workspace root), `content_pattern` (str, optional — search file contents)
- **Behavior**: If only `pattern` is given, performs a glob-style file name search. If `content_pattern` is given, searches file contents using regex.
- **Safety**: Auto-approved (read-only)
- **Returns**: List of matching file paths with optional line matches

#### `directory_list`
- **Purpose**: List contents of a directory
- **Arguments**: `path` (str, required), `recursive` (bool, optional — default false)
- **Behavior**: Lists files and directories at the given path. If `recursive` is true, lists the full tree. Shows file sizes and types.
- **Safety**: Auto-approved (read-only)

### File Modification

#### `file_write`
- **Purpose**: Create or overwrite a file
- **Arguments**: `path` (str, required), `content` (str, required)
- **Behavior**: Writes `content` to the file at `path`. Creates parent directories if needed. If the file exists, it is overwritten entirely.
- **Safety**: Requires approval (modifies filesystem)
- **Pre-action**: Safety Layer creates a backup of the existing file (if any) before writing

#### `patch_apply`
- **Purpose**: Apply a targeted edit to a file
- **Arguments**:
    - `path` (str, required)
    - `patch` (str, optional) — diff-style operations using `@@` anchors and `-`/`+` lines
    - `old_text`/`new_text` (str, optional compatibility mode)
- **Behavior**:
    - Patch mode parses one or more operations, resolves optional anchors, applies context-aware replacement, and tolerates indentation-only mismatches by reindenting replacement lines against the matched region.
    - Legacy mode keeps exact single-match replacement semantics for `old_text`/`new_text`.
    - Ambiguous matches still error explicitly.
- **Safety**: Requires approval (modifies filesystem)
- **Pre-action**: Safety Layer creates a backup before patching
- **Preferred over `file_write`**: For edits to existing files, `patch_apply` is preferred because it shows exactly what changes and is less error-prone than full overwrites.

### File Editing Strategy

The system uses a hybrid approach:
1. **Patch preferred**: For modifying existing files, `patch_apply` is the default. It produces a clear diff and reduces the chance of accidentally overwriting unrelated content.
2. **Fallback to overwrite**: If a patch cannot be applied cleanly (ambiguous match, complex restructuring), `file_write` is used as a fallback. The agent should explain why a full overwrite is needed.

### Shell Execution

#### `shell_execute`
- **Purpose**: Run a shell command
- **Arguments**: `command` (str, required), `timeout` (int, optional — seconds, default from config), `working_dir` (str, optional — defaults to workspace root)
- **Behavior**: Executes commands through an `ExecProcess` abstraction. The default `LocalExecProcess` runs a subprocess within the workspace. On POSIX, command I/O is captured via PTY with incremental polling and bounded output buffering; on non-POSIX hosts, a subprocess fallback is used. A `RemoteExecProcess` seam is available for future delegated execution paths while preserving the same `ToolResult` contract. Runtime also supports optional process wrappers via `safety.os_sandbox_backend` (`off`, `auto`, `macos-seatbelt`, `linux-bwrap`, `container-docker`) plus typed policy override controls (`safety.sandbox_network_access`, `safety.sandbox_writable_roots`, and container image/resource settings). `auto` falls back to local execution when backend binaries are unavailable, while explicit backend selection fails setup if unavailable.
- **Safety**: Requires approval (executes arbitrary commands)
- **Workspace constraint**: The command runs with `working_dir` set to the workspace root (or specified directory). The Safety Layer may reject commands that attempt to escape the workspace.
- **Timeout**: Commands are killed if they exceed the timeout. The agent is notified of the timeout.

#### `test_execute`
- **Purpose**: Run tests using a detected or specified framework
- **Arguments**: `framework` (str, optional — auto-detect if not provided), `path` (str, optional — specific test file or directory), `args` (str, optional — additional arguments)
- **Behavior**: Detects the test framework based on project files:
  - `pytest.ini`, `setup.cfg` with `[tool:pytest]`, or `pyproject.toml` with `[tool.pytest]` → `pytest`
  - `package.json` with `scripts.test` → `npm test`
  - `Cargo.toml` → `cargo test`
  - `go.mod` → `go test ./...`
  - Falls back to user-specified framework
- **Safety**: Requires approval (executes commands)
- **Output**: Parsed test results when possible (pass/fail counts, failed test names)

#### `python_repl_execute`
- **Purpose**: Execute Python code snippets for computational/transform tasks
- **Arguments**: `code` (str, required), `timeout` (int, optional — seconds, default 30)
- **Behavior**: Runs `python -c <code>` in a subprocess rooted at the workspace and returns combined stdout/stderr plus exit code.
- **Safety**: Requires approval (executes arbitrary code)
- **Design note**: This is an intentional Phase 14 baseline divergence from Codex's V8 embedding approach; LocalAgentCLI uses Python-native subprocess execution for now.

### Git Operations

#### `git_status`
- **Purpose**: Show git status of the workspace
- **Arguments**: None
- **Behavior**: Runs `git status` in the workspace root. Returns parsed status (staged, unstaged, untracked files).
- **Safety**: Auto-approved (read-only)

#### `git_diff`
- **Purpose**: Show git diff
- **Arguments**: `staged` (bool, optional — default false), `path` (str, optional — specific file)
- **Behavior**: Runs `git diff` (or `git diff --staged`). Returns the diff output.
- **Safety**: Auto-approved (read-only)

#### `git_commit`
- **Purpose**: Create a git commit
- **Arguments**: `message` (str, required), `files` (list[str], optional — specific files to stage; if empty, commits all staged changes)
- **Behavior**: Stages the specified files (or uses current staging), creates a commit with the given message.
- **Safety**: Requires approval (modifies git history)

---

## Tool Output Schema

Every tool returns a `ToolResult` with this structure:

```python
@dataclass
class ToolResult:
    status: str          # "success" | "error" | "timeout" | "denied"
    summary: str         # One-line human-readable summary
    output: str          # Full output text
    error: str | None    # Error message if status != "success"
    exit_code: int | None  # For shell/test tools
    files_changed: list[str]  # Paths of files modified by this tool
    duration: float      # Execution time in seconds
```

### Status Values

| Status | Meaning |
|---|---|
| `success` | Tool completed successfully |
| `error` | Tool encountered an error during execution |
| `timeout` | Tool exceeded its timeout limit |
| `denied` | User denied the approval request |

---

## Tool Registration

Built-in tools are still registered through `ToolRegistry`, but runtime-facing
surfaces now assemble the final per-turn inventory through `ToolRouter`. The
router provides tool metadata to the model and dispatches tool calls to the
correct built-in, dynamic, or MCP-backed implementation.

### ToolRegistry

```python
# localagentcli/tools/registry.py

class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """Register a tool instance."""
        self._tools[tool.name] = tool

    def get_tool(self, name: str) -> Tool | None:
        """Retrieve a tool by name."""
        return self._tools.get(name)

    def list_tools(self) -> list[Tool]:
        """Return all registered tools."""
        return list(self._tools.values())

    def get_tool_definitions(self, model_info: ModelInfo | None = None) -> list[dict]:
        """Return model-facing tool definitions.

        When model_info is provided, definitions are adapted for the active
        model (capability gates + minimum token budget for advanced tools).
        """
        ...
```

### Tool ABC

```python
# localagentcli/tools/base.py

from abc import ABC, abstractmethod

class Tool(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        """Tool name as used in function calls."""

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable description of what this tool does."""

    @property
    @abstractmethod
    def parameters_schema(self) -> dict:
        """JSON Schema for the tool's parameters."""

    @abstractmethod
    def execute(self, **kwargs) -> ToolResult:
        """Execute the tool with the given parameters."""

    @property
    def requires_approval(self) -> bool:
        """Whether this tool requires user approval before execution. Default: True."""
        return True

    @property
    def is_read_only(self) -> bool:
        """Whether this tool only reads data (no side effects). Default: False."""
        return False

    @property
    def required_model_capabilities(self) -> tuple[str, ...]:
        """Capability keys required before exposing this tool to the model."""
        return ()

    @property
    def minimum_model_default_max_tokens(self) -> int:
        """Minimum default model token budget required to expose this tool."""
        return 0

    def definition(self) -> dict:
        """Return the tool definition for the model's function calling API."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters_schema
        }
```

### Example Tool Implementation

```python
# localagentcli/tools/file_read.py

class FileReadTool(Tool):
    @property
    def name(self) -> str:
        return "file_read"

    @property
    def description(self) -> str:
        return "Read the contents of a file. Returns the file text."

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to workspace"},
                "offset": {"type": "integer", "description": "Starting line number (0-indexed)"},
                "limit": {"type": "integer", "description": "Maximum lines to read"}
            },
            "required": ["path"]
        }

    @property
    def requires_approval(self) -> bool:
        return False

    @property
    def is_read_only(self) -> bool:
        return True

    def execute(self, path: str, offset: int = 0, limit: int | None = None) -> ToolResult:
        ...
```

---

## Tool Execution Flow

```
Agent requests tool call
         │
         ▼
ToolRegistry.get_tool(name)
         │
         ▼
SafetyLayer.check_approval(tool, args)
         │
    ┌────┴────┐
    │         │
 Approved   Denied → ToolResult(status="denied")
    │
    ▼
SafetyLayer.pre_action(tool, args)  ← backup files if needed
    │
    ▼
Tool.execute(**args)
    │
    ▼
ToolResult returned to agent
```

---

## Tool Safety Classification

| Tool | Read-Only | Requires Approval (Balanced Mode) |
|---|---|---|
| `file_read` | Yes | No |
| `file_search` | Yes | No |
| `directory_list` | Yes | No |
| `file_write` | No | Yes |
| `patch_apply` | No | Yes |
| `shell_execute` | No | Yes |
| `test_execute` | No | Yes |
| `git_status` | Yes | No |
| `git_diff` | Yes | No |
| `git_commit` | No | Yes |
