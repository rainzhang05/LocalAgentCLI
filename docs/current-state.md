# LocalAgentCLI — Current State

> **Last updated**: 2026-03-22 — **Phase 8 slice 1 (operator-state visibility, shipped):** agent runtime phases now include explicit `retrying`, and `agent_task_state` persists `wait_reason`, `retry_count`, and `last_error` alongside route/phase/step/pending-tool metadata. Prompt toolbar and `/status` now surface this richer state so operators can distinguish waiting approval vs retrying vs recovering quickly. **Phase 7 readiness-depth + transport follow-ons (shipped):** readiness now reports operator posture (`ready`, `degraded`, `blocked`) plus tradeoff and next-step guidance; `/mode agent` and dispatch-time runtime gating surface posture/tradeoff details, and `/providers list` + `/providers test` include clearer readiness state context. Provider async streams now enforce optional idle-timeout guards and can apply configurable per-turn connection policy (`reuse` / `close_after_turn`) while preserving normalized `error` + `done` chunks. **Async runtime (shipped):** interactive shell and `exec` run under `asyncio.run`; `SessionRuntime.aiter_events` drives turns; `iter_events()` remains as a compatibility bridge but now emits `DeprecationWarning`; remote providers use async HTTP with cooperative cancel; `ModelAbstractionLayer.astream_generate` / `agenerate` bridge local sync backends off the event loop; agent dispatch re-checks remote model readiness (parity with `/mode agent`); provider cache invalidates on config/model binding. **Safety:** typed `SandboxPosture` / `parse_sandbox_mode` (`localagentcli/safety/posture.py`), config validation aligned; docs describe application-layer containment vs no OS-level shell/MCP isolation; extended high-risk shell patterns (`chmod`/`777`, `docker rm|rmi|system prune`, `kubectl delete`). **MCP (stdio):** per-request read timeouts, subprocess env merged with `os.environ` when `[mcp_servers.*].env` is set, deterministic disambiguation when sanitized MCP tool names collide; product doc `docs/mcp.md` describes configuration, safety, and intentional skills posture (`AGENTS.md` + pinned instructions; no separate skills runtime). **Session durability (shipped):** JSON session files (`format_version`), optional named autosave, append-only runtime JSONL under the cache dir (not merged into chat history). **Agent tools:** read-only parallel batches use a bounded pool (up to 16 workers) so concurrent I/O-bound tools still run on single-CPU hosts. Also: exec persist-on-exit, fork metadata, sandbox, shell/streaming polish.)
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
| `[x]` | CLI entry point (`localagentcli` command, `localagent` alias) | 2026-03-21 — entry bootstrap now supports the interactive shell plus a richer `localagentcli exec` surface with `chat`/`agent` modes, JSON event output, headless approval policy, and saved-session resume/fork flows, all reusing the shared runtime stack |
| `[x]` | Shell UI (input loop, prompt) | 2026-03-21 — prompt shows a live slash-command menu with arrow-key selection, keeps matching options visible while editing/backspacing across root and nested pickers, debounces completion menu refreshes during typing when the toolkit app loop is available, auto-loads repository-root `AGENTS.md` instructions, exits on consecutive idle `Ctrl+C` presses without a save prompt, exposes a persistent prompt-time status toolbar (local target label from registry metadata only, without repeated on-disk detection on each toolbar paint) that can surface agent route/phase and undo availability alongside shared action/confirm prompts, and now consumes the shared submission/event runtime rather than directly orchestrating turns itself |
| `[x]` | Command Router (parsing, dispatch) | 2026-03-17 |
| `[x]` | `/help` command | 2026-03-19 — grouped help, command-specific help, and slash-menu metadata are all driven by per-command `CommandSpec` declarations, and router-level unknown/invalid command errors now include consistent `/help` guidance plus close-match suggestions when available |
| `[x]` | `/exit` command | 2026-03-17 |
| `[x]` | `/status` command | 2026-03-22 — `/status` now renders the expanded form of the same shared status snapshot used by the prompt toolbar, including route/phase/step, pending tool, wait reason, retry count, last error, and undo-ready counts; in agent mode, idle placeholders keep that section layout stable even when no task is active |
| `[x]` | `/config` command | 2026-03-19 — `/config` now opens an interactive schema-aware editor in TTY mode while keeping explicit dotted-key reads/writes for scripted use, and free-form edits now use the shared text-prompt helper |
| `[x]` | `/setup` wizard | 2026-03-19 — simplified for Phase 1 (workspace, mode, logging level), now uses the shared prompt contract for wizard questions, and still falls back to persisted defaults in non-interactive launches |
| `[x]` | Config system (TOML read/write) | 2026-03-17 |
| `[x]` | Config defaults and validation | 2026-03-22 — `safety.sandbox_mode` validated via `parse_sandbox_mode`; `mcp_servers` tables; `[sessions].autosave_named` and `autosave_debounce_seconds` with bool/string coercion for autosave |
| `[x]` | Session state dataclass | 2026-03-17 |
| `[x]` | Session manager (new/save/load/list/clear) | 2026-03-21 — default-target repair; exec resume/fork; fork lineage metadata; exec persist-on-exit; `format_version` on save; opt-in debounced named autosave and flush from shell drain/exit; chat/agent controllers notify the scheduler when wired from `SessionExecutionRuntime` |
| `[x]` | Storage manager (directory init) | 2026-03-17 |
| `[x]` | Logger (file-based, leveled) | 2026-03-17 |

---

## Phase 2 — Remote Models

| Status | Component | Notes |
|---|---|---|
| `[x]` | Provider base class (ABC) | 2026-03-18 |
| `[x]` | Provider registry | 2026-03-18 |
| `[x]` | OpenAI-compatible provider | 2026-03-19 — model list now comes from the provider `GET /models` response with default-model fallback, streamed tool-call deltas are accumulated, capability checks are resolved per selected model id, and discovered models now carry inferred-vs-fallback readiness provenance |
| `[x]` | Anthropic provider | 2026-03-19 — model list and connection test now use the live `GET /v1/models` API with default-model fallback, mixed text/thinking/tool blocks are preserved in order for non-streaming and streaming paths, and discovered models now carry inferred-vs-fallback readiness provenance |
| `[x]` | Generic REST provider | 2026-03-19 — configurable model discovery endpoint/fields now back provider model selection, with default-model fallback plus optional mapped reasoning/tool-call fields, and discovery results now label configured-vs-fallback readiness provenance |
| `[x]` | API key manager (keychain + encrypted) | 2026-03-18 |
| `[x]` | `/providers add` command | 2026-03-19 — provider type/name/base URL/API key/test-now prompts now share the same picker/text/secret/confirm contract as the rest of the shell |
| `[x]` | `/providers list` command | 2026-03-22 — now shows selected model context plus discovery state and readiness posture (`ready`/`degraded`/`blocked`) when known |
| `[x]` | `/providers remove` command | 2026-03-18 |
| `[x]` | `/providers use` command | 2026-03-19 — retained as a hidden compatibility alias behind `/set`, now explicitly states whether it auto-bound a live-discovered model or only a legacy fallback |
| `[x]` | `/set` target-selection command | 2026-03-19 — unified picker for local models and provider models, with provider model selection starting empty instead of prefilled and picker descriptions now surfacing readiness tiers and discovery state; legacy-fallback provider selections now add explicit post-selection guidance to refresh discovery and pick an API-discovered model |
| `[x]` | `/providers test` command | 2026-03-22 — reports connectivity, discovery state, selected-model readiness posture, and tradeoff guidance (in addition to legacy-fallback recovery phrasing and provider-creation failure context) |
| `[x]` | SSE streaming support | 2026-03-19 — normalized chunk pipeline now preserves final text, reasoning, tool calls, notifications, errors, and done events consistently across providers |
| `[x]` | Model abstraction layer | 2026-03-19 — `generate()` now collects the same normalized stream pipeline used by `stream_generate()` |

---

## Phase 3 — Local Models

| Status | Component | Notes |
|---|---|---|
| `[x]` | Model registry (`registry.json`) | 2026-03-19 — ModelEntry now persists capability provenance alongside boolean capability flags, with backwards-compatible defaults for older registry entries |
| `[x]` | Model installer (HF download) | 2026-03-19 — Hugging Face Hub download with live per-file progress when dry-run planning is available, faster fallback progress refresh, compatibility with newer `huggingface_hub` progress kwargs, conservative capability inference during registration, and local readiness provenance recording |
| `[x]` | Model installer (URL download) | 2026-03-19 — httpx streaming with resume support and continuously refreshed progress output |
| `[x]` | Format detector (MLX/GGUF/safetensors) | 2026-03-19 — auto-detection pipeline with unsupported-backend-aware repair for stale registry entries |
| `[x]` | Backend base class (ABC) | 2026-03-17 — already existed from Phase 2 |
| `[x]` | MLX backend | 2026-03-19 — macOS Apple Silicon, lazy mlx-lm import, sampler-based generation compatibility, and best-effort cancellation hook |
| `[x]` | GGUF backend | 2026-03-19 — all platforms, lazy llama-cpp-python import, and best-effort cancellation hook |
| `[x]` | Safetensors backend | 2026-03-19 — all platforms, lazy torch/transformers import, plus threaded-stream cancellation via stopping criteria |
| `[x]` | Hardware detection and warnings | 2026-03-18 — CPU/RAM/GPU detection, >80% warning |
| `[x]` | `/models list` command | 2026-03-19 — now adds a compact readiness column for agent availability/confidence |
| `[x]` | `/models search` command | 2026-03-18 |
| `[x]` | `/models install` command | 2026-03-19 — hf and url subcommands, plus `/models` layered picker backed by live Hugging Face family/model discovery across many families |
| `[x]` | `/models remove` command | 2026-03-18 — with file cleanup |
| `[x]` | `/models use` command | 2026-03-18 — hidden compatibility alias behind `/set`, still supports direct selection with hardware warnings |
| `[x]` | `/models inspect` command | 2026-03-19 — now renders per-capability readiness lines with both tier and rationale instead of raw booleans |
| `[x]` | Model versioning | 2026-03-18 — auto-increment v1/v2, name@version syntax |

---

## Phase 4 — Chat Mode

| Status | Component | Notes |
|---|---|---|
| `[x]` | Chat controller | 2026-03-18 — `localagentcli/agents/chat.py` routes chat turns through the model abstraction layer |
| `[x]` | Streaming output renderer | 2026-03-21 — renderer now owns the shared output contract for status, success, warning, error, and secondary-detail lanes; late-arriving secondary detail is flushed once at safe boundaries instead of disappearing after the first primary text; consecutive neutral status lines coalesce (one Details panel per batch, deduped identical neighbors) with explicit flush at agent-event tail from the shell; step/task activity wording remains normalized (`Step N started`, `Task completed`) |
| `[x]` | Reasoning panel display | 2026-03-19 — chat, direct-answer, and planned-agent reasoning now all use the same dimmed `Details` lane rather than mixing separate reasoning presentations |
| `[x]` | Context compactor (auto-summarization) | 2026-03-21 — `localagentcli/session/compactor.py` + `session/tokens.py`: UTF-8 byte ceiling token estimate, optional generation headroom before threshold, shared `build_conversation_model_messages` in `session/instructions.py` |
| `[x]` | Pinned instructions | 2026-03-19 — retained on `Session`, combined with auto-detected repository `AGENTS.md` instructions, and preserved by `ChatController` across compaction |
| `[x]` | `/mode chat` command | 2026-03-19 — mode changes now use shared success/warning presentation for normal switches and cancelled stop-confirmation paths |
| `[x]` | `/mode agent` command | 2026-03-22 — rejections now include readiness posture and tradeoff messaging (`chat available` vs `agent blocked`) plus next-step guidance; still rejects untrusted remote fallback states and preserves shared command presentation |
| `[x]` | Status header display | 2026-03-19 — replaced by a persistent prompt-time status toolbar showing mode, active target, workspace, and a short hint; `/status` uses the same snapshot data in expanded form, and the toolbar now keeps agent-mode state explicit with an `agent: idle` label when no task is running |
| `[x]` | Input history (up/down arrows) | 2026-03-18 — prompt history is session-backed and persisted via session metadata |
| `[x]` | Tab completion for commands | 2026-03-18 — live slash-command menu, typed filtering, arrow-key navigation, and Tab acceptance via prompt-toolkit |

---

## Phase 5 — Agent System

| Status | Component | Notes |
|---|---|---|
| `[x]` | Tool base class (ABC) | 2026-03-18 |
| `[x]` | Tool registry | 2026-03-22 — `ToolRouter` merges built-in, dynamic, and MCP stdio tools; `tools/schema.py` validates `parameters_schema` on `Tool.definition()` and dynamic registration; MCP client honors `timeout`, merges env with parent process, and avoids qualified-name collisions |
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
| `[x]` | Agent controller | 2026-03-19 — session-integrated task orchestration now includes triage-based direct-answer fast path, single-step synthesis, controller reuse, remote capability validation by selected model id, interruption-aware cancellation, persisted `agent_task_state` snapshots, and explicit stopped vs timed-out vs failed outcomes |
| `[x]` | Agent loop (understand/plan/execute/observe) | 2026-03-21 — step prompts may append **Agent task status (runtime):** from `session.metadata["agent_task_state"]` (`task_context.py`, `AgentLoop.run(..., session=...)`); eligible multi-call read-only batches run concurrently with `min(batch_size, 16)` thread-pool workers (`agents/loop.py`) |
| `[x]` | Task planner | 2026-03-19 — model-driven JSON plans with heuristic fallback and replan support, now generating only the minimum number of steps needed instead of a fixed 2-6 step shape |
| `[x]` | Agent events system | 2026-03-19 — structured route, phase, plan, step, reasoning, tool, completion, stopped, timeout, and failure events rendered by the shell, with approval-risk and rollback-preview metadata flowing into the renderer |
| `[x]` | `/agent approve` command | 2026-03-19 — resumes pending tool actions and now persists autonomous approvals across future tasks in the shell and future sessions while still forcing explicit approval for high-risk actions |
| `[x]` | `/agent deny` command | 2026-03-19 — rejects the pending tool action and returns the loop to recovery/replanning as needed |
| `[x]` | `/agent undo` command | 2026-03-19 — reverts the most recent rollback entry recorded for the current session and refuses to run while an agent task is active |
| `[x]` | `/agent undo-all` command | 2026-03-19 — reverts all rollback entries recorded for the current session in reverse order and refuses to run while an agent task is active |
| `[x]` | Ctrl+C agent stop path | 2026-03-19 — stops the active agent task from the shell, cancels active generation when supported, records a warning-style stop state instead of a generic failure, and exits the idle shell after a consecutive double press without prompting to save |

---

## Phase 6 — Safety

| Status | Component | Notes |
|---|---|---|
| `[x]` | Safety layer (central gate) | 2026-03-22 — `localagentcli/safety/layer.py` uses `SandboxPosture` for runtime sandbox checks; validates boundaries, classifies risk, explains high-risk flags, describes rollback availability, applies approval policy, records rollback history; `read-only` posture blocks side-effecting tools even in autonomous mode |
| `[x]` | Approval manager (balanced mode) | 2026-03-18 — central safety gate now enforces prompts for standard side-effecting actions and read-only high-risk actions |
| `[x]` | Approval manager (autonomous mode) | 2026-03-19 — autonomous mode auto-approves standard actions, persists correctly across future tasks, and still pauses high-risk operations for explicit approval |
| `[x]` | Approval UX (inline prompts) | 2026-03-20 — inline prompts flush pending renderer detail before blocking for input, use the shared action-prompt surface for approve/deny/details/approve-all, and render tool-specific previews with target, risk, warning, overwrite/create, and rollback context, plus explicit truncation labels for long preview sections |
| `[x]` | Workspace boundary enforcement | 2026-03-18 — dedicated `WorkspaceBoundary` enforces root confinement for tool paths and shell working directories |
| `[x]` | Symlink validation | 2026-03-18 — symlinks resolving outside the workspace root are blocked centrally and in shared path resolution helpers |
| `[x]` | High-risk action detection | 2026-03-22 — shell commands (including extended patterns for permissive `chmod`, destructive `docker`/`kubectl` verbs) and sensitive file paths are classified centrally so high-risk actions always require approval |
| `[x]` | Rollback manager (file backups) | 2026-03-18 — `RollbackManager` stores per-session backups and a JSON rollback log under `cache/rollback/` |
| `[x]` | Undo capability | 2026-03-19 — rollback history supports `undo_last()` and `undo_all()` restoration for modified and newly created files, with Windows-safe modified-file restore behavior plus explicit `/agent undo` and `/agent undo-all` command surfaces |

---

## Phase 7 — Packaging

| Status | Component | Notes |
|---|---|---|
| `[x]` | `pyproject.toml` configuration | 2026-03-18 — production metadata, project URLs, license files, classifiers, and release tooling extras added |
| `[x]` | Backend auto-install on demand | 2026-03-18 — shell prompts to install missing MLX/GGUF/Torch dependencies and installs direct backend requirements before retrying model load |
| `[x]` | Unit tests | 2026-03-22 — full suite includes MCP env merge, approval/sandbox integration for MCP tools, colliding sanitized MCP name disambiguation, `SandboxPosture`/read-only sandbox tests, and config `safety.sandbox_mode` validation; run `pytest --cov` for current counts and coverage |
| `[x]` | Integration tests | 2026-03-18 — setup/save/load and backend auto-install flows covered in `tests/integration/test_packaging_flows.py` |
| `[x]` | CLI tests | 2026-03-18 — subprocess coverage for interactive and non-interactive first-run setup, session restore, single- and double-`Ctrl+C` handling in `tests/cli/test_packaging_cli.py`, with a Windows-safe non-interactive interrupt path |
| `[x]` | Agent workflow tests | 2026-03-18 — planner, controller, shell integration, provider tool-calling, and `/agent` command coverage added |
| `[x]` | Safety tests | 2026-03-22 — boundary, rollback, safety-layer, high-risk approval, read-only sandbox blocks, `parse_sandbox_mode`, and extended shell pattern coverage |
| `[x]` | Cross-platform testing (macOS) | 2026-03-17 — via CI matrix |
| `[x]` | Cross-platform testing (Linux) | 2026-03-17 — via CI matrix |
| `[x]` | Cross-platform testing (Windows) | 2026-03-18 — added `windows-latest` to the GitHub Actions test matrix |
| `[~]` | PyPI release | 2026-03-18 — build artifacts, README rendering, `twine check`, publish workflow, and local `pipx` install are validated; final upload still requires PyPI/TestPyPI project setup, trusted-publisher configuration, and a pushed release tag |

---

## CI / Workflows

| Status | Component | Notes |
|---|---|---|
| `[x]` | `.github/workflows/test.yml` | 2026-03-18 — pytest + coverage on ubuntu/macos/windows × py3.11-3.13, plus package build, `twine check`, and `pipx` smoke verification through the resolved `PIPX_BIN_DIR` entrypoint |
| `[x]` | `.github/workflows/lint.yml` | 2026-03-17 — ruff check + format |
| `[x]` | `.github/workflows/typecheck.yml` | 2026-03-17 — mypy |
| `[x]` | `.github/workflows/publish.yml` | 2026-03-18 — build, artifact validation, `pipx` smoke test via resolved `PIPX_BIN_DIR`, and trusted publishing paths for TestPyPI/PyPI |

---

## Documentation

| Status | Component | Notes |
|---|---|---|
| `[x]` | `docs/architecture.md` | Complete |
| `[x]` | `docs/commands.md` | 2026-03-19 — `/set default`, interactive `/config`, always-available `/hf-token`, shared command metadata, renderer-backed command presentation, readiness-aware target selection, and provider discovery messaging documented |
| `[x]` | `docs/model-system.md` | 2026-03-19 — normalized stream chunk schema, shared generation collector, conservative capability inference, local capability-provenance storage, backend cancellation behavior, and editable Hugging Face token flow documented |
| `[x]` | `docs/remote-providers.md` | 2026-03-22 — adds readiness posture/tradeoff guidance surfaces and operator-facing behavior for `/mode agent`, runtime dispatch checks, `/providers list`, and `/providers test` |
| `[x]` | `docs/agent-system.md` | 2026-03-22 — entry requirements now explicitly include posture/tradeoff wording for readiness-based agent-mode rejections |
| `[x]` | `docs/tool-system.md` | 2026-03-22 — links to MCP extensibility doc; parameter schema and parallel read-only batch rules unchanged |
| `[x]` | `docs/mcp.md` | 2026-03-22 — stdio MCP config, naming, `readOnlyHint`, sandbox and approval behavior, limitations, project guidance vs future skills |
| `[x]` | `docs/safety-and-permissions.md` | 2026-03-22 — MCP `readOnlyHint` approval table rows; autonomous mode wording includes MCP tools that require approval |
| `[x]` | `docs/session-and-config.md` | 2026-03-22 — `[mcp_servers]` example comments reference MCP doc and optional `env` / `timeout` keys |
| `[x]` | `docs/cli-and-ux.md` | 2026-03-20 — primary vs secondary output rendering, dimmed `Details` panel, prompt-time status toolbar, agent route/phase/undo status surfaces, shared prompt helpers, renderer-backed command-result presentation, and truncated approval preview behavior documented |
| `[x]` | `docs/storage-and-logging.md` | Complete |
| `[x]` | `docs/packaging-and-release.md` | 2026-03-18 — release checklist, trusted-publishing prerequisites, `pipx` smoke path guidance, non-interactive first-run setup expectations, and local wheel refresh command documented |
| `[x]` | `docs/roadmap.md` | Complete |
| `[x]` | `docs/current-state.md` | Complete |
| `[x]` | `README.md` | 2026-03-18 — install, usage, backend, development, and release validation instructions refreshed |
| `[x]` | `CHANGELOG.md` | 2026-03-18 — release notes expanded for hardening and packaging work |
| `[x]` | `CLAUDE.md` | Complete — includes testing/CI requirements |
| `[x]` | `AGENTS.md` | Complete — includes testing/CI requirements |
