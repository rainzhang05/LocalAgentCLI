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
approvals_reviewer = "user"     # "user" | "guardian_subagent"
sandbox_mode = "workspace-write"  # "workspace-write" | "read-only" | "danger-full-access"
os_sandbox_backend = "off"      # "off" | "auto" | "macos-seatbelt" | "linux-bwrap" | "container-docker"
sandbox_network_access = "auto" # "auto" | "allow" | "deny"
sandbox_writable_roots = ""      # Comma-separated extra writable roots (absolute or workspace-relative)
os_sandbox_container_image = "python:3.12-slim"  # Container image for container-docker backend
os_sandbox_container_cpu_limit = ""  # Optional container CPU limit (for example "1.5")
os_sandbox_container_memory_limit = "" # Optional container memory limit (for example "2g")

[generation]
temperature = 0.7
max_tokens = 4096
top_p = 1.0
reasoning_effort = ""          # "" | "low" | "medium" | "high"
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
# MCP stdio servers are stored as nested tables (see docs/mcp.md).
# Example:
# [mcp_servers.demo]
# command = "python"
# args = ["server.py"]
# cwd = "/path/to/project"
# env = { KEY = "value" }   # optional; merged over the process environment
# timeout = 30              # optional; per-request read timeout (seconds)

[features]
# Feature toggles to enable or disable specific experimental or stable features.
# Example:
# dummy_experimental = true
sqlite_session_store = false         # When true, sessions persist in ~/.localagent/sessions.db (with JSON auto-migration on load)
multi_agent_path_routing = false     # When true, enables feature-gated path-based multi-agent dynamic tools (spawn/send/wait/close/resume)

[sessions]
autosave_named = false              # When true, debounce-save named sessions during interactive chat/agent work
autosave_unnamed = false            # When true, debounce-save unnamed sessions to generated autosave IDs
autosave_unnamed_prefix = "autosave_" # Prefix used for generated unnamed autosave session names
autosave_unnamed_retention_days = 14 # Retention window for unnamed autosaves and old runtime logs
autosave_debounce_seconds = 2       # Minimum quiet period before writing (seconds); must be > 0

[shell]
persistent_details_lane = false     # When true, re-render the recent Details window at flush boundaries during streaming
thinking_indicator_enabled = true   # When true, show a transient thinking indicator during runtime drains
thinking_indicator_style = "dots"   # "dots" | "line" | "pulse"
thinking_animation_interval_ms = 120 # Minimum 40ms between thinking frames
theme = "default"                  # "default" | "high-contrast" | "mono"
notification_dedupe = true         # Deduplicate adjacent repeated shell notifications
startup_banner = true              # Show a startup context banner with mode/target/workspace/session
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
| `safety.approvals_reviewer` | string | `"user"` | Approval reviewer routing (`user` keeps interactive approval; `guardian_subagent` routes eligible approval-required actions through guardian risk review with fail-closed denial on reviewer errors) |
| `safety.sandbox_mode` | string | `"workspace-write"` | Runtime sandbox posture: `workspace-write`, `read-only`, or `danger-full-access` (invalid values are rejected when validated) |
| `safety.os_sandbox_backend` | string | `"off"` | Optional command wrapper backend: `off`, `auto`, `macos-seatbelt`, `linux-bwrap`, `container-docker` |
| `safety.sandbox_network_access` | string | `"auto"` | Runtime network policy override for typed sandbox policy (`auto`, `allow`, `deny`) |
| `safety.sandbox_writable_roots` | string | `""` | Comma-separated extra writable roots merged with workspace roots in `workspace-write` posture |
| `safety.os_sandbox_container_image` | string | `"python:3.12-slim"` | Container image used when `safety.os_sandbox_backend = container-docker` |
| `safety.os_sandbox_container_cpu_limit` | string | `""` | Optional docker `--cpus` value for container backend |
| `safety.os_sandbox_container_memory_limit` | string | `""` | Optional docker `--memory` value for container backend |
| `generation.temperature` | float | `0.7` | Sampling temperature |
| `generation.max_tokens` | int | `4096` | Maximum tokens to generate |
| `generation.top_p` | float | `1.0` | Nucleus sampling threshold |
| `generation.reasoning_effort` | string | `""` | Optional reasoning depth hint (`low`, `medium`, `high`) passed through when the active model supports reasoning effort controls |
| `timeouts.shell_command` | int | `120` | Shell command timeout (seconds) |
| `timeouts.model_response` | int | `300` | Model response timeout (seconds) |
| `timeouts.inactivity` | int | `600` | Agent inactivity timeout (seconds) |
| `features.*` | bool | (varies) | Individual feature toggles mapping to the internal feature registry. Default depends on feature stage. |
| `features.sqlite_session_store` | bool | `false` | Enables the SQLite-backed session store (`~/.localagent/sessions.db`). When enabled, named sessions are read from SQLite first, and missing legacy JSON sessions are auto-migrated on first load. |
| `features.multi_agent_path_routing` | bool | `false` | Enables feature-gated path-based multi-agent dynamic tools (`spawn_agent`, `send_input`, `wait_agent`, `close_agent`, `resume_agent`) and persists a lightweight active-agent snapshot in session metadata. |
| `shell.persistent_details_lane` | bool | `false` | When `true`, streaming surfaces re-render the rolling Details lane at flush boundaries so secondary context remains visible during long-running turns |
| `shell.thinking_indicator_enabled` | bool | `true` | Enables transient thinking animation while runtime submissions are being drained |
| `shell.thinking_indicator_style` | string | `"dots"` | Thinking indicator frame set (`dots`, `line`, or `pulse`) |
| `shell.thinking_animation_interval_ms` | int | `120` | Thinking animation cadence in milliseconds (minimum `40`) |
| `shell.theme` | string | `"default"` | Shell style token set (`default`, `high-contrast`, `mono`) used by renderer status/details/panels |
| `shell.notification_dedupe` | bool | `true` | Deduplicates adjacent identical structured notifications before rendering |
| `shell.startup_banner` | bool | `true` | Controls startup context banner rendering in interactive shell sessions |
| `sessions.autosave_named` | bool | `false` | When `true`, the interactive shell debounce-saves the current session to its saved name after chat/agent mutations (only applies when the session already has a name from `/session save`) |
| `sessions.autosave_unnamed` | bool | `false` | When `true`, unnamed sessions are debounce-saved using generated autosave names (does not rename the live in-memory session) |
| `sessions.autosave_unnamed_prefix` | string | `"autosave_"` | Prefix for generated unnamed autosave session names |
| `sessions.autosave_unnamed_retention_days` | int | `14` | Retention window for pruning old unnamed autosaves and stale runtime-event logs |
| `sessions.autosave_debounce_seconds` | int | `2` | Debounce interval for named autosaves; must be greater than zero |

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
- Serializes the full `Session` object payload
- Storage:
    - default: `~/.localagent/sessions/<name>.json`
    - when `features.sqlite_session_store=true`: `~/.localagent/sessions.db`
- If no name is given, uses `session_<timestamp>` format
- Includes all history, tasks, pinned instructions, and config overrides

#### Load

- Command: `/session load <name>`
- Deserializes the session from the active store
- Restores mode, model, provider, workspace, history, and task state
- If the referenced model is no longer installed, warns the user and clears the active model
- When `features.sqlite_session_store=true`, LocalAgentCLI reads from SQLite first; if the name only exists as a legacy JSON file, it is loaded and auto-migrated into SQLite.

#### Fork

Forking creates a new in-memory session from a saved JSON snapshot with a **new session id** while copying history, tasks, pinned instructions, and config overrides.

- **CLI**: `localagentcli exec --fork <saved-name> ...` loads the forked session, runs the one-shot turn, then persists under the fork’s name (or `--save-session` when provided).
- **Provenance** (stored on the forked session’s `metadata` and included when that session is saved):
  - `fork_parent_name` — saved session name that was forked from
  - `fork_parent_id` — UUID of the source session at fork time
  - `forked_at` — ISO timestamp when the fork was created
    - `fork_parent_startup_context` — normalized parent turn-context snapshot captured at fork creation
    - `context_diff_baseline` — initial diff baseline seeded from the parent snapshot for first-turn fork context updates

#### Headless `exec` session persistence

For `localagentcli exec`, when a persistence target is in effect (`--session`, `--fork`, or `--save-session`), the current session is **written best-effort** to that name when the command exits, including after **Ctrl+C** (KeyboardInterrupt) or an error during the turn. If the final save fails (for example disk error), the error is swallowed so the process can still shut down; operators should verify the session file when durability matters.

#### Named session autosave (interactive)

When `sessions.autosave_named` is `true` in config (session overrides apply), the shell **schedules** a debounced write to the session’s saved file name after meaningful chat or agent updates (for example user messages, assistant replies, compaction, agent task state changes). The debounce window is `sessions.autosave_debounce_seconds`. After each drained runtime submission (including approval pauses), a **flush** runs so pending debounced saves are applied before the next prompt. On exit, a flush runs so in-flight autosaves are not lost.

- **Unnamed sessions** (never saved with a name) are never autosaved; use `/session save` first.
- **Failures** during autosave are ignored (best-effort); interactive use continues.
- Default is **`false`** so disk writes stay opt-in.

When `sessions.autosave_unnamed=true`, unnamed sessions are persisted under generated IDs (`<autosave_unnamed_prefix><session-id>`) while keeping the live in-memory session unnamed. `sessions.autosave_unnamed_retention_days` controls cleanup of older unnamed autosaves and stale runtime-event logs.

#### Session file format version

Saved session payloads include `format_version` (currently `1`) for forward compatibility. Older JSON files without this field still load; the next save writes the current version.

#### SQLite session store (opt-in)

When `features.sqlite_session_store` is enabled:

- Session persistence is handled by `~/.localagent/sessions.db`.
- `/session save`, `/session load`, `/session list`, `exec --session`, and `exec --fork` continue using the same user-facing commands.
- Legacy JSON session files remain supported as compatibility inputs: if a named session is not present in SQLite but exists as JSON, load succeeds and the session is best-effort migrated into SQLite.
- If SQLite initialization fails at startup, LocalAgentCLI falls back to the JSON store for safety.

SQLite schema changes are applied through ordered SQL migrations under `localagentcli/session/migrations/` and tracked in `schema_migrations`, enabling versioned upgrades beyond the initial schema.

#### Runtime JSONL replay reconciliation

On `/session load`, LocalAgentCLI performs a best-effort reconciliation pass using append-only runtime event logs under `~/.localagent/cache/runtime-events/<session-id>.jsonl`:

- Completed turns in the runtime log (`submission` + `turn_completed`) can recover missing user/assistant pairs into in-memory session history.
- Recovery is conservative and idempotent for previously saved user/assistant pairs (duplicate pairs are skipped).
- Invalid/corrupt JSONL lines are ignored rather than failing session load.
- Replay metadata is recorded under `session.metadata.runtime_replay` for observability.

#### Long-horizon memory (workspace scoped)

When SQLite sessions are enabled, LocalAgentCLI also maintains a workspace-scoped memory table (`session_memories`) for durable context beyond immediate session snapshots:

- Memory candidates are extracted conservatively from compaction summaries (`is_summary=True`) and explicitly tagged assistant memory candidates.
- Memory rows are persisted per session/workspace and deduplicated per session content.
- On session load, the newest workspace memories are merged into `session.metadata.long_horizon_memory`.
- System prompt assembly appends a compact `Long-horizon memory:` block when memory entries are present.

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
2. **API usage-aware threshold check**: When providers return usage counters, LocalAgentCLI stores normalized prompt/completion/total counts in session metadata (`metadata.usage_budget`). Compaction checks consider both the heuristic estimate and the latest provider-reported prompt budget (falling back cleanly when usage is unavailable).
3. **Threshold gate**: If the effective estimate reaches **75% of an effective context limit**, compaction triggers. The effective limit is the model context window minus a **generation headroom** reserved for the next reply (default: the smaller of one-eighth of the window, one-quarter of the window, and 2048 tokens). Pass `generation_headroom_tokens=0` to `ContextCompactor` to disable that reserve.
4. **Summarization**: The oldest messages (excluding pinned instructions and the most recent N messages) are sent to the model with a summarization prompt
5. **Replacement**: The summarized messages are replaced with a single `Message(role="system", content=summary, is_summary=True)`
6. **Retention**: Pinned instructions and the most recent messages (configurable, default: last 10) are always kept verbatim

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

    def needs_compaction(
        self,
        messages: list[Message],
        usage_snapshot: dict[str, Any] | None = None,
    ) -> bool:
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
5. **Middle-turn reduction before summary**: For very large histories, compaction transcript construction keeps head/tail context and inserts an explicit omitted-middle marker, reducing redundancy while preserving recency
6. **Tool-schema-aware transcript formatting**: Tool-role messages are rendered with structured fields (`tool`, `status`, `summary`, `error`, output preview) instead of flattening to unstructured plain text, which preserves agent-observation signal
7. **Transparency**: When compaction occurs, the user sees an inline activity log message: `"Context compacted: summarized N messages"`

**Regression coverage:** Pytest covers compaction on chat input and on agent-mode dispatch when history exceeds the configured threshold, including direct-answer and multi-step `handle_task` paths after compaction (`tests/test_compaction_integration.py`).

### Usage budget metadata

When remote providers return usage counters, LocalAgentCLI persists a normalized usage snapshot in `session.metadata["usage_budget"]`:

- `latest`: latest normalized prompt/completion/total counts with source + timestamp
- `cumulative`: running aggregate prompt/completion/total counts across model calls
- `turns_with_usage`: number of model calls that included usage data

This state is used for budgeting/compaction decisions and for agent task-state telemetry.

### Persistent Summaries

When a session is saved, the system generates a final summary of the session for quick reference. This summary is stored in the session metadata and displayed in `/session list`.

---

## Session Storage Format

```json
{
  "format_version": 1,
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
