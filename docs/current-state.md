# LocalAgentCLI — Current State

> **Last updated**: 2026-03-26 — **Phase 14 remaining baselines landed:** MCP OAuth browser flow now has a baseline `/mcp oauth <server>` surface (authorization-code + PKCE + token exchange + secure token storage), and remote plugin/skills sync now has baseline commands (`/plugin sync-remote`, `/skills sync-remote`) using JSON manifest URLs. **Phase 14 follow-on baselines landed:** plugin lifecycle includes workspace candidate discovery and sync (`/plugin discover`, `/plugin sync`) in addition to local install/list/remove, and the default toolset includes `python_repl_execute` (subprocess Python execution) as a practical code-execution baseline diverging from Codex's V8 embedding approach. **Phase 14 slice 5 baseline increment (shipped):** local skills runtime supports skill installation/list/removal (`/skills list|install|remove`) with filesystem-backed storage (`~/.localagent/skills`), workspace discovery of `SKILL.md` files in common project skill directories (`skills/`, `.skills/`, `.github/skills/`), and skill-as-prompt-overlay integration via session instruction sync. **Phase 14 slice 4 baseline increment (shipped):** local plugin management has a baseline implementation with filesystem-backed plugin storage (`~/.localagent/plugins`) and command surfaces for list/install/remove (`/plugin list`, `/plugin install`, `/plugin remove`). **Phase 14 slices 2-3 baseline increment (shipped):** MCP auth and elicitation foundations are in place: HTTP/SSE MCP requests resolve bearer tokens from secure local key storage (`/mcp login`, `/mcp logout`) in addition to env/header config, and MCP tool calls support baseline schema-driven elicitation callbacks routed through shell prompts when servers request additional operator input. **Phase 14 slice 1 + slice 7 foundation (shipped):** MCP transport support includes `stdio`, `http`, and `sse` in `localagentcli/mcp/client.py`, and agent dispatch can refresh the active tool router at turn boundaries when `features.mcp_tool_inventory_refresh` is enabled (via `runtime/core.py` + controller/loop tool-registry refresh hooks). Full provider-specific OAuth device-flow automation and richer cloud-backed plugin/skills ecosystem features remain deferred follow-on slices. **Phase 11 slice 11 (unified turn loop, shipped):** `AgentLoop` now supports unified multi-round step execution with a configurable per-step round budget (`max_step_rounds`) and unified-loop mode enabled by default, allowing many model↔tool rounds in-step before completion/failure without requiring a separate planner round-trip. **Phase 11 slice 8 (streaming shell execution, shipped):** `shell_execute` now uses PTY-backed incremental capture on POSIX (with bounded output buffering and timeout-safe termination) plus cross-platform fallback behavior, replacing direct blocking `subprocess.run(capture_output=True)` as the primary path. **Phase 11 slice 7 (robust patch apply, shipped):** `patch_apply` supports diff-style patch operations with optional `@@` anchors, context-aware matching, and indentation-tolerant replacement while preserving legacy exact single-match `old_text/new_text` behavior. **Phase 11 slice 6 (model-info-driven tool adaptation, shipped):** tool definitions are adapted per active `ModelInfo` (tool-use capability gating, required capability tags, minimum token-budget thresholds) via `ToolRegistry`/`ToolRouter` before each model round. **Phase 11 slice 10 (reasoning effort controls, shipped):** LocalAgentCLI supports configurable `generation.reasoning_effort` (`low` / `medium` / `high`) in shared runtime generation options and agent profiles, with model-aware filtering and OpenAI-compatible request pass-through only on reasoning-capable models. **Phase 11 slice 9 (adaptive semantic truncation, shipped):** agent tool-observation payloads use model-aware adaptive middle truncation instead of a fixed 4000-character slice and include explicit truncation metadata; context compaction transcript formatting drops redundant middle turns and preserves tool schema signal when summarizing older history. **Phase 11 slice 5 (provider prompt caching, shipped):** Anthropic requests support prompt caching by wrapping system prompts in cache-controlled text blocks when `providers.<name>.options.prompt_cache` is enabled (optional `prompt_cache_type`, default `ephemeral`); OpenAI-compatible providers support explicit prompt-cache pass-through fields (`prompt_cache`, `prompt_cache_key`) for compatible backends when configured. **Phase 11 slice 3 (planner round-trip elimination, shipped):** agent dispatch always seeds execution with a local bootstrap plan and no longer performs a separate model planning call before first step execution; `AgentLoop.run/arun` also bootstrap a local plan when invoked without one. Planning model calls remain only for recovery/replanning on repeated failures. **Phase 11 slice 4 (enriched step prompt layering, shipped):** `AgentLoop` step prompts use a structured execution brief (execution rules + output contract + task objective + plan status + current step focus), while still layering repository/pinned instructions and environment context in the same top-level system message. **Phase 11 slice 2 (context preservation parity, shipped):** agent triage and planning preserve leading system context (workspace instructions/environment) when tail-windowing long conversations, and `AgentLoop` merges transcript system context into one primary step-system message with session-based instructions/environment fallback when upstream transcript system content is absent. **Phase 11 slice 1 (generation profile parity, shipped):** controller and loop share one model-aware generation profile builder (`localagentcli/agents/profiles.py`), removing loop hardcoded `max_tokens` fallback values and using `ModelInfo.default_max_tokens` when explicit generation options are absent. **Phase 9 slice 3 (richer telemetry recap, shipped):** installer telemetry emits schema `v2` records with explicit completion-path signals and file/download cache counters, and completion summaries include path/total/downloaded/cached/cache-hit context when available (including URL resume/already-complete paths). **Phase 9 slice 2 (installer telemetry follow-on, shipped):** model installs append JSONL telemetry records to `~/.localagent/cache/downloads/install_telemetry.jsonl` for both Hugging Face and URL paths (including failed download attempts), persist compact download telemetry metadata on registered model entries for successful installs, and print a concise post-install telemetry summary line (source, elapsed time, downloaded/cached bytes, average speed, and file cache counts when available). **Phase 9 slice 1 (terminal resilience baseline, shipped):** stream status/details/plan output applies width-safe truncation with ellipsis on narrow terminals; prompt-toolkit command/selection menus reduce reserved completion height under narrow widths; installer live-progress file labels are width-safe and truncate with ellipsis when needed. Existing adaptive status pacing, persistent details lane option, and unicode symbol fallback remain unchanged. **Phase 8 slice 2 (operator-state runtime UI pacing + persistent lane exploration, shipped):** stream status batching uses adaptive catch-up hysteresis under backlog pressure, and the details lane can run in opt-in persistent mode via `shell.persistent_details_lane` so secondary context stays visible at each flush boundary. **Phase 8 slice 1 (operator-state visibility, shipped):** agent runtime phases include explicit `retrying`, and `agent_task_state` persists `wait_reason`, `retry_count`, and `last_error` alongside route/phase/step/pending-tool metadata. Prompt toolbar and `/status` surface this richer state so operators can distinguish waiting approval vs retrying vs recovering quickly. **Phase 7 readiness-depth + transport follow-ons (shipped):** readiness reports operator posture (`ready`, `degraded`, `blocked`) plus tradeoff and next-step guidance; `/mode agent` and dispatch-time runtime gating surface posture/tradeoff details, and `/providers list` + `/providers test` include clearer readiness state context. Provider async streams enforce optional idle-timeout guards and can apply configurable per-turn connection policy (`reuse` / `close_after_turn`) while preserving normalized `error` + `done` chunks. **Async runtime (shipped):** interactive shell and `exec` run under `asyncio.run`; `SessionRuntime.aiter_events` drives turns; `iter_events()` remains as a compatibility bridge but now emits `DeprecationWarning`; remote providers use async HTTP with cooperative cancel; `ModelAbstractionLayer.astream_generate` / `agenerate` bridge local sync backends off the event loop; agent dispatch re-checks remote model readiness (parity with `/mode agent`); provider cache invalidates on config/model binding. **Safety:** typed `SandboxPosture` / `parse_sandbox_mode` (`localagentcli/safety/posture.py`), config validation aligned; docs describe application-layer containment vs no OS-level shell/MCP isolation; extended high-risk shell patterns (`chmod`/`777`, `docker rm|rmi|system prune`, `kubectl delete`). **MCP:** per-request read timeouts, subprocess env merged with `os.environ` when `[mcp_servers.*].env` is set, deterministic disambiguation when sanitized MCP tool names collide, transport expansion to HTTP/SSE, key-managed bearer tokens, OAuth code-flow baseline, and elicitation callbacks. **Session durability (shipped):** JSON session files (`format_version`), optional named autosave, append-only runtime JSONL under the cache dir (not merged into chat history). **Agent tools:** read-only parallel batches use a bounded pool (up to 16 workers) so concurrent I/O-bound tools still run on single-CPU hosts. **Packaging/test portability follow-on (shipped):** the environment-context cwd assertion derives its expected absolute path via `Path.resolve()`, matching the runtime formatter on Windows and POSIX hosts. Also: exec persist-on-exit, fork metadata, sandbox, shell/streaming polish.)
>
> **Phase 12 completed (2026-03-25):** shell UX now includes configurable transient thinking indicators during runtime drains (`shell.thinking_indicator_enabled`, `shell.thinking_indicator_style`, `shell.thinking_animation_interval_ms`), theme tokens (`shell.theme`), startup banner control (`shell.startup_banner`), notification dedupe (`shell.notification_dedupe`), richer approval previews (file-change summaries + unified diff/code-fence previews), and markdown/code rendering upgrades (fenced code syntax highlighting in stream output plus markdown-aware message/preview rendering). `agent_task_state` timing metadata (`active`, `started_at`, `ended_at`, `updated_at`) is persisted and surfaced through `/status` and the prompt toolbar.
>
> **Phase 13 foundation slice landed (2026-03-25):** session persistence now uses a pluggable store abstraction (`SessionStore`) with default JSON behavior and an opt-in SQLite backend (`features.sqlite_session_store`). When SQLite is enabled, sessions persist in `~/.localagent/sessions.db`; legacy JSON sessions are still loadable and are best-effort auto-migrated into SQLite on first load. Session command surfaces and `exec --session/--fork` flows keep the same operator-facing behavior.
>
> **Phase 13 slice 2 landed (2026-03-25):** `/session load` now performs best-effort runtime JSONL reconciliation from `~/.localagent/cache/runtime-events/<session-id>.jsonl`, recovering missing completed turn pairs (`user` + `assistant`) when present, skipping duplicate pairs, tolerating malformed JSONL lines, and recording replay metadata under `session.metadata.runtime_replay`.
>
> **Phase 13 slice 3 landed (2026-03-26):** SQLite session persistence now uses an explicit migration runner with ordered SQL migrations (`localagentcli/session/migrations/*.sql`) tracked in `schema_migrations`. Legacy `schema_meta` version-1 databases are recognized and backfilled so follow-on migrations apply safely. Replay checkpoint fields are persisted in SQLite columns and kept aligned with `session.metadata.runtime_replay`.
>
> **Phase 13 slice 4 landed (2026-03-26):** SQLite persistence now includes workspace-scoped long-horizon memory (`session_memories`). Memory candidates are extracted from compaction summaries and tagged assistant outputs, loaded memories are merged into `session.metadata.long_horizon_memory`, and prompt construction appends a compact `Long-horizon memory:` block when present.
>
> **Phase 13 slice 5 landed (2026-03-26):** session autosave now supports unnamed interactive sessions behind config (`sessions.autosave_unnamed`), persisting to generated IDs (`<autosave_unnamed_prefix><session-id>`) without renaming the live session; retention pruning now removes aged unnamed autosaves and stale runtime-event logs (`sessions.autosave_unnamed_retention_days`).
>
> **Phase 13 slice 6 landed (2026-03-26):** behavior-level persistence parity tests now cover compact-prefix preservation, replay-resume history superset behavior, and fork-divergence isolation (`tests/test_persistence_compact_resume_fork.py`).
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
| `[x]` | Shell UI (input loop, prompt) | 2026-03-25 — startup renders a configurable context banner (`shell.startup_banner`) with mode/target/workspace/session summary, runtime drains show transient thinking animation frames, planned-agent completion summaries route through markdown-aware rendering, and keyboard-first command/selection menus now keep no menu background fill, render unselected options in black text, selected options in turquoise (`#40E0D0`), while the prompt toolbar stays transparent and text-only |
| `[x]` | Command Router (parsing, dispatch) | 2026-03-17 |
| `[x]` | `/help` command | 2026-03-19 — grouped help, command-specific help, and slash-menu metadata are all driven by per-command `CommandSpec` declarations, and router-level unknown/invalid command errors now include consistent `/help` guidance plus close-match suggestions when available |
| `[x]` | `/exit` command | 2026-03-17 |
| `[x]` | `/status` command | 2026-03-25 — `/status` and the prompt toolbar still share one snapshot contract and now also surface task activity timing from `agent_task_state` (`active`, `started_at`, `updated_at`, computed elapsed) alongside route/phase/step/pending-tool/retry/error and undo-ready counts |
| `[x]` | `/config` command | 2026-03-19 — `/config` now opens an interactive schema-aware editor in TTY mode while keeping explicit dotted-key reads/writes for scripted use, and free-form edits now use the shared text-prompt helper |
| `[x]` | `/setup` wizard | 2026-03-19 — simplified for Phase 1 (workspace, mode, logging level), now uses the shared prompt contract for wizard questions, and still falls back to persisted defaults in non-interactive launches |
| `[x]` | Config system (TOML read/write) | 2026-03-17 |
| `[x]` | Config defaults and validation | 2026-03-26 — safety config now includes OS-wrapper backend validation (`off`/`auto`/`macos-seatbelt`/`linux-bwrap`/`container-docker`), typed sandbox policy overrides (`safety.sandbox_network_access`, `safety.sandbox_writable_roots`), and optional container backend image/resource settings |
| `[x]` | Session state dataclass | 2026-03-17 |
| `[x]` | Session manager (new/save/load/list/clear) | 2026-03-25 — now delegates persistence through a pluggable `SessionStore` abstraction (JSON default, opt-in SQLite via `features.sqlite_session_store`), while preserving existing command/API behavior; includes legacy JSON compatibility with best-effort auto-migration into SQLite on first load when enabled; prior default-target repair, exec resume/fork, fork lineage metadata, persist-on-exit, and named autosave behaviors remain intact |
| `[x]` | Storage manager (directory init) | 2026-03-17 |
| `[x]` | Logger (file-based, leveled) | 2026-03-17 |

---

## Phase 2 — Remote Models

| Status | Component | Notes |
|---|---|---|
| `[x]` | Provider base class (ABC) | 2026-03-18 |
| `[x]` | Provider registry | 2026-03-18 |
| `[x]` | OpenAI-compatible provider | 2026-03-22 — model list now comes from the provider `GET /models` response with default-model fallback, streamed tool-call deltas are accumulated, capability checks are resolved per selected model id, discovered models now carry inferred-vs-fallback readiness provenance, and reasoning-capable models expose supported reasoning levels while request payloads support validated `reasoning_effort` pass-through |
| `[x]` | Anthropic provider | 2026-03-22 — model list and connection test now use the live `GET /v1/models` API with default-model fallback, mixed text/thinking/tool blocks are preserved in order for non-streaming and streaming paths, discovered models now carry inferred-vs-fallback readiness provenance, and optional prompt-cache system blocks are supported via provider options |
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
| `[x]` | Model installer (HF download) | 2026-03-22 — Hugging Face Hub download with live per-file progress when dry-run planning is available, width-safe labels for narrow terminals, JSONL telemetry sidecar entries (`cache/downloads/install_telemetry.jsonl`), and richer `v2` post-install summaries (`completion_path`, total/downloaded/cached bytes, file downloaded-vs-cached counts, cache-hit ratio when known) |
| `[x]` | Model installer (URL download) | 2026-03-22 — httpx streaming with resume support, width-safe progress labels, failure-and-success telemetry records in `install_telemetry.jsonl`, and richer `v2` summary path signals for `url_fresh`/`url_resumed`/`url_already_complete`/`url_failed` |
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
| `[x]` | Streaming output renderer | 2026-03-25 — renderer now supports theme tokens, transient thinking indicator lifecycle (start/render/clear), structured notification routing with optional adjacent dedupe, markdown-aware preview/message rendering, and syntax-highlighted fenced code blocks in streamed output, while preserving status batching, persistent details lane behavior, width-safe truncation, and normalized agent activity/status surfaces |
| `[x]` | Reasoning panel display | 2026-03-19 — chat, direct-answer, and planned-agent reasoning now all use the same dimmed `Details` lane rather than mixing separate reasoning presentations |
| `[x]` | Context compactor (auto-summarization) | 2026-03-22 — `localagentcli/session/compactor.py` + `session/tokens.py`: UTF-8 byte ceiling token estimate, optional generation headroom before threshold, shared `build_conversation_model_messages` in `session/instructions.py`, transcript middle-drop markers for large histories, and structured tool-message formatting to preserve tool schema signal during summary generation |
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
| `[x]` | Tool registry | 2026-03-26 — `ToolRouter` merges built-in, dynamic, and MCP tools; `tools/schema.py` validates `parameters_schema` on `Tool.definition()` and dynamic registration; MCP client honors `timeout`, merges env with parent process, avoids qualified-name collisions, and now receives full runtime execution-policy updates (backend + typed policy + container options) for stdio launches; per-turn tool definitions are model-adapted using active `ModelInfo` capability and token-budget gates |
| `[x]` | `file_read` tool | 2026-03-18 |
| `[x]` | `file_search` tool | 2026-03-18 |
| `[x]` | `directory_list` tool | 2026-03-18 |
| `[x]` | `file_write` tool | 2026-03-18 |
| `[x]` | `patch_apply` tool | 2026-03-23 — supports diff-style patch operations with optional `@@` anchors, context-aware matching, and indentation-tolerant replacement while keeping legacy exact single-match `old_text/new_text` mode |
| `[x]` | `shell_execute` tool | 2026-03-26 — execution routes through `ExecProcess` (`LocalExecProcess` default + `RemoteExecProcess` seam) and supports optional wrappers (`macos-seatbelt`, `linux-bwrap`, `container-docker`) with typed policy overrides for network/writable roots and optional container image/cpu/memory settings; `auto` backend still falls back safely |
| `[x]` | `test_execute` tool | 2026-03-18 |
| `[x]` | `git_status` tool | 2026-03-18 |
| `[x]` | `git_diff` tool | 2026-03-18 |
| `[x]` | `git_commit` tool | 2026-03-18 |
| `[x]` | Agent controller | 2026-03-19 — session-integrated task orchestration now includes triage-based direct-answer fast path, single-step synthesis, controller reuse, remote capability validation by selected model id, interruption-aware cancellation, persisted `agent_task_state` snapshots, and explicit stopped vs timed-out vs failed outcomes |
| `[x]` | Agent loop (understand/plan/execute/observe) | 2026-03-23 — step prompts may append **Agent task status (runtime):** from `session.metadata["agent_task_state"]` (`task_context.py`, `AgentLoop.run(..., session=...)`); eligible multi-call read-only batches run concurrently with `min(batch_size, 16)` thread-pool workers (`agents/loop.py`); default step generation options now use the shared model-aware profile builder instead of hardcoded loop token caps; transcript/system context is merged into one primary step-system message with session-based instructions/environment fallback when missing; step prompt framing now uses structured execution rules/output contract/task-plan-step sections; initial execution no longer uses a separate model planning round-trip; tool-observation payload truncation is model-aware/adaptive with explicit truncation metadata instead of a fixed 4000-char slice; unified-turn execution now supports multi-round model↔tool iteration per step with a configurable round budget and model-adapted per-round tool schemas |
| `[x]` | Task planner | 2026-03-19 — model-driven JSON plans with heuristic fallback and replan support, now generating only the minimum number of steps needed instead of a fixed 2-6 step shape |
| `[x]` | Agent events system | 2026-03-19 — structured route, phase, plan, step, reasoning, tool, completion, stopped, timeout, and failure events rendered by the shell, with approval-risk and rollback-preview metadata flowing into the renderer |
| `[x]` | `/agent approve` command | 2026-03-26 — resumes pending tool actions and persists autonomous approvals across future tasks; high-risk actions still require explicit approval in normal postures, with the Phase 15 slice-1 exception for high-risk `shell_execute` in `danger-full-access` |
| `[x]` | `/agent deny` command | 2026-03-19 — rejects the pending tool action and returns the loop to recovery/replanning as needed |
| `[x]` | `/agent undo` command | 2026-03-19 — reverts the most recent rollback entry recorded for the current session and refuses to run while an agent task is active |
| `[x]` | `/agent undo-all` command | 2026-03-19 — reverts all rollback entries recorded for the current session in reverse order and refuses to run while an agent task is active |
| `[x]` | Ctrl+C agent stop path | 2026-03-19 — stops the active agent task from the shell, cancels active generation when supported, records a warning-style stop state instead of a generic failure, and exits the idle shell after a consecutive double press without prompting to save |

---

## Phase 6 — Safety

| Status | Component | Notes |
|---|---|---|
| `[x]` | Safety layer (central gate) | 2026-03-26 — `localagentcli/safety/layer.py` uses typed runtime sandbox policy (`RuntimeSandboxPolicy`) derived from `SandboxPosture`, validates boundaries, classifies risk, explains high-risk flags, describes rollback availability, applies approval policy, and records rollback history; `read-only` blocks side-effecting tools even in autonomous mode; autonomous + `danger-full-access` bypasses approval pauses for high-risk `shell_execute` |
| `[x]` | Approval manager (balanced mode) | 2026-03-18 — central safety gate now enforces prompts for standard side-effecting actions and read-only high-risk actions |
| `[x]` | Approval manager (autonomous mode) | 2026-03-26 — autonomous mode auto-approves standard actions, persists across future tasks, and now applies sandbox-aware high-risk behavior (high-risk `shell_execute` in `danger-full-access` skips interactive approval; other high-risk actions still pause) |
| `[x]` | Approval UX (inline prompts) | 2026-03-25 — inline prompts flush pending renderer detail before input, keep shared keyboard action flow (`approve`/`deny`/`details`/`approve all`), and now render richer tool previews including patch file-change summaries, unified diff fences (syntax highlighted by preview renderer), file-write language-fenced content previews, and explicit truncation labels |
| `[x]` | Workspace boundary enforcement | 2026-03-18 — dedicated `WorkspaceBoundary` enforces root confinement for tool paths and shell working directories |
| `[x]` | Symlink validation | 2026-03-18 — symlinks resolving outside the workspace root are blocked centrally and in shared path resolution helpers |
| `[x]` | High-risk action detection | 2026-03-26 — shell commands (including extended patterns for permissive `chmod`, destructive `docker`/`kubectl` verbs) and sensitive file paths are classified centrally, with sandbox-aware approval handling for autonomous `danger-full-access` shell execution |
| `[x]` | Rollback manager (file backups) | 2026-03-18 — `RollbackManager` stores per-session backups and a JSON rollback log under `cache/rollback/` |
| `[x]` | Undo capability | 2026-03-19 — rollback history supports `undo_last()` and `undo_all()` restoration for modified and newly created files, with Windows-safe modified-file restore behavior plus explicit `/agent undo` and `/agent undo-all` command surfaces |

---

## Phase 7 — Packaging

| Status | Component | Notes |
|---|---|---|
| `[x]` | `pyproject.toml` configuration | 2026-03-18 — production metadata, project URLs, license files, classifiers, and release tooling extras added |
| `[x]` | Backend auto-install on demand | 2026-03-18 — shell prompts to install missing MLX/GGUF/Torch dependencies and installs direct backend requirements before retrying model load |
| `[x]` | Unit tests | 2026-03-26 — full suite now includes backend/wrapper coverage for seatbelt/bwrap/container, typed policy override parsing tests (network + writable roots), runtime fallback/strict-backend behavior checks, MCP stdio launch-policy propagation tests (including container mode), MCP env merge, approval/sandbox integration for MCP tools, and cross-platform environment-context cwd assertions; run `pytest --cov` for current counts and coverage |
| `[x]` | Integration tests | 2026-03-18 — setup/save/load and backend auto-install flows covered in `tests/integration/test_packaging_flows.py` |
| `[x]` | CLI tests | 2026-03-18 — subprocess coverage for interactive and non-interactive first-run setup, session restore, single- and double-`Ctrl+C` handling in `tests/cli/test_packaging_cli.py`, with a Windows-safe non-interactive interrupt path |
| `[x]` | Agent workflow tests | 2026-03-18 — planner, controller, shell integration, provider tool-calling, and `/agent` command coverage added |
| `[x]` | Safety tests | 2026-03-26 — boundary, rollback, safety-layer, high-risk approval, read-only sandbox blocks, `parse_sandbox_mode`, extended shell pattern coverage, and autonomous `danger-full-access` high-risk shell approval scenarios |
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
| `[x]` | `docs/tool-system.md` | 2026-03-26 — now documents feature-gated turn-boundary MCP tool inventory refresh (`features.mcp_tool_inventory_refresh`) in addition to schema and parallel read-only batch rules |
| `[x]` | `docs/mcp.md` | 2026-03-26 — transport config covers `stdio`/`http`/`sse`, auth header options, and stdio subprocess execution policy propagation including `container-docker` + `auto` fallback/explicit-backend failure behavior |
| `[x]` | `docs/safety-and-permissions.md` | 2026-03-26 — MCP `readOnlyHint` approval table rows, autonomous mode wording, explicit sandbox-aware high-risk approval exception (`danger-full-access` + high-risk `shell_execute`), typed policy override controls, and clarified wrapper isolation limits |
| `[x]` | `docs/session-and-config.md` | 2026-03-26 — safety config examples now include backend selection (`container-docker` included), typed policy override fields, and container backend settings |
| `[x]` | `docs/cli-and-ux.md` | 2026-03-20 — primary vs secondary output rendering, dimmed `Details` panel, prompt-time status toolbar, agent route/phase/undo status surfaces, shared prompt helpers, renderer-backed command-result presentation, and truncated approval preview behavior documented |
| `[x]` | `docs/storage-and-logging.md` | Complete |
| `[x]` | `docs/packaging-and-release.md` | 2026-03-18 — release checklist, trusted-publishing prerequisites, `pipx` smoke path guidance, non-interactive first-run setup expectations, and local wheel refresh command documented |
| `[x]` | `docs/roadmap.md` | Complete |
| `[x]` | `docs/current-state.md` | Complete |
| `[x]` | `README.md` | 2026-03-18 — install, usage, backend, development, and release validation instructions refreshed |
| `[x]` | `CHANGELOG.md` | 2026-03-18 — release notes expanded for hardening and packaging work |
| `[x]` | `CLAUDE.md` | Complete — includes testing/CI requirements |
| `[x]` | `AGENTS.md` | Complete — includes testing/CI requirements |
