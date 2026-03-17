# LocalAgentCLI — Development Roadmap

This document defines the phased implementation plan. Each phase builds on the previous one. Phases should be completed in order — later phases depend on earlier ones.

---

## Phase Overview

```
Phase 1: Shell Core ──→ Phase 2: Remote Models ──→ Phase 3: Local Models
                                                          │
Phase 4: Chat Mode ←──────────────────────────────────────┘
         │
Phase 5: Agent System ──→ Phase 6: Safety ──→ Phase 7: Packaging
```

---

## Phase 1 — Shell Core

**Goal**: A working interactive shell with command routing, configuration, and session management.

### Deliverables

| Component | Description | Key Files |
|---|---|---|
| CLI entry point | `localagent` command launches the shell | `localagentcli/__main__.py` |
| Shell UI | Input loop, prompt, basic output rendering | `localagentcli/shell/ui.py`, `prompt.py` |
| Command Router | Slash command parsing and dispatch | `localagentcli/commands/router.py` |
| Core commands | `/help`, `/exit`, `/status`, `/config`, `/setup` | `localagentcli/commands/*.py` |
| Config system | TOML read/write, defaults, validation | `localagentcli/config/manager.py`, `defaults.py` |
| Session management | New/save/load/list/clear sessions | `localagentcli/session/manager.py`, `state.py` |
| Storage init | `~/.localagent/` directory structure | `localagentcli/storage/manager.py` |
| Basic logging | File-based logging with levels | `localagentcli/storage/logger.py` |

### Exit Criteria
- `localagent` launches and displays the shell prompt
- `/help` lists all commands
- `/config` reads and writes `config.toml`
- `/session save` and `/session load` work correctly
- `/exit` shuts down cleanly

### Dependencies
- None (this is the foundation)

### Relevant Docs
- [architecture.md](architecture.md) — overall structure and package layout
- [commands.md](commands.md) — command registry and core commands
- [session-and-config.md](session-and-config.md) — config and session systems
- [cli-and-ux.md](cli-and-ux.md) — shell UI and input handling
- [storage-and-logging.md](storage-and-logging.md) — directory structure and logging

---

## Phase 2 — Remote Models

**Goal**: Connect to remote API providers and stream model responses.

### Deliverables

| Component | Description | Key Files |
|---|---|---|
| Provider registry | Add/remove/list/use providers | `localagentcli/providers/registry.py` |
| OpenAI provider | OpenAI-compatible API integration | `localagentcli/providers/openai.py` |
| Anthropic provider | Anthropic Messages API integration | `localagentcli/providers/anthropic.py` |
| Generic REST provider | Configurable REST endpoint | `localagentcli/providers/rest.py` |
| Key management | OS keychain + encrypted fallback | `localagentcli/providers/keys.py` |
| Provider commands | `/providers add`, `list`, `remove`, `use`, `test` | `localagentcli/commands/providers.py` |
| Streaming | SSE-based streaming for all providers | `localagentcli/shell/streaming.py` |
| Model abstraction | Unified interface for remote models | `localagentcli/models/abstraction.py` |

### Exit Criteria
- `/providers add` configures an OpenAI or Anthropic provider
- `/providers test` verifies connectivity
- Plain text input produces streaming model responses
- API keys are stored securely (keychain or encrypted)

### Dependencies
- Phase 1 (shell, commands, config, sessions)

### Relevant Docs
- [remote-providers.md](remote-providers.md) — provider types, key storage, streaming protocol
- [model-system.md](model-system.md) — model abstraction layer

---

## Phase 3 — Local Models

**Goal**: Download, install, and run local models with automatic backend selection.

### Deliverables

| Component | Description | Key Files |
|---|---|---|
| Model registry | Track installed models in `registry.json` | `localagentcli/models/registry.py` |
| Model installer | Download from HF and URLs | `localagentcli/models/installer.py` |
| Format detector | Auto-detect MLX/GGUF/safetensors | `localagentcli/models/detector.py` |
| MLX backend | Apple Silicon inference | `localagentcli/models/backends/mlx.py` |
| GGUF backend | llama.cpp inference | `localagentcli/models/backends/gguf.py` |
| Safetensors backend | PyTorch inference | `localagentcli/models/backends/safetensors.py` |
| Model commands | `/models install`, `list`, `use`, `remove`, `inspect`, `search` | `localagentcli/commands/models.py` |
| Hardware detection | Detect capabilities, warn on limitations | `localagentcli/models/detector.py` |

### Exit Criteria
- `/models install hf <repo>` downloads and registers a model
- `/models use <name>` loads the model with the correct backend
- Local model produces streaming responses to plain text input
- Hardware warnings display when model is too heavy

### Dependencies
- Phase 1 (shell, commands, config)
- Phase 2 (model abstraction layer — extends it for local backends)

### Relevant Docs
- [model-system.md](model-system.md) — formats, registry, detection, backends, abstraction layer

---

## Phase 4 — Chat Mode

**Goal**: Polished conversational experience with context management.

### Deliverables

| Component | Description | Key Files |
|---|---|---|
| Chat controller | Chat mode logic | `localagentcli/agents/chat.py` |
| Context compaction | Automatic summarization | `localagentcli/session/compactor.py` |
| Reasoning display | Scrollable reasoning panel | `localagentcli/shell/streaming.py` |
| Pinned instructions | Instructions that survive compaction | `localagentcli/session/state.py` |
| Mode commands | `/mode chat`, `/mode agent` | `localagentcli/commands/mode.py` |
| UX polish | Status header, formatting, history navigation | `localagentcli/shell/ui.py` |

### Exit Criteria
- `/mode chat` switches to chat mode
- Extended conversations trigger auto-compaction without data loss
- Reasoning tokens display in a separate panel
- Session history persists correctly after compaction

### Dependencies
- Phase 2 or 3 (need at least one working model/provider)

### Relevant Docs
- [agent-system.md](agent-system.md) — chat mode details, chat controller
- [session-and-config.md](session-and-config.md) — context management, compaction

---

## Phase 5 — Agent System

**Goal**: Full agent loop with tool execution.

### Deliverables

| Component | Description | Key Files |
|---|---|---|
| Tool ABC and registry | Tool interface and registration | `localagentcli/tools/base.py`, `registry.py` |
| Core tools | All 10 tools implemented | `localagentcli/tools/*.py` |
| Agent controller | Agent mode orchestration | `localagentcli/agents/controller.py` |
| Agent loop | Understand → plan → execute → observe → update | `localagentcli/agents/loop.py` |
| Task planner | Plan generation and tracking | `localagentcli/agents/planner.py` |
| Agent commands | `/agent approve`, `deny`, `stop` | `localagentcli/commands/agent.py` |
| Basic approval | Inline prompts for tool approval | `localagentcli/safety/approval.py` |

### Exit Criteria
- `/mode agent` + task input triggers the agent loop
- Agent generates and displays a plan
- Tools execute with approval prompts
- Agent completes multi-step tasks (e.g., "create a Python script and test it")
- `/agent stop` halts execution

### Dependencies
- Phase 4 (chat mode, mode switching, context management)

### Relevant Docs
- [agent-system.md](agent-system.md) — agent loop, events, controller
- [tool-system.md](tool-system.md) — tool definitions, output schema, registration

---

## Phase 6 — Safety

**Goal**: Robust safety controls, workspace boundaries, and rollback.

### Deliverables

| Component | Description | Key Files |
|---|---|---|
| Safety layer | Central approval gate | `localagentcli/safety/layer.py` |
| Approval manager | Mode-based approval logic | `localagentcli/safety/approval.py` |
| Workspace boundary | Path validation and enforcement | `localagentcli/safety/boundary.py` |
| Rollback manager | File backup and undo | `localagentcli/safety/rollback.py` |
| High-risk detection | Pattern matching for dangerous operations | `localagentcli/safety/layer.py` |

### Exit Criteria
- Write operations require approval in balanced mode
- `/agent approve` enables autonomy (except high-risk)
- Paths outside workspace are rejected
- File backups are created before modifications
- Undo restores files to previous state

### Dependencies
- Phase 5 (tools and agent loop must exist to apply safety to)

### Relevant Docs
- [safety-and-permissions.md](safety-and-permissions.md) — approval modes, boundaries, rollback

---

## Phase 7 — Packaging

**Goal**: Production-ready release on PyPI.

### Deliverables

| Component | Description |
|---|---|
| `pyproject.toml` | Complete package configuration with all dependencies |
| Auto-install | Backend dependencies installed on demand |
| Cross-platform testing | Verified on macOS, Linux, Windows |
| Full test suite | All test categories passing |
| Documentation | User-facing README with installation and usage |
| PyPI release | Published and installable via `pipx install localagentcli` |

### Exit Criteria
- All 9 Definition of Done criteria pass (see [packaging-and-release.md](packaging-and-release.md))
- `pipx install localagentcli` works on macOS, Linux, and Windows
- All automated tests pass
- No critical bugs

### Dependencies
- All previous phases

### Relevant Docs
- [packaging-and-release.md](packaging-and-release.md) — package structure, dependencies, testing, release process

---

## Phase Dependencies Graph

```
Phase 1 (Shell Core)
   │
   ├──→ Phase 2 (Remote Models)
   │       │
   │       ├──→ Phase 3 (Local Models)
   │       │       │
   │       └───────┤
   │               ▼
   │         Phase 4 (Chat Mode)
   │               │
   │               ▼
   │         Phase 5 (Agent System)
   │               │
   │               ▼
   │         Phase 6 (Safety)
   │               │
   │               ▼
   └─────→ Phase 7 (Packaging)
```

Phase 4 can begin as soon as either Phase 2 or Phase 3 is complete (only one working model source is needed). Phases 2 and 3 can be developed in parallel after Phase 1.
