# LocalAgentCLI — Current State

> **Last updated**: 2026-03-18 (Phase 2 complete — remote providers, streaming, key management)
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
| `[ ]` | Model registry (`registry.json`) | |
| `[ ]` | Model installer (HF download) | |
| `[ ]` | Model installer (URL download) | |
| `[ ]` | Format detector (MLX/GGUF/safetensors) | |
| `[ ]` | Backend base class (ABC) | |
| `[ ]` | MLX backend | |
| `[ ]` | GGUF backend | |
| `[ ]` | Safetensors backend | |
| `[ ]` | Hardware detection and warnings | |
| `[ ]` | `/models list` command | |
| `[ ]` | `/models search` command | |
| `[ ]` | `/models install` command | |
| `[ ]` | `/models remove` command | |
| `[ ]` | `/models use` command | |
| `[ ]` | `/models inspect` command | |
| `[ ]` | Model versioning | |

---

## Phase 4 — Chat Mode

| Status | Component | Notes |
|---|---|---|
| `[ ]` | Chat controller | |
| `[ ]` | Streaming output renderer | |
| `[ ]` | Reasoning panel display | |
| `[ ]` | Context compactor (auto-summarization) | |
| `[ ]` | Pinned instructions | |
| `[ ]` | `/mode chat` command | |
| `[ ]` | `/mode agent` command | |
| `[ ]` | Status header display | |
| `[ ]` | Input history (up/down arrows) | |
| `[ ]` | Tab completion for commands | |

---

## Phase 5 — Agent System

| Status | Component | Notes |
|---|---|---|
| `[ ]` | Tool base class (ABC) | |
| `[ ]` | Tool registry | |
| `[ ]` | `file_read` tool | |
| `[ ]` | `file_search` tool | |
| `[ ]` | `directory_list` tool | |
| `[ ]` | `file_write` tool | |
| `[ ]` | `patch_apply` tool | |
| `[ ]` | `shell_execute` tool | |
| `[ ]` | `test_execute` tool | |
| `[ ]` | `git_status` tool | |
| `[ ]` | `git_diff` tool | |
| `[ ]` | `git_commit` tool | |
| `[ ]` | Agent controller | |
| `[ ]` | Agent loop (understand/plan/execute/observe) | |
| `[ ]` | Task planner | |
| `[ ]` | Agent events system | |
| `[ ]` | `/agent approve` command | |
| `[ ]` | `/agent deny` command | |
| `[ ]` | `/agent stop` command | |

---

## Phase 6 — Safety

| Status | Component | Notes |
|---|---|---|
| `[ ]` | Safety layer (central gate) | |
| `[ ]` | Approval manager (balanced mode) | |
| `[ ]` | Approval manager (autonomous mode) | |
| `[ ]` | Approval UX (inline prompts) | |
| `[ ]` | Workspace boundary enforcement | |
| `[ ]` | Symlink validation | |
| `[ ]` | High-risk action detection | |
| `[ ]` | Rollback manager (file backups) | |
| `[ ]` | Undo capability | |

---

## Phase 7 — Packaging

| Status | Component | Notes |
|---|---|---|
| `[x]` | `pyproject.toml` configuration | 2026-03-17 |
| `[ ]` | Backend auto-install on demand | |
| `[x]` | Unit tests | 2026-03-18 — 384 tests, 93% coverage |
| `[ ]` | Integration tests | |
| `[ ]` | CLI tests | |
| `[ ]` | Agent workflow tests | |
| `[ ]` | Safety tests | |
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
