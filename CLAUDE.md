# CLAUDE.md — Development Rules for LocalAgentCLI

This file defines mandatory rules for AI agents (Claude, Codex, Copilot, or any other) working on this project. These rules also apply to human developers.

---

## Before Making Any Changes

1. **Read the docs first.** Before modifying any code, read the relevant documents in `/docs/`. The documentation is the source of truth for all architecture, behavior, and implementation decisions.

2. **Understand the architecture.** Start with [docs/architecture.md](docs/architecture.md) to understand the system layers and package structure. Then read the specific doc for the area you're working on.

3. **Check the roadmap.** Read [docs/roadmap.md](docs/roadmap.md) to understand phase dependencies. Do not implement Phase 5 components before Phase 1 is complete.

4. **Check current state.** Read [docs/current-state.md](docs/current-state.md) to know what has been implemented and what hasn't.

---

## While Making Changes

5. **Follow the documented architecture.** Use the package structure, class names, and interfaces defined in the docs. Do not invent alternative patterns.

6. **Keep everything consistent.** If you change a behavior that is documented, update the relevant doc. If a doc says X and the code says Y, the doc is the authority — fix the code.

7. **Stay within scope.** Only implement what is specified in the docs. Do not add features, abstractions, or "improvements" that are not documented.

8. **Follow the roadmap order.** Implement phases in order. Within a phase, implement dependencies before dependents (e.g., Tool ABC before individual tools, Command Router before individual commands).

---

## After Making Changes

9. **Update current state.** After completing any implementation work, update [docs/current-state.md](docs/current-state.md) to reflect what was built. Change status markers from `[ ]` to `[~]` (in progress) or `[x]` (done).

10. **Commit one file at a time.** Each modified or created file gets its own commit. If you modify 5 files, that's 5 commits. Commit on the current branch — do not create new branches.

11. **Use descriptive commit messages.** Follow the format: `<type>: <description>`. Types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`.

---

## Documentation Index

| Document | Covers |
|---|---|
| [docs/architecture.md](docs/architecture.md) | System layers, package structure, concurrency, cross-platform |
| [docs/commands.md](docs/commands.md) | All slash commands, routing rules, command registry |
| [docs/model-system.md](docs/model-system.md) | Model formats, registry, detection, backends, abstraction layer |
| [docs/remote-providers.md](docs/remote-providers.md) | API providers, key storage, streaming, provider types |
| [docs/agent-system.md](docs/agent-system.md) | Chat mode, agent mode, agent loop, mode switching |
| [docs/tool-system.md](docs/tool-system.md) | Tool definitions, output schema, registration, execution flow |
| [docs/safety-and-permissions.md](docs/safety-and-permissions.md) | Approval modes, workspace boundary, rollback, high-risk actions |
| [docs/session-and-config.md](docs/session-and-config.md) | TOML config, session state, context compaction |
| [docs/cli-and-ux.md](docs/cli-and-ux.md) | Terminal UI, streaming, input handling, error display |
| [docs/storage-and-logging.md](docs/storage-and-logging.md) | Directory structure, log levels, file locking |
| [docs/packaging-and-release.md](docs/packaging-and-release.md) | Installation, dependencies, testing, release process |
| [docs/roadmap.md](docs/roadmap.md) | Development phases and dependencies |
| [docs/current-state.md](docs/current-state.md) | Implementation status tracker |

---

## Commit Rules

- **One file per commit.** Never bundle multiple file changes in a single commit.
- **Commit on current branch.** Do not create new branches.
- **Commit locally.** Do not push unless explicitly asked.
- **No co-author trailers.** Do not add `Co-Authored-By` or any attribution lines. Commits are attributed to the user via their git config.
- **Commit format**: `<type>: <short description>`
  - `feat: add command router with dispatch logic`
  - `fix: handle missing config.toml on first run`
  - `docs: update current-state.md after Phase 1 work`
  - `test: add unit tests for workspace boundary`
  - `refactor: extract streaming logic from ShellUI`
  - `chore: update dependencies in pyproject.toml`

---

## Key Principles

- **The docs are the spec.** If it's not in the docs, don't build it.
- **No guessing.** If the docs are ambiguous, ask for clarification rather than making assumptions.
- **Consistency over cleverness.** Follow existing patterns even if you think of a "better" way.
- **Update docs/current-state.md.** This is non-negotiable. Every implementation session ends with an updated current-state.md.
