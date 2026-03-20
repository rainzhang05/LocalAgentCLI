# AGENTS.md — Agent Guidelines for LocalAgentCLI

This file provides guidelines for any AI agent or automated system working on this repository. It is intentionally consistent with [CLAUDE.md](CLAUDE.md) — both files enforce the same rules.

---

## Mandatory Workflow

### 1. Read Before You Write

Before modifying any code in this repository, read the relevant documentation in `/docs/`. The documentation is the authoritative specification for all architecture, behavior, and implementation decisions.

**Start here:**
- [docs/architecture.md](docs/architecture.md) — system layers, package structure, data flow
- [docs/current-state.md](docs/current-state.md) — what's implemented, what's not
- [docs/roadmap.md](docs/roadmap.md) — phase order and dependencies

**Then read the specific doc for your area of work:**

| Area | Document |
|---|---|
| Shell and input handling | [docs/cli-and-ux.md](docs/cli-and-ux.md) |
| Slash commands | [docs/commands.md](docs/commands.md) |
| Local models and backends | [docs/model-system.md](docs/model-system.md) |
| Remote API providers | [docs/remote-providers.md](docs/remote-providers.md) |
| Chat and agent modes | [docs/agent-system.md](docs/agent-system.md) |
| Tools (file, shell, git) | [docs/tool-system.md](docs/tool-system.md) |
| Approvals, safety, rollback | [docs/safety-and-permissions.md](docs/safety-and-permissions.md) |
| Config and sessions | [docs/session-and-config.md](docs/session-and-config.md) |
| Filesystem and logs | [docs/storage-and-logging.md](docs/storage-and-logging.md) |
| Packaging and testing | [docs/packaging-and-release.md](docs/packaging-and-release.md) |

### 2. Follow the Architecture

- Use the package structure defined in [docs/architecture.md](docs/architecture.md)
- Use the class names and interfaces specified in the docs
- Do not invent alternative patterns or abstractions
- Each layer communicates only with its immediate neighbors

### 3. Follow the Roadmap

- Implement phases in order (Phase 1 before Phase 2, etc.)
- Within a phase, implement base classes before concrete implementations
- Check [docs/roadmap.md](docs/roadmap.md) for phase dependencies

### 4. Keep Docs Consistent

- If the docs say X and the code says Y, the docs are correct — fix the code
- If you change a behavior that is documented, update the relevant doc
- Keep repository-owned docs product-local. Do not mention external workspace phases, migration bookkeeping, or reference-repo provenance in `LocalAgentCLI` docs.
- Do not add undocumented features

### 5. Update Current State

After completing any implementation work, update [docs/current-state.md](docs/current-state.md):
- Change `[ ]` to `[~]` (in progress) or `[x]` (done)
- Add the date and any relevant notes
- This step is mandatory for every implementation session

### 6. Commit Rules

- **One file per commit.** If you modify 10 files, make 10 commits.
- **Commit on the current branch.** Do not create new branches.
- **Commit locally only.** Do not push unless explicitly instructed.
- **No co-author trailers.** Do not add `Co-Authored-By` or any attribution lines. Commits are attributed to the user via their git config.
- **Commit message format**: `<type>: <short description>`
  - Types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`
  - Example: `feat: add command router with dispatch logic`

### 7. Testing and CI

After making changes, ensure all CI checks pass locally before committing:

- **Tests with coverage**: `python -m pytest --cov=localagentcli --cov-fail-under=80`
- **Lint**: `ruff check .` and `ruff format --check .`
- **Type check**: `mypy localagentcli/`

Test coverage must remain at or above **80%**. If you add new code, add corresponding tests. If coverage drops below 80%, add tests to bring it back up before committing.

---

## What Not To Do

- Do not implement features not specified in the docs
- Do not skip phases in the roadmap
- Do not create new branches
- Do not push to remote without explicit instruction
- Do not modify code without reading the relevant docs first
- Do not bundle multiple file changes in a single commit
- Do not leave `docs/current-state.md` out of date after implementation work

---

## Cross-Reference

This file is intentionally consistent with [CLAUDE.md](CLAUDE.md). Both files enforce the same rules. If you find a discrepancy between them, treat it as a bug and reconcile them.
