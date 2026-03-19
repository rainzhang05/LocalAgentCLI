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
2. **Hierarchical structure**: Related commands are grouped under a parent (e.g., `/models list`, `/models install`). Parent commands usually print subcommand help, but interactive parents are allowed when the command is explicitly acting as a wizard or picker.
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
- **Behavior**: Launches the first-run setup wizard. In the current implementation it configures:
  1. Workspace directory
  2. Default mode (`chat` or `agent`)
  3. Logging level (`normal`, `verbose`, or `debug`)
- **Non-interactive behavior**: If stdin is not interactive (for example under `pipx` smoke tests, CI pipes, or shell redirection), the command does not prompt. It keeps the current/default values, persists them, and completes successfully so first launch does not fail.
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
  - No arguments: opens an interactive config editor in a TTY, or prints all current configuration values in non-interactive environments
  - One argument (key): prints the value of that key
  - Two arguments (key + value): sets the key to the new value and persists to `config.toml`
- **Interactive editing**:
  - Presents only schema-approved config keys
  - Enum-like values (mode, logging level, approval mode) are chosen from valid options only
  - Free-form values are still validated before being persisted
- **Valid keys**: dotted keys such as `general.default_mode`, `general.workspace`, `general.logging_level`, `model.active_model`, `provider.active_provider`, `safety.approval_mode`, `generation.temperature`, `generation.max_tokens`, `generation.top_p`, and the timeout keys

#### `/hf-token`
- **Syntax**: `/hf-token [token]`
- **Behavior**: Stores the Hugging Face token used for private Hub model discovery and downloads. If no token is provided and the shell is interactive, prompts securely for it.
- **Visibility**: Once a token is already available, the command is hidden from the live slash-command menu and `/help`.

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

#### `/models`
- **Syntax**: `/models`
- **Behavior**: Opens an interactive Hugging Face picker backed by live Hub API discovery. The flow is layered:
  1. Choose the runtime/backend family (`PyTorch / Safetensors`, `MLX` when supported, or `GGUF`)
  2. Choose a model family (`GPT-OSS`, `Qwen`, `Llama`, `Gemma`, `Mistral`, `Phi`, `DeepSeek`, `Granite`, etc.)
  3. Choose the exact model repo discovered live from the Hugging Face API
  4. Download the model immediately and set it as the active local model for the current session
- **Navigation**: Up/Down arrows move through options, typing filters the current layer, and Enter accepts the highlighted choice.

#### `/models list`
- **Syntax**: `/models list`
- **Behavior**: Lists all installed local models with their name, format, size, and capabilities.
- **Output**: Formatted table.

#### `/models search <query>`
- **Syntax**: `/models search <query>`
- **Behavior**: Searches installed local models by name, format, or registry metadata.
- **Output**: Formatted list of installed matches.

#### `/models install hf <repo>`
- **Syntax**: `/models install hf <repo>`
- **Behavior**: Downloads a model from an explicit Hugging Face repository path. Automatically detects the model format (MLX, GGUF, safetensors), assigns the appropriate backend, validates the model structure, extracts metadata, and registers it in the model registry.
- **Progress**: Displays a download progress bar with speed and ETA.

#### `/models install url <url>`
- **Syntax**: `/models install url <url>`
- **Behavior**: Downloads a model file from a direct URL. Same detection, validation, and registration as HF install.

#### `/models remove <name>`
- **Syntax**: `/models remove <name>`
- **Behavior**: Removes an installed model. Deletes model files from `~/.localagent/models/` and removes the registry entry.
- **Selection**: If no name is provided in an interactive terminal, opens a picker of installed models.

#### `/models inspect <name>`
- **Syntax**: `/models inspect <name>`
- **Behavior**: Displays detailed metadata about an installed model: format, size on disk, parameter count (if available), quantization level, capabilities (tool use, reasoning, streaming), backend assignment, and version info.
- **Selection**: If no name is provided in an interactive terminal, opens a picker of installed models.

---

### Target Commands

#### `/set`
- **Syntax**: `/set`
- **Behavior**: Opens a layered picker for choosing the active inference target.
  1. Choose between `Providers` and `Local models`
  2. For providers: choose a configured provider, then choose one of its discovered models
  3. For local models: choose one installed local model directly
- **Scope**: Applies to the current session only.
- **Notes**: This is the primary interactive replacement for `/models use` and `/providers use`.

#### `/set default`
- **Syntax**: `/set default`
- **Behavior**: Opens the same layered picker as `/set`, but persists the selected target as the CLI-wide default for new sessions.
- **Scope**: Global config. The selected target becomes the startup default until changed or removed.
- **Fallback**: If the stored default target later becomes invalid (for example, a local model is deleted), the shell falls back to the next available installed model or configured provider model.

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
- **Note**: Providers no longer own a user-configurable default model. Model selection happens through `/set` or `/set default`.

#### `/providers remove`
- **Syntax**: `/providers remove <name>`
- **Behavior**: Removes a configured provider. Deletes stored credentials. Prompts for confirmation.
- **Selection**: If no name is provided in an interactive terminal, opens a picker of configured providers.

#### `/providers test`
- **Syntax**: `/providers test [name]`
- **Behavior**: Tests connectivity to a provider by sending a minimal request. Reports success or failure with error details.
- **Selection**: In an interactive terminal, if no name is provided the command opens a picker of configured providers, defaulting to the current provider when possible.
- **Non-interactive behavior**: Without a name, falls back to the current session provider and then the globally active provider.

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
- **Selection**: If no name is provided in an interactive terminal, opens a picker of saved sessions.

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
- **Behavior**: Sets approval mode to `autonomous` for the current session and future sessions. If an agent task is currently paused on approval, the pending action is approved and the task resumes in autonomous mode. High-risk actions still require explicit approval.
- **Reset**: Use `/config safety.approval_mode balanced` to switch back to balanced approvals.

#### `/agent deny`
- **Syntax**: `/agent deny`
- **Behavior**: Denies the pending agent action and halts the current step. The agent re-plans from the current state.

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
