# LocalAgentCLI — Session and Configuration

This document covers the configuration system (TOML-based global config) and the session system (state management, save/load, context compaction). For config-related commands, see [commands.md](commands.md).

---

## Configuration System

### Format

Configuration uses **TOML** for its readability and structured nature.

### Location

```
~/.localagent/config.toml
```

### Scope

- **Global config only**: There is one config file for the entire installation
- **Session overrides**: Some config values can be overridden per-session, but these overrides live in memory only and are not persisted to the config file

### Default Configuration

```toml
# ~/.localagent/config.toml

[general]
default_mode = "agent"          # "chat" | "agent"
workspace = "."                 # Default workspace path (current directory)
logging_level = "normal"        # "normal" | "verbose" | "debug"

[model]
active_model = ""               # CLI-wide default target identifier

[provider]
active_provider = ""            # CLI-wide default provider for remote targets

[safety]
approval_mode = "balanced"      # "balanced" | "autonomous"
sandbox_mode = "workspace-write"  # "workspace-write" | "read-only" | "danger-full-access"

[generation]
temperature = 0.7
max_tokens = 4096
top_p = 1.0
# Additional generation parameters can be added here

[timeouts]
shell_command = 120             # Seconds before shell commands are killed
model_response = 300            # Seconds before model response times out
inactivity = 600                # Seconds of agent inactivity before pause

[providers]
# Provider configurations are stored as sub-tables
# Example:
# [providers.openai]
# type = "openai"
# base_url = "https://api.openai.com/v1"

[mcp_servers]
# MCP stdio servers are stored as nested tables
# Example:
# [mcp_servers.demo]
# command = "python"
# args = ["server.py"]
# cwd = "/path/to/project"
```

### Configurable Fields

| Field | Type | Default | Description |
|---|---|---|---|
| `general.default_mode` | string | `"agent"` | Mode at startup |
| `general.workspace` | string | `"."` | Default workspace path |
| `general.logging_level` | string | `"normal"` | Log verbosity |
| `model.active_model` | string | `""` | CLI-wide default target identifier. For local targets this is `<name>@<version>`; for remote targets this is the selected remote model id. |
| `provider.active_provider` | string | `""` | CLI-wide default remote provider name. Empty means the default target is local or unset. |
| `safety.approval_mode` | string | `"balanced"` | Approval mode (`balanced` or `autonomous`) |
| `safety.sandbox_mode` | string | `"workspace-write"` | Runtime sandbox posture for tool execution |
| `generation.temperature` | float | `0.7` | Sampling temperature |
| `generation.max_tokens` | int | `4096` | Maximum tokens to generate |
| `generation.top_p` | float | `1.0` | Nucleus sampling threshold |
| `timeouts.shell_command` | int | `120` | Shell command timeout (seconds) |
| `timeouts.model_response` | int | `300` | Model response timeout (seconds) |
| `timeouts.inactivity` | int | `600` | Agent inactivity timeout (seconds) |

### ConfigManager

```python
# localagentcli/config/manager.py

class ConfigManager:
    def __init__(self, config_path: Path = None):
        self._path = config_path or Path.home() / ".localagent" / "config.toml"
        self._config: dict = {}

    def load(self) -> None:
        """Load config from disk. Creates default config if file doesn't exist."""

    def save(self) -> None:
        """Write current config to disk."""

    def get(self, key: str, default: Any = None) -> Any:
        """Get a config value by dotted key (e.g., 'general.default_mode')."""

    def set(self, key: str, value: Any) -> None:
        """Set a config value by dotted key. Validates the key and value type."""

    def get_all(self) -> dict:
        """Return the full config as a dict."""

    def reset_to_defaults(self) -> None:
        """Reset all config values to their defaults."""
```

### Config Validation

When a value is set via `/config`, the system validates:
1. The key exists in the known schema
2. The value type matches the expected type
3. The value is within valid ranges (e.g., temperature 0.0–2.0)
4. For enum-like fields (mode, logging_level), the value is one of the allowed options

Invalid values are rejected with a clear error message.

### Interactive `/config` Editing

- In an interactive shell, `/config` opens a picker of valid config keys instead of only printing the file contents
- Enum-like keys use choice menus so only legitimate values can be selected
- Free-form keys still route through the same schema validation before they are saved
- In non-interactive environments, `/config` without arguments continues to print the current configuration

---

## Session System

### Session State

A session holds the complete runtime state of the application:

```python
# localagentcli/session/state.py

@dataclass
class Session:
    id: str                          # Unique session identifier (UUID)
    name: str | None                 # User-assigned name (optional)
    mode: str                        # "chat" | "agent"
    model: str                       # Active model name
    provider: str                    # Active provider name
    workspace: str                   # Workspace root path
    history: list[Message]           # Conversation/interaction history
    tasks: list[TaskPlan]            # Agent task plans (agent mode)
    pinned_instructions: list[str]   # Instructions that survive compaction
    config_overrides: dict           # Session-level config overrides
    created_at: datetime
    updated_at: datetime
    metadata: dict                   # Arbitrary session metadata
```

### Message Schema

```python
@dataclass
class Message:
    role: str          # "user" | "assistant" | "system" | "tool"
    content: str       # Message text
    timestamp: datetime
    metadata: dict     # Optional: tool call info, reasoning tokens, etc.
    is_summary: bool = False  # True if this message is a compaction summary
```

### Session Features

#### Save

- Command: `/session save [name]`
- Serializes the full `Session` object to JSON
- Storage: `~/.localagent/sessions/<name>.json`
- If no name is given, uses `session_<timestamp>` format
- Includes all history, tasks, pinned instructions, and config overrides

#### Load

- Command: `/session load <name>`
- Deserializes the session from disk
- Restores mode, model, provider, workspace, history, and task state
- If the referenced model is no longer installed, warns the user and clears the active model

#### Fork

Forking creates a new in-memory session from a saved JSON snapshot with a **new session id** while copying history, tasks, pinned instructions, and config overrides.

- **CLI**: `localagentcli exec --fork <saved-name> ...` loads the forked session, runs the one-shot turn, then persists under the fork’s name (or `--save-session` when provided).
- **Provenance** (stored on the forked session’s `metadata` and included when that session is saved):
  - `fork_parent_name` — saved session name that was forked from
  - `fork_parent_id` — UUID of the source session at fork time
  - `forked_at` — ISO timestamp when the fork was created

#### Headless `exec` session persistence

For `localagentcli exec`, when a persistence target is in effect (`--session`, `--fork`, or `--save-session`), the current session is **written best-effort** to that name when the command exits, including after **Ctrl+C** (KeyboardInterrupt) or an error during the turn. If the final save fails (for example disk error), the error is swallowed so the process can still shut down; operators should verify the session file when durability matters.

#### List

- Command: `/session list`
- Lists all saved sessions with: name, creation date, model, message count, last mode

#### New

- Command: `/session new`
- Creates a fresh session with default values
- Applies the CLI-wide default target selected via `/set default`
- If that stored target is no longer valid, the shell falls back to another available installed model or configured provider model when possible
- When a replacement happens, the shell prints one warning naming the invalid target, the replacement target, and the reason for the repair
- The repaired target is written back to config so future sessions start from the valid replacement
- Clears history, tasks, and config overrides
- Does not affect global config

#### Clear

- Command: `/session clear`
- Clears history and tasks only
- Keeps active model, provider, workspace, and config overrides

---

## Context Management

### The Problem

Language models have finite context windows. A long conversation or agent task can exceed this limit, causing errors or degraded quality.

### The Solution: Automatic Compaction

The system implements automatic context compaction (summarization) to maintain unlimited logical continuity within a bounded context window.

### How It Works

1. **Token counting**: After each interaction, the system estimates tokens for the full prompt budget (repository instructions, pinned instructions, and message history). The estimate is a **coarse lower bound**: UTF-8 byte length of each message’s role and content, converted with a ceiling divide-by-four heuristic (not a real tokenizer), plus a small fixed per-message overhead and a capped contribution from non-empty `metadata` (so tool payloads are not treated as free).
2. **Threshold check**: If the estimate reaches **75% of an effective context limit**, compaction triggers. The effective limit is the model context window minus a **generation headroom** reserved for the next reply (default: the smaller of one-eighth of the window, one-quarter of the window, and 2048 tokens). Pass `generation_headroom_tokens=0` to `ContextCompactor` to disable that reserve.
3. **Summarization**: The oldest messages (excluding pinned instructions and the most recent N messages) are sent to the model with a summarization prompt
4. **Replacement**: The summarized messages are replaced with a single `Message(role="system", content=summary, is_summary=True)`
5. **Retention**: Pinned instructions and the most recent messages (configurable, default: last 10) are always kept verbatim

### ContextCompactor

```python
# localagentcli/session/compactor.py

class ContextCompactor:
    def __init__(
        self,
        model: ModelAbstractionLayer,
        context_limit: int,
        generation_headroom_tokens: int | None = None,
    ):
        self._model = model
        self._context_limit = context_limit
        self._threshold = 0.75  # Trigger at 75% of effective context limit
        self._keep_recent = 10  # Always keep the last N messages

    def needs_compaction(self, messages: list[Message]) -> bool:
        """Check if the message history exceeds the compaction threshold."""

    def compact(self, messages: list[Message],
                pinned: list[str]) -> list[Message]:
        """Compact the message history.

        1. Separate pinned instructions and recent messages (keep these)
        2. Summarize the remaining older messages
        3. Return: pinned + [summary] + recent
        """

    def estimate_tokens(self, messages: list[Message]) -> int:
        """Estimate tokens using the shared UTF-8 byte ceiling heuristic."""
```

### Compaction Rules

1. **Pinned instructions never compacted**: They are always included verbatim at the start of the context
2. **Recent messages preserved**: The most recent N messages are never summarized (configurable via `_keep_recent`)
3. **Summaries are recursive**: If a summary itself becomes old enough, it can be summarized again in a subsequent compaction
4. **Important steps retained**: In agent mode, tool call results and key observations are flagged as important and are included in summaries with higher detail
5. **Transparency**: When compaction occurs, the user sees an inline activity log message: `"Context compacted: summarized N messages"`

**Regression coverage:** Pytest covers compaction on chat input and on agent-mode dispatch when history exceeds the configured threshold, including direct-answer and multi-step `handle_task` paths after compaction (`tests/test_compaction_integration.py`).

### Persistent Summaries

When a session is saved, the system generates a final summary of the session for quick reference. This summary is stored in the session metadata and displayed in `/session list`.

---

## Session Storage Format

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "name": "refactor-auth",
  "mode": "agent",
  "model": "codellama-7b",
  "provider": "",
  "workspace": "/home/user/project",
  "history": [
    {
      "role": "user",
      "content": "Refactor the auth module to use JWT",
      "timestamp": "2025-01-15T10:30:00Z",
      "metadata": {},
      "is_summary": false
    }
  ],
  "tasks": [],
  "pinned_instructions": ["Always use type hints in Python code"],
  "config_overrides": {"generation.temperature": 0.3},
  "created_at": "2025-01-15T10:29:55Z",
  "updated_at": "2025-01-15T11:15:00Z",
  "metadata": {
    "summary": "Refactored auth module from session-based to JWT. Modified 4 files.",
    "message_count": 42
  }
}
```

---

## SessionManager

```python
# localagentcli/session/manager.py

class SessionManager:
    def __init__(self, sessions_dir: Path, config: ConfigManager):
        self._dir = sessions_dir
        self._config = config
        self._current: Session | None = None

    def new_session(self) -> Session:
        """Create a fresh session with defaults from config."""

    def save_session(self, name: str | None = None) -> Path:
        """Save the current session to disk. Returns the file path."""

    def load_session(self, name: str) -> Session:
        """Load a session from disk. Sets it as the current session."""

    def fork_session(self, name: str, fork_name: str | None = None) -> Session:
        """Fork a saved session: new id, optional name, fork lineage metadata."""

    def list_sessions(self) -> list[SessionSummary]:
        """List all saved sessions with summary info."""

    def clear_session(self) -> None:
        """Clear history and tasks of the current session."""

    @property
    def current(self) -> Session:
        """The active session."""

    def apply_config_override(self, key: str, value: Any) -> None:
        """Set a session-level config override (in memory only)."""

    def get_effective_config(self, key: str) -> Any:
        """Get a config value with session overrides applied.
        Session override > global config > default.
        """
```
