# LocalAgentCLI — Current State

> **Last updated**: 2026-03-18 (Phase 6 complete — central safety layer, workspace boundary enforcement, high-risk detection, rollback, undo, and safety tests)
>
> This document tracks the implementation status of every component. Update it after completing any implementation work.

---

## How to Update

After implementing a component:
1. Change its status marker from `[ ]` to `[~]` (in progress) or `[x]` (done)
2. Add the date of the status change
3. Add brief notes if relevant (e.g., "partial — missing streaming support")
4. Commit this file as part of your implementation work

**Status markers:**
- `[ ]` — Not started
- `[~]` — In progress
- `[x]` — Done

---

## Phase 1 — Shell Core

| Status | Component | Notes |
|---|---|---|
| `[x]` | CLI entry point (`localagent` command) | 2026-03-17 |
| `[x]` | Shell UI (input loop, prompt) | 2026-03-17 |
| `[x]` | Command Router (parsing, dispatch) | 2026-03-17 |
| `[x]` | `/help` command | 2026-03-17 |
| `[x]` | `/exit` command | 2026-03-17 |
| `[x]` | `/status` command | 2026-03-17 |
| `[x]` | `/config` command | 2026-03-17 |
| `[x]` | `/setup` wizard | 2026-03-17 — simplified for Phase 1 (no model/provider) |
| `[x]` | Config system (TOML read/write) | 2026-03-17 |
| `[x]` | Config defaults and validation | 2026-03-17 |
| `[x]` | Session state dataclass | 2026-03-17 |
| `[x]` | Session manager (new/save/load/list/clear) | 2026-03-17 |
| `[x]` | Storage manager (directory init) | 2026-03-17 |
| `[x]` | Logger (file-based, leveled) | 2026-03-17 |

---

## Phase 2 — Remote Models

| Status | Component | Notes |
|---|---|---|
| `[x]` | Provider base class (ABC) | 2026-03-18 |
| `[x]` | Provider registry | 2026-03-18 |
| `[x]` | OpenAI-compatible provider | 2026-03-18 |
| `[x]` | Anthropic provider | 2026-03-18 |
| `[x]` | Generic REST provider | 2026-03-18 |
| `[x]` | API key manager (keychain + encrypted) | 2026-03-18 |
| `[x]` | `/providers add` command | 2026-03-18 |
| `[x]` | `/providers list` command | 2026-03-18 |
| `[x]` | `/providers remove` command | 2026-03-18 |
| `[x]` | `/providers use` command | 2026-03-18 |
| `[x]` | `/providers test` command | 2026-03-18 |
| `[x]` | SSE streaming support | 2026-03-18 |
| `[x]` | Model abstraction layer | 2026-03-18 |

---

## Phase 3 — Local Models

| Status | Component | Notes |
|---|---|---|
| `[x]` | Model registry (`registry.json`) | 2026-03-18 — ModelEntry dataclass, JSON persistence with filelock |
| `[x]` | Model installer (HF download) | 2026-03-18 — huggingface_hub.snapshot_download |
| `[x]` | Model installer (URL download) | 2026-03-18 — httpx streaming with resume support |
| `[x]` | Format detector (MLX/GGUF/safetensors) | 2026-03-18 — auto-detection pipeline |
| `[x]` | Backend base class (ABC) | 2026-03-17 — already existed from Phase 2 |
| `[x]` | MLX backend | 2026-03-18 — macOS Apple Silicon, lazy mlx-lm import |
| `[x]` | GGUF backend | 2026-03-18 — all platforms, lazy llama-cpp-python import |
| `[x]` | Safetensors backend | 2026-03-18 — all platforms, lazy torch/transformers import |
| `[x]` | Hardware detection and warnings | 2026-03-18 — CPU/RAM/GPU detection, >80% warning |
| `[x]` | `/models list` command | 2026-03-18 |
| `[x]` | `/models search` command | 2026-03-18 |
| `[x]` | `/models install` command | 2026-03-18 — hf and url subcommands |
| `[x]` | `/models remove` command | 2026-03-18 — with file cleanup |
| `[x]` | `/models use` command | 2026-03-18 — with hardware warnings |
| `[x]` | `/models inspect` command | 2026-03-18 |
| `[x]` | Model versioning | 2026-03-18 — auto-increment v1/v2, name@version syntax |

---

## Phase 4 — Chat Mode

| Status | Component | Notes |
|---|---|---|
| `[x]` | Chat controller | 2026-03-18 — `localagentcli/agents/chat.py` routes chat turns through the model abstraction layer |
| `[x]` | Streaming output renderer | 2026-03-18 — reasoning/activity-aware renderer in `localagentcli/shell/streaming.py` |
| `[x]` | Reasoning panel display | 2026-03-18 — buffered reasoning rendered in a distinct panel above assistant output |
| `[x]` | Context compactor (auto-summarization) | 2026-03-18 — `localagentcli/session/compactor.py` summarizes older history once context threshold is exceeded |
| `[x]` | Pinned instructions | 2026-03-18 — retained on `Session` and preserved by `ChatController` across compaction |
| `[x]` | `/mode chat` command | 2026-03-18 |
| `[x]` | `/mode agent` command | 2026-03-18 — mode switching implemented in Phase 4 and now activates the Phase 5 agent workflow |
| `[x]` | Status header display | 2026-03-18 — header shows mode, active model/provider target, and workspace |
| `[x]` | Input history (up/down arrows) | 2026-03-18 — prompt history is session-backed and persisted via session metadata |
| `[x]` | Tab completion for commands | 2026-03-18 — slash command and subcommand completion via prompt toolkit completer |

---

## Phase 5 — Agent System

| Status | Component | Notes |
|---|---|---|
| `[x]` | Tool base class (ABC) | 2026-03-18 |
| `[x]` | Tool registry | 2026-03-18 |
| `[x]` | `file_read` tool | 2026-03-18 |
| `[x]` | `file_search` tool | 2026-03-18 |
| `[x]` | `directory_list` tool | 2026-03-18 |
| `[x]` | `file_write` tool | 2026-03-18 |
| `[x]` | `patch_apply` tool | 2026-03-18 |
| `[x]` | `shell_execute` tool | 2026-03-18 |
| `[x]` | `test_execute` tool | 2026-03-18 |
| `[x]` | `git_status` tool | 2026-03-18 |
| `[x]` | `git_diff` tool | 2026-03-18 |
| `[x]` | `git_commit` tool | 2026-03-18 |
| `[x]` | Agent controller | 2026-03-18 — session-integrated task orchestration with persistence, compaction, approvals, and tool-result history |
| `[x]` | Agent loop (understand/plan/execute/observe) | 2026-03-18 — iterative per-step execution with tool calling, replanning, and completion/failure events |
| `[x]` | Task planner | 2026-03-18 — model-driven JSON plans with heuristic fallback and replan support |
| `[x]` | Agent events system | 2026-03-18 — structured plan, step, reasoning, tool, completion, and failure events rendered by the shell |
| `[x]` | `/agent approve` command | 2026-03-18 — resumes pending tool actions and can switch the current task to autonomous approvals |
| `[x]` | `/agent deny` command | 2026-03-18 — rejects the pending tool action and resumes the agent loop |
| `[x]` | `/agent stop` command | 2026-03-18 — stops the active agent task from the command layer or inline approval flow |

---

## Phase 6 — Safety

| Status | Component | Notes |
|---|---|---|
| `[x]` | Safety layer (central gate) | 2026-03-18 — `localagentcli/safety/layer.py` now validates boundaries, classifies risk, applies approval policy, and records rollback history around tool execution |
| `[x]` | Approval manager (balanced mode) | 2026-03-18 — central safety gate now enforces prompts for standard side-effecting actions and read-only high-risk actions |
| `[x]` | Approval manager (autonomous mode) | 2026-03-18 — autonomous mode auto-approves standard actions but still pauses high-risk operations for explicit approval |
| `[x]` | Approval UX (inline prompts) | 2026-03-18 — inline prompts now surface high-risk labels and outside-workspace warnings from the safety layer |
| `[x]` | Workspace boundary enforcement | 2026-03-18 — dedicated `WorkspaceBoundary` enforces root confinement for tool paths and shell working directories |
| `[x]` | Symlink validation | 2026-03-18 — symlinks resolving outside the workspace root are blocked centrally and in shared path resolution helpers |
| `[x]` | High-risk action detection | 2026-03-18 — shell commands and sensitive file paths are classified centrally so high-risk actions always require approval |
| `[x]` | Rollback manager (file backups) | 2026-03-18 — `RollbackManager` stores per-session backups and a JSON rollback log under `cache/rollback/` |
| `[x]` | Undo capability | 2026-03-18 — rollback history supports `undo_last()` and `undo_all()` restoration for modified and newly created files |

---

## Phase 7 — Packaging

| Status | Component | Notes |
|---|---|---|
| `[x]` | `pyproject.toml` configuration | 2026-03-17 |
| `[ ]` | Backend auto-install on demand | |
| `[x]` | Unit tests | 2026-03-18 — 619 tests |
| `[ ]` | Integration tests | |
| `[ ]` | CLI tests | |
| `[x]` | Agent workflow tests | 2026-03-18 — planner, controller, shell integration, provider tool-calling, and `/agent` command coverage added |
| `[x]` | Safety tests | 2026-03-18 — added boundary, rollback, safety-layer, and high-risk approval coverage |
| `[x]` | Cross-platform testing (macOS) | 2026-03-17 — via CI matrix |
| `[x]` | Cross-platform testing (Linux) | 2026-03-17 — via CI matrix |
| `[ ]` | Cross-platform testing (Windows) | |
| `[ ]` | PyPI release | |

---

## CI / Workflows

| Status | Component | Notes |
|---|---|---|
| `[x]` | `.github/workflows/test.yml` | 2026-03-17 — pytest + coverage, matrix: ubuntu/macos × py3.11-3.13 |
| `[x]` | `.github/workflows/lint.yml` | 2026-03-17 — ruff check + format |
| `[x]` | `.github/workflows/typecheck.yml` | 2026-03-17 — mypy |

---

## Documentation

| Status | Component | Notes |
|---|---|---|
| `[x]` | `docs/architecture.md` | Complete |
| `[x]` | `docs/commands.md` | Complete |
| `[x]` | `docs/model-system.md` | Complete |
| `[x]` | `docs/remote-providers.md` | Complete |
| `[x]` | `docs/agent-system.md` | Complete |
| `[x]` | `docs/tool-system.md` | Complete |
| `[x]` | `docs/safety-and-permissions.md` | Complete |
| `[x]` | `docs/session-and-config.md` | Complete |
| `[x]` | `docs/cli-and-ux.md` | Complete |
| `[x]` | `docs/storage-and-logging.md` | Complete |
| `[x]` | `docs/packaging-and-release.md` | Complete |
| `[x]` | `docs/roadmap.md` | Complete |
| `[x]` | `docs/current-state.md` | Complete |
| `[x]` | `CLAUDE.md` | Complete — includes testing/CI requirements |
| `[x]` | `AGENTS.md` | Complete — includes testing/CI requirements |
