# LocalAgentCLI — Architecture

## Overview

LocalAgentCLI is a production-grade, local-first AI CLI providing a unified interactive shell plus a shared submission/event runtime for local models (Hugging Face, direct downloads) and remote models (API providers). It delivers a modern agentic CLI experience with full transparency, strict safety controls, consistent cross-platform behavior, and zero manual configuration requirement.

---

## System Architecture

```
┌─────────────────────────────────┐
│          CLI Surfaces           │  ← Interactive shell, one-shot exec
├─────────────────────────────────┤
│            Shell UI             │  ← Prompt loop, activity rendering, approvals
├─────────────────────────────────┤
│        Command Router           │  ← Slash commands vs. plain text dispatch
├─────────────────────────────────┤
│ Runtime Services & Session Core │  ← Shared session/config/model/tool wiring
├─────────────────────────────────┤
│ Submission / Event Protocol     │  ← Shared ops, approvals, stream events
├─────────────────────────────────┤
│       Session Manager           │  ← State, history, context compaction
├─────────────────────────────────┤
│    Model Abstraction Layer      │  ← Unified generate/stream interface
├────────────────┬────────────────┤
│ Local Backends │ Remote Provid. │  ← MLX / GGUF / Safetensors | OpenAI / Anthropic / REST
├────────────────┴────────────────┤
│   Chat / Agent Controllers      │  ← Chat mode / Agent mode execution
├─────────────────────────────────┤
│         Tool Runtime            │  ← file, shell, git, test tools
├─────────────────────────────────┤
│         Safety Layer            │  ← Approvals, boundaries, rollback
├─────────────────────────────────┤
│      Storage & Logging          │  ← Persistent state, logs, config
└─────────────────────────────────┘
```

Each layer communicates only with its immediate neighbors. No layer may bypass the Safety Layer for operations that modify the filesystem or execute commands.

---

## Layer Responsibilities

### Shell UI
- Accepts user input from the terminal
- Routes input: lines starting with `/` go to Command Router; all other text goes through the shared execution runtime for chat or agent handling
- Renders streaming model output token-by-token
- Displays inline activity logs (tool calls, approvals, errors)
- Provides a scrollable reasoning panel when the model emits reasoning tokens
- Handles interrupt signals (Ctrl+C) to cancel in-flight operations gracefully
- Shows a persistent status header (active model, mode, workspace)

### Runtime Services And Execution
- Owns shared process-level services such as config, storage, registries, sessions, model installation helpers, and logging
- Resolves the active backend or provider into the unified model abstraction
- Builds shared generation options, context limits, tool registries, and safety wiring
- Drives interactive and headless turns on an **async** path: `SessionRuntime.aiter_events()`, `SessionExecutionRuntime.arun_chat_turn` / `adispatch_agent_turn`, with remote HTTP via `httpx.AsyncClient` and local backends bridged through the model layer without blocking the event loop
- Exposes reusable chat-turn and agent-dispatch entrypoints for both the interactive shell and non-interactive surfaces
- Keeps controller reuse and model/provider caching out of the prompt loop; provider instances are keyed by config fingerprint and active model so target changes invalidate stale clients

### Submission / Event Protocol
- Accepts typed runtime operations such as user turns, approval decisions, interrupts, and shutdown
- Emits a shared event stream for streamed text, agent activity, approval requests, completion, failure, and interruption
- Persists append-only runtime event logs per session for stronger continuity and debugging
- Allows interactive and headless surfaces to consume the same execution flow without duplicating turn orchestration

### Command Router
- Maintains a registry of all slash commands
- Parses command strings into command name + arguments
- Dispatches to the appropriate command handler
- Returns structured results (success/error) to the Shell UI
- Validates argument types and counts before dispatch
- Provides consistent error messages for unknown commands, missing arguments, and invalid syntax

### Session Manager
- Holds the current session state: mode, model, provider, workspace, history, tasks, logs
- Supports save/load of named sessions to disk
- Implements automatic context compaction (summarization) when history grows large
- Retains important steps and pinned instructions across compaction
- Allows session overrides of global config values (in memory only)

### Model Abstraction Layer
- Provides a unified interface regardless of whether the underlying model is local or remote
- Core methods: `generate()`, `stream_generate()`, `agenerate()`, `astream_generate()`, `supports_tools()`, `supports_reasoning()`, `supports_streaming()`
- Normalizes all model outputs into a consistent format
- Enforces the rule: if a model does not support tool use, agent mode is blocked
- Selects the appropriate backend or provider based on the active model configuration

### Local Backends
- **MLX Backend**: macOS-optimized inference using Apple's MLX framework. Automatically detects and leverages Apple Silicon hardware. Handles model loading, tokenization, and generation.
- **GGUF Backend**: Cross-platform inference using llama.cpp bindings. Supports quantized models. Works on macOS, Linux, and Windows.
- **Safetensors Backend**: PyTorch-based inference for safetensors-format models. Full precision or quantized. Requires PyTorch runtime.

Each backend must:
1. Load the model into memory
2. Handle inference (single-shot and streaming)
3. Manage memory (load/unload, memory pressure detection)
4. Expose capability flags (tool use, reasoning, streaming)

### Remote Providers
- Manages connections to external API services (OpenAI-compatible, Anthropic-style, generic REST)
- Handles API key storage (OS keychain preferred, encrypted local fallback)
- Discovers available models from provider APIs
- Streams responses in real time
- Normalizes provider-specific response formats into the unified output schema

### Agent Controller
- Implements two execution modes:
  - **Chat Mode**: Simple request/response with session history, auto-compaction, and reasoning display
  - **Agent Mode**: Multi-step autonomous execution with plan generation, tool calls, observation, and iterative refinement
- The agent loop runs until the task is complete or the user interrupts
- Manages subtask decomposition and dynamic re-planning

### Tool Runtime
- Registers and executes tools (file operations, shell commands, git operations, test runners)
- Enforces the tool output schema: `{status, summary, output, error, exit_code, files_changed, duration}`
- All tool executions are routed through the Safety Layer before running
- Tools operate only within the active workspace boundary

### Safety Layer
- Intercepts all tool calls and applies approval rules based on the current approval mode
- Enforces workspace boundary restrictions
- Maintains a rollback log (file backups, patch history) for undo capability
- Always requires explicit approval for high-risk actions (deletes, system commands, external downloads, credential access)
- Provides inline approval prompts to the user

### Storage & Logging
- Manages the `~/.localagent/` directory structure
- Reads/writes config (`config.toml`), model registry (`registry.json`), sessions, logs, cache, and secrets
- Supports multiple log levels (normal, verbose, debug)
- Exports logs in text and JSON formats

---

## Suggested Python Package Structure

```
localagentcli/
├── __init__.py
├── __main__.py              # Entry point: interactive shell + headless exec surface
├── runtime/
│   ├── __init__.py
│   ├── core.py              # Shared runtime services and execution helpers
│   ├── protocol.py          # Submission and event protocol
│   ├── session_runtime.py   # Session-bound submission/event runtime
│   └── event_log.py         # Append-only runtime event logs
├── shell/
│   ├── __init__.py
│   ├── ui.py                # ShellUI — input loop, rendering, status header
│   ├── prompt.py            # Prompt line formatting and input handling
│   └── streaming.py         # Token-by-token streaming renderer
├── commands/
│   ├── __init__.py
│   ├── router.py            # CommandRouter — registry, dispatch, parsing
│   ├── help.py              # /help command
│   ├── setup.py             # /setup command
│   ├── status.py            # /status command
│   ├── config.py            # /config command
│   ├── mode.py              # /mode command
│   ├── models.py            # /models command group
│   ├── providers.py         # /providers command group
│   ├── workspace.py         # /workspace command
│   ├── session.py           # /session command group
│   ├── agent.py             # /agent command group
│   └── logs.py              # /logs command group
├── models/
│   ├── __init__.py
│   ├── registry.py          # ModelRegistry — tracks installed models
│   ├── abstraction.py       # ModelAbstractionLayer — unified interface
│   ├── detector.py          # Format detection and backend assignment
│   ├── backends/
│   │   ├── __init__.py
│   │   ├── base.py          # ModelBackend ABC
│   │   ├── mlx.py           # MLXBackend
│   │   ├── gguf.py          # GGUFBackend
│   │   └── safetensors.py   # SafetensorsBackend
│   └── installer.py         # Model download and installation
├── providers/
│   ├── __init__.py
│   ├── registry.py          # ProviderRegistry — manages configured providers
│   ├── base.py              # RemoteProvider ABC
│   ├── openai.py            # OpenAIProvider
│   ├── anthropic.py         # AnthropicProvider
│   ├── rest.py              # GenericRESTProvider
│   └── keys.py              # API key storage (keychain / encrypted)
├── agents/
│   ├── __init__.py
│   ├── chat.py              # ChatController — chat mode logic
│   ├── controller.py        # AgentController — agent mode orchestration
│   ├── loop.py              # AgentLoop — understand/plan/execute/observe cycle
│   └── planner.py           # TaskPlan — plan generation and tracking
├── tools/
│   ├── __init__.py
│   ├── registry.py          # ToolRegistry — tool registration and lookup
│   ├── base.py              # Tool ABC and ToolResult schema
│   ├── file_read.py
│   ├── file_search.py
│   ├── directory_list.py
│   ├── file_write.py
│   ├── patch_apply.py
│   ├── shell_execute.py
│   ├── test_execute.py
│   ├── git_status.py
│   ├── git_diff.py
│   └── git_commit.py
├── safety/
│   ├── __init__.py
│   ├── layer.py             # SafetyLayer — central approval gate
│   ├── approval.py          # ApprovalManager — mode-based approval logic
│   ├── boundary.py          # WorkspaceBoundary — path enforcement
│   └── rollback.py          # RollbackManager — backup/undo
├── storage/
│   ├── __init__.py
│   ├── manager.py           # StorageManager — directory layout, file I/O
│   └── logger.py            # Logger — leveled logging, export
├── config/
│   ├── __init__.py
│   ├── manager.py           # ConfigManager — TOML read/write
│   └── defaults.py          # Default configuration values
└── session/
    ├── __init__.py
    ├── manager.py            # SessionManager — save/load/list sessions
    ├── state.py              # Session — session state dataclass
    └── compactor.py          # ContextCompactor — summarization logic
```

---

## Concurrency Model

- **One task per shell**: Each shell instance runs a single active task at a time. The agent loop, model generation, and tool execution are sequential within a session.
- **One active turn per session runtime**: Interactive and headless surfaces both serialize submissions through one active turn at a time per session runtime.
- **Multiple shells allowed**: Users may run multiple `localagentcli` instances simultaneously. Each instance operates independently with its own session state.
- **No shared mutable state**: Instances share only the filesystem (`~/.localagent/`). File-level locking must be used when writing to shared resources (config, registry, logs).

---

## Cross-Platform Requirements

| Concern | macOS | Linux | Windows |
|---|---|---|---|
| Shell UI | Full support | Full support | Full support (native + WSL) |
| MLX Backend | Apple Silicon optimized | Not available | Not available |
| GGUF Backend | Full support | Full support | Full support |
| Safetensors Backend | Full support | Full support | Full support |
| Keychain storage | macOS Keychain | libsecret / kwallet | Windows Credential Store |
| File paths | POSIX | POSIX | Handle both `\` and `/` |
| Entry point | `localagentcli` | `localagentcli` | `localagentcli` / `localagentcli.exe` |

Behavior must be identical across all platforms after launch, except for backend availability (MLX is macOS-only). The system must detect the platform at startup and adapt accordingly without user intervention.

---

## Future-Proofing

The architecture must support these extensions without structural rewrites:

- **New model formats**: Adding a new backend requires implementing the `ModelBackend` ABC and registering it with the detector. No changes to the abstraction layer or higher layers.
- **New remote providers**: Adding a provider requires implementing the `RemoteProvider` ABC and registering it. The unified interface remains unchanged.
- **Multimodal expansion**: The `generate()` and `stream_generate()` interfaces should accept optional image/audio inputs. This is a method signature extension, not a structural change.
- **RAG integration**: A retrieval layer can be inserted between the Session Manager and Model Abstraction Layer. The session history would include retrieved context alongside user messages.
- **Plugin system**: Tools, commands, and backends can be loaded dynamically from a `plugins/` directory using entry points or a plugin registry.

---

## Design Constraints

1. **Zero configuration required**: The system must be usable immediately after installation. All defaults are sensible. The first-run `/setup` wizard handles model/provider selection interactively.
2. **Streaming always enabled**: All model output is streamed token-by-token. No batch-mode output.
3. **Transparency**: Every internal operation (tool call, approval decision, error) is visible to the user via inline activity logs.
4. **Safety by default**: The default approval mode (`balanced`) requires user confirmation for all write operations, shell commands, and git operations. Read-only operations are auto-approved.
