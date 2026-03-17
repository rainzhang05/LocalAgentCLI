# LocalAgentCLI — Command System

## Input Routing

All user input in the interactive shell follows two paths:

| Input Pattern | Destination | Example |
|---|---|---|
| Starts with `/` | Command Router | `/help`, `/models list` |
| Plain text | Active model (via Session Manager) | `explain this code` |

The Command Router strips the leading `/`, splits on whitespace to extract the command name and arguments, and dispatches to the registered handler.

---

## Design Rules

1. **Consistent syntax**: All commands follow the pattern `/command [subcommand] [arguments]`. No positional ambiguity.
2. **Hierarchical structure**: Related commands are grouped under a parent (e.g., `/models list`, `/models install`). The parent alone (e.g., `/models`) prints its subcommand help.
3. **Human-readable output**: All command responses use clear, formatted text. No raw data dumps.
4. **Predictable errors**: Invalid commands return a structured error with the invalid input, a brief explanation, and a suggestion (e.g., "Did you mean `/models list`?").

---

## Command Reference

### System Commands

#### `/help`
- **Syntax**: `/help [command]`
- **Behavior**: Without arguments, prints a summary of all available commands grouped by category. With a command name, prints detailed help for that specific command including syntax, arguments, and examples.
- **Output**: Formatted text table or section.

#### `/setup`
- **Syntax**: `/setup`
- **Behavior**: Launches the first-run interactive setup wizard. Walks the user through:
  1. Selecting or installing a model (local or remote)
  2. Configuring a remote provider (if desired)
  3. Setting the default mode (chat or agent)
  4. Setting the workspace directory
- **Idempotent**: Can be re-run at any time to reconfigure.

#### `/status`
- **Syntax**: `/status`
- **Behavior**: Displays current session state:
  - Active mode (chat / agent)
  - Active model name and backend
  - Active provider (if remote)
  - Workspace path
  - Session name (if saved)
  - Approval mode
- **Output**: Compact key-value display.

#### `/config`
- **Syntax**: `/config [key] [value]`
- **Behavior**:
  - No arguments: prints all current configuration values
  - One argument (key): prints the value of that key
  - Two arguments (key + value): sets the key to the new value and persists to `config.toml`
- **Valid keys**: `default_mode`, `active_model`, `provider`, `workspace`, `approval_mode`, `timeout`, `logging_level`, and generation settings (`temperature`, `max_tokens`, `top_p`, etc.)

#### `/exit`
- **Syntax**: `/exit`
- **Behavior**: Cleanly shuts down the shell. If the current session has unsaved changes, prompts the user to save. Unloads any loaded model. Flushes logs.

---

### Mode Commands

#### `/mode chat`
- **Syntax**: `/mode chat`
- **Behavior**: Switches the session to chat mode. Subsequent plain text input is processed as simple conversation with the model. Tools are not invoked automatically.

#### `/mode agent`
- **Syntax**: `/mode agent`
- **Behavior**: Switches the session to agent mode. Subsequent plain text input is processed as a task. The agent loop activates: planning, tool execution, observation, and iteration.
- **Precondition**: The active model must support tool use. If it does not, this command fails with a clear error explaining why and suggesting a model that supports tools.

---

### Model Commands

#### `/models list`
- **Syntax**: `/models list`
- **Behavior**: Lists all installed local models with their name, format, size, and capabilities.
- **Output**: Formatted table.

#### `/models search <query>`
- **Syntax**: `/models search <query>`
- **Behavior**: Searches Hugging Face for models matching the query. Returns a list of matching repositories with model name, size, format, and download count.
- **Output**: Formatted table with results.

#### `/models install hf <repo>`
- **Syntax**: `/models install hf <repo>`
- **Behavior**: Downloads a model from a Hugging Face repository. Automatically detects the model format (MLX, GGUF, safetensors), assigns the appropriate backend, validates the model structure, extracts metadata, and registers it in the model registry.
- **Progress**: Displays a download progress bar with speed and ETA.

#### `/models install url <url>`
- **Syntax**: `/models install url <url>`
- **Behavior**: Downloads a model file from a direct URL. Same detection, validation, and registration as HF install.

#### `/models remove <name>`
- **Syntax**: `/models remove <name>`
- **Behavior**: Removes an installed model. Deletes model files from `~/.localagent/models/` and removes the registry entry. Prompts for confirmation before deletion.

#### `/models use <name>`
- **Syntax**: `/models use <name>`
- **Behavior**: Sets the active model for the current session. Loads the model into memory using the appropriate backend. If another model is loaded, unloads it first.
- **Validation**: Fails if the model name is not found in the registry.

#### `/models inspect <name>`
- **Syntax**: `/models inspect <name>`
- **Behavior**: Displays detailed metadata about an installed model: format, size on disk, parameter count (if available), quantization level, capabilities (tool use, reasoning, streaming), backend assignment, and version info.

---

### Provider Commands

#### `/providers list`
- **Syntax**: `/providers list`
- **Behavior**: Lists all configured remote providers with their name, type (OpenAI / Anthropic / REST), and status (connected / not tested).

#### `/providers add`
- **Syntax**: `/providers add`
- **Behavior**: Launches an interactive wizard to add a new provider:
  1. Select provider type (OpenAI-compatible, Anthropic, generic REST)
  2. Enter API base URL (if not default)
  3. Enter API key (stored securely)
  4. Optionally test the connection
  5. Register the provider

#### `/providers remove`
- **Syntax**: `/providers remove <name>`
- **Behavior**: Removes a configured provider. Deletes stored credentials. Prompts for confirmation.

#### `/providers use`
- **Syntax**: `/providers use <name>`
- **Behavior**: Sets the active provider for the current session. Overrides the global config for this session only.

#### `/providers test`
- **Syntax**: `/providers test [name]`
- **Behavior**: Tests connectivity to a provider by sending a minimal request. Reports success or failure with error details. Without a name, tests the currently active provider.

---

### Workspace Commands

#### `/workspace set <path>`
- **Syntax**: `/workspace set <path>`
- **Behavior**: Sets the workspace root directory. All file operations and shell commands in agent mode are constrained to this directory. The path must exist and be a directory.
- **Default**: If never set, defaults to the current working directory at launch.

---

### Session Commands

#### `/session new`
- **Syntax**: `/session new`
- **Behavior**: Starts a fresh session. Clears history, resets mode to default, and unloads any task state. Does not affect global config.

#### `/session save [name]`
- **Syntax**: `/session save [name]`
- **Behavior**: Saves the current session to disk. If no name is given, generates a timestamp-based name. Saves mode, model, provider, workspace, history, and task state.
- **Storage**: `~/.localagent/sessions/<name>.json`

#### `/session load <name>`
- **Syntax**: `/session load <name>`
- **Behavior**: Loads a previously saved session. Restores all session state including history and active model.

#### `/session list`
- **Syntax**: `/session list`
- **Behavior**: Lists all saved sessions with their name, creation date, model used, and message count.

#### `/session clear`
- **Syntax**: `/session clear`
- **Behavior**: Clears the current session history while keeping the active model, provider, and workspace. Useful for resetting context without restarting.

---

### Agent Commands

#### `/agent approve`
- **Syntax**: `/agent approve`
- **Behavior**: Switches to autonomous approval mode for the current agent task. The agent will no longer prompt for approval on standard actions (file writes, shell commands). High-risk actions still require explicit approval.

#### `/agent deny`
- **Syntax**: `/agent deny`
- **Behavior**: Denies the pending agent action and halts the current step. The agent re-plans from the current state.

#### `/agent stop`
- **Syntax**: `/agent stop`
- **Behavior**: Immediately stops the running agent task. Preserves current state so the user can review what was done.

---

### Log Commands

#### `/logs show`
- **Syntax**: `/logs show [level] [count]`
- **Behavior**: Displays recent log entries. Optional level filter (normal, verbose, debug). Optional count to limit entries (default: 50).

#### `/logs export`
- **Syntax**: `/logs export <format> [path]`
- **Behavior**: Exports the current session's logs to a file. Supported formats: `text`, `json`. If no path is given, writes to `~/.localagent/logs/export_<timestamp>.<ext>`.

---

## Implementation Hints

### Command Registry Pattern

```python
# localagentcli/commands/router.py

class CommandRouter:
    def __init__(self):
        self._commands: dict[str, CommandHandler] = {}

    def register(self, name: str, handler: CommandHandler) -> None:
        """Register a command handler. Name supports dot notation for subcommands."""
        self._commands[name] = handler

    def dispatch(self, input_line: str) -> CommandResult:
        """Parse input and dispatch to the registered handler."""
        parts = input_line.strip().split()
        command_name = parts[0]  # e.g., "models"
        # Try "models list" first, then fall back to "models"
        subcommand_name = f"{parts[0]} {parts[1]}" if len(parts) > 1 else None
        if subcommand_name and subcommand_name in self._commands:
            return self._commands[subcommand_name].execute(parts[2:])
        if command_name in self._commands:
            return self._commands[command_name].execute(parts[1:])
        return CommandResult.error(f"Unknown command: /{command_name}")
```

### CommandHandler ABC

```python
# localagentcli/commands/router.py

from abc import ABC, abstractmethod

class CommandHandler(ABC):
    @abstractmethod
    def execute(self, args: list[str]) -> CommandResult:
        """Execute the command with the given arguments."""
        ...

    @abstractmethod
    def help_text(self) -> str:
        """Return help text for this command."""
        ...
```

### CommandResult

```python
@dataclass
class CommandResult:
    success: bool
    message: str
    data: dict | None = None

    @classmethod
    def ok(cls, message: str, data: dict | None = None) -> "CommandResult":
        return cls(success=True, message=message, data=data)

    @classmethod
    def error(cls, message: str) -> "CommandResult":
        return cls(success=False, message=message)
```

### Registration

Each command module registers itself with the router at startup:

```python
# localagentcli/commands/models.py

class ModelsListHandler(CommandHandler):
    def execute(self, args: list[str]) -> CommandResult:
        ...

    def help_text(self) -> str:
        return "List all installed models"

def register(router: CommandRouter) -> None:
    router.register("models list", ModelsListHandler())
    router.register("models search", ModelsSearchHandler())
    router.register("models install", ModelsInstallHandler())
    router.register("models remove", ModelsRemoveHandler())
    router.register("models use", ModelsUseHandler())
    router.register("models inspect", ModelsInspectHandler())
```
