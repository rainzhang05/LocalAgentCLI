# Changelog

## 0.2.0 - 2026-03-25

### Added

- Added async shell and headless `exec` turn execution on a shared submission/event runtime, including append-only runtime event logs, resume/fork flows, and named session autosave.
- Added MCP stdio integration with dynamic tool discovery/registration, per-server timeout and environment support, and read-only hint aware approval behavior.
- Added feature staging configuration, model metadata (`ModelInfo`) plumbing, provider prompt-caching options, and configurable reasoning-effort controls.

### Changed

- Upgraded agent execution with bootstrap-first planning, unified multi-round in-step tool/model turns, structured step briefs, model-aware generation profiles, preserved system context, and adaptive tool-observation truncation.
- Enhanced tool runtime with robust anchored/context-aware `patch_apply` behavior and PTY-backed incremental `shell_execute` streaming on POSIX with cross-platform fallback.
- Improved model/provider orchestration with readiness posture + provenance signaling, async provider transport hardening (timeouts/connection policy), and normalized stream error/finalization behavior.
- Refined shell UX with richer status and retry visibility, persistent details lane controls, narrow-terminal resilience, markdown + syntax-highlighted rendering, startup banner/theme controls, and richer approval previews.

### Fixed

- Fixed reliability issues across command dispatch guidance, runtime finalization paths, duplicate direct-answer summaries, stream final-text derivation, Windows-safe import/path handling, and session/mode runtime reset behavior.

## 0.1.0 - 2026-03-18

### Added

- Added backend dependency auto-install prompts for local model backends.
- Added packaging-oriented integration and CLI subprocess tests.
- Added TestPyPI/PyPI-ready publish automation and pipx smoke validation.

### Changed

- Expanded CI coverage to include Windows and package build verification.
- Completed packaging metadata and user-facing installation/usage documentation.
