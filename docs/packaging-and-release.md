# LocalAgentCLI — Packaging and Release

This document covers installation, packaging, dependency management, entry points, testing requirements, and the definition of done.

---

## Installation

### Primary Method

```bash
pipx install localagentcli
```

- `pipx` is the recommended installation method because it creates an isolated virtual environment for the application while making the `localagentcli` command globally available.
- The package name on PyPI is `localagentcli`.

### Alternative Methods

```bash
# Standard pip install (not recommended for CLI tools)
pip install localagentcli

# Development install from source
git clone https://github.com/rainzhang05/LocalAgentCLI.git
cd LocalAgentCLI
pip install -e ".[dev]"
```

### Refreshing a Local `pipx` Install During Development

If you already have `localagentcli` installed with `pipx` and want to refresh it to the newest local project state, rebuild the wheel and force-reinstall it:

```bash
python -m build
pipx install --force dist/*.whl
```

This keeps the install aligned with the latest local wheel without waiting for a PyPI publish.

---

## Entry Point

```bash
localagentcli
```

The primary command launches the interactive shell. The package also exposes a small non-interactive surface for one-shot requests:

```bash
localagentcli exec "Explain the latest test failures."
```

That headless surface also supports:

```bash
localagentcli exec --mode agent --json "Review the latest changes"
localagentcli exec --session saved-work "Continue the task"
localagentcli exec --fork saved-work "Try an alternate approach"
```

The package also exposes `localagent` as a compatibility alias.

### Entry Point Configuration

```toml
# pyproject.toml
[project.scripts]
localagentcli = "localagentcli.__main__:main"
localagent = "localagentcli.__main__:main"
```

```python
# localagentcli/__main__.py

def main(argv=None):
    """Launch the interactive shell or a one-shot non-interactive turn."""
    args = _parse_args([] if argv is None else list(argv))
    storage, config, first_run = _bootstrap_application()

    if args.command == "exec":
        return _run_exec(" ".join(args.prompt).strip(), config, storage)

    shell = ShellUI(config=config, storage=storage, first_run=first_run)
    shell.run()
    return 0

if __name__ == "__main__":
    main()
```

---

## Package Structure

```toml
# pyproject.toml

[build-system]
requires = ["setuptools>=68.0", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "localagentcli"
version = "0.2.0"
description = "A production-grade, local-first AI CLI"
readme = "README.md"
requires-python = ">=3.11"
license = "MIT"
license-files = ["LICENSE"]
authors = [{name = "rainzhang05"}]
keywords = ["ai", "agent", "cli", "llm", "local models"]
classifiers = [
    "Development Status :: 4 - Beta",
    "Environment :: Console",
    "Intended Audience :: Developers",
    "Operating System :: MacOS",
    "Operating System :: Microsoft :: Windows",
    "Operating System :: POSIX :: Linux",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3 :: Only",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: 3.13",
    "Topic :: Scientific/Engineering :: Artificial Intelligence",
    "Typing :: Typed",
]

dependencies = [
    "prompt-toolkit>=3.0",
    "rich>=13.0",
    "click>=8.0",
    "toml>=0.10",
    "keyring>=24.0",
    "huggingface-hub>=0.20",
    "httpx>=0.25",
    "filelock>=3.12",
]

[project.optional-dependencies]
mlx = [
    "mlx>=0.5",
    "mlx-lm>=0.5",
]
gguf = [
    "llama-cpp-python>=0.2",
]
torch = [
    "torch>=2.0",
    "transformers>=4.35",
    "safetensors>=0.4",
]
all = [
    "mlx>=0.5",
    "mlx-lm>=0.5",
    "llama-cpp-python>=0.2",
    "torch>=2.0",
    "transformers>=4.35",
    "safetensors>=0.4",
]
dev = [
    "build>=1.2",
    "pytest>=7.0",
    "pytest-asyncio>=0.21",
    "pytest-cov>=4.0",
    "ruff>=0.1",
    "mypy>=1.5",
    "twine>=5.1",
    "types-toml>=0.10",
]

[project.urls]
Homepage = "https://github.com/rainzhang05/LocalAgentCLI"
Repository = "https://github.com/rainzhang05/LocalAgentCLI"
Issues = "https://github.com/rainzhang05/LocalAgentCLI/issues"
Changelog = "https://github.com/rainzhang05/LocalAgentCLI/blob/main/CHANGELOG.md"

[project.scripts]
localagentcli = "localagentcli.__main__:main"
localagent = "localagentcli.__main__:main"
```

---

## Dependency Management

### Core Dependencies (Always Installed)

| Package | Purpose |
|---|---|
| `prompt-toolkit` | Terminal input, history, tab completion |
| `rich` | Rich text rendering, panels, syntax highlighting |
| `click` | CLI entry point parsing |
| `toml` | TOML config file parsing |
| `keyring` | OS keychain access for API keys |
| `huggingface-hub` | Model downloads from Hugging Face |
| `httpx` | HTTP client for remote providers |
| `filelock` | Cross-platform file locking |

### Optional Backend Dependencies

Backend dependencies are installed on demand when the user first needs them:

1. **MLX** (`mlx`, `mlx-lm`): Installed when user loads an MLX model on macOS
2. **GGUF** (`llama-cpp-python`): Installed when user loads a GGUF model
3. **PyTorch** (`torch`, `transformers`, `safetensors`): Installed when user loads a safetensors model

### Auto-Install Behavior

When a user attempts to use a backend whose dependencies are not installed:

1. Detect the missing dependency
2. Display an inline confirmation prompt from the shell UI
3. On confirmation, run `python -m pip install <backend requirements>` for the selected backend
4. Verify the installation succeeded
5. Proceed with model loading

The implementation keeps two maps:

- import names used to detect missing modules, such as `mlx_lm` or `llama_cpp`
- pinned requirement specifiers used for installation, such as `mlx-lm>=0.5` or `llama-cpp-python>=0.2`

The shell owns the confirmation and retry loop so backend modules remain focused on loading and generation.

---

## Testing Requirements

### Test Categories

| Category | What It Tests | Tools |
|---|---|---|
| Unit tests | Individual classes and functions | `pytest` |
| Integration tests | Component interactions (e.g., command router + session manager) | `pytest` |
| CLI tests | End-to-end CLI behavior (input → output) | `pytest` + subprocess |
| End-to-end (E2E) tests | Headless `exec` plus session save/reload across processes (deterministic model stubs) | `pytest` + `pytest-asyncio` + subprocess (`tests/e2e/`) |
| Agent workflow tests | Full agent loop (task → plan → tools → result) | `pytest` with mock models |
| Safety tests | Approval enforcement, boundary checks, rollback | `pytest` |
| Performance baselines (opt-in) | Deterministic hot paths (e.g. message assembly), local timing only | `pytest` with `RUN_PERF=1` (`tests/perf/`) |

### Opt-in performance baselines

A small **opt-in** suite under `tests/perf/` exercises hot paths without network or I/O variance. Tests are skipped unless `RUN_PERF=1` is set, so default CI and the publish **release gate** stay unchanged.

From the repository root:

```bash
cd LocalAgentCLI
RUN_PERF=1 python -m pytest tests/perf/ -v -m perf
```

Results are for **local regression awareness** only: there is a loose sanity ceiling on total wall time to catch pathological regressions, not micro-benchmark thresholds.

### Critical Test Flows

These flows must be tested end-to-end and must pass before any release:

1. **Install → Launch → Setup**
   - `pipx install localagentcli` completes without errors
   - `localagentcli` launches the interactive shell
   - `localagentcli exec "hello"` runs a one-shot request through the shared runtime without entering the prompt loop
   - `localagentcli exec --json "hello"` emits one runtime event per line on stdout
   - First-run `/setup` wizard completes successfully in interactive terminals
   - First-run `/setup` falls back to current/default values without prompting when stdin is non-interactive, so packaged smoke tests and piped launches do not fail with `EOFError`
   - Config file is created with valid defaults

2. **Model Install/Use**
   - `/models install hf <repo>` downloads and registers a model
   - `/models list` shows the installed model
   - `/set` can switch to an installed local model and load it
   - `/models inspect <name>` shows correct metadata
   - `/models remove <name>` cleans up files and registry

3. **Agent Execution**
   - Enter agent mode with a tool-capable model
   - Submit a task (e.g., "create a hello world script")
   - Agent generates a plan
   - Agent executes tools (file_write, etc.)
   - Approval prompts appear for write operations
   - Task completes with correct result

4. **Interruption Handling**
   - Ctrl+C during model generation stops generation cleanly
   - Ctrl+C during agent execution stops the current task
   - A second consecutive Ctrl+C from the idle prompt exits the shell
   - Partial output is displayed and not corrupted
   - The shell returns to the input prompt

5. **Session Restore**
   - `/session save test` saves successfully
   - `/session list` shows the saved session
   - `/exit` and relaunch
   - `/session load test` restores full state
   - Conversation history is intact

The CI smoke path should execute the installed app from `python -m pipx environment --value PIPX_BIN_DIR` rather than hard-coding a `pipx` venv location, because the internal venv path can differ across `pipx` versions and environments.

The CI/package smoke path should also exercise a non-interactive first launch, such as piping `/exit` into `localagentcli`, because packaged entrypoint validation often runs without a TTY. The first-run setup flow must treat that case as valid and persist defaults instead of prompting.

### Test Organization

The repository keeps broad unit and component coverage in the top-level `tests/` directory and adds dedicated phase-level suites under:

```text
tests/
├── cli/
│   └── test_packaging_cli.py
├── e2e/
├── perf/
│   └── test_perf_baseline.py
├── integration/
│   └── test_packaging_flows.py
├── test_*.py
└── conftest.py
```

---

## Definition of Done

A release is complete when a user can:

1. **Install via pipx**: `pipx install localagentcli` succeeds on macOS, Linux, and Windows
2. **Launch CLI**: `localagentcli` opens the interactive shell
3. **Configure model/provider**: Set up a local model or remote provider through the setup wizard or manual commands
4. **Chat with streaming**: Have a streaming conversation in chat mode
5. **Run agent tasks**: Submit a task in agent mode and have the agent execute it with tools
6. **Observe all actions**: See every tool call, approval decision, and intermediate result in the activity log
7. **Approve/deny actions**: Receive approval prompts for write operations and be able to approve or deny them
8. **Save/load session**: Save a session, exit, relaunch, and load the session with full state restored
9. **Recover from errors**: Encounter an error (bad model, network failure, tool failure) and continue using the CLI without restarting

All 9 criteria must be verified by automated tests and manual testing before a version is released.

---

## Release Process

### Local Release Checklist

1. Install development dependencies: `pip install -e ".[dev]"`
2. Run tests with coverage: `python -m pytest --cov=localagentcli --cov-fail-under=80`
3. Run lint and formatting checks: `ruff check .` and `ruff format --check .`
4. Run static typing: `mypy localagentcli/`
5. When bumping `[project].version` in `pyproject.toml`, add a matching `## <version>` section to `CHANGELOG.md` (the test suite asserts this alignment via `tests/test_packaging_metadata.py`).
6. Build artifacts: `python -m build`
7. Validate metadata: `python -m twine check dist/*`
8. Smoke test the built wheel locally:

```bash
pipx install --force dist/localagentcli-0.2.0-py3-none-any.whl
localagentcli
pipx uninstall localagentcli
```

### CI / Automation

The repository uses:

- `.github/workflows/test.yml` for the cross-platform test matrix on macOS, Linux, and Windows plus package build and `pipx` smoke validation
- `.github/workflows/lint.yml` for Ruff validation
- `.github/workflows/typecheck.yml` for mypy
- `.github/workflows/publish.yml` for build, artifact validation, `pipx` smoke testing, and trusted publishing to TestPyPI or PyPI. The **Publish** workflow runs a **release gate** job first (Ruff check, Ruff format check, mypy on `localagentcli/`, and pytest with the same coverage floor as local development): the build and publish jobs only run after that gate passes.

### Future changelog automation (optional)

Today’s release bar is a hand-edited root `CHANGELOG.md` with a `## <version>` heading that matches `[project].version` in `pyproject.toml` (enforced by `tests/test_packaging_metadata.py::test_changelog_documents_pyproject_version`).

A future iteration may adopt richer automation—for example per-pull-request changelog fragments, Towncrier-style assembly, or generated release notes from merged PR labels. None of that is required for publishing; maintainers should not block releases on tooling that is not yet in the repository.

### PyPI Publishing Prerequisites

Before the first real release, complete these repository-side setup steps:

1. Create the `localagentcli` project on PyPI and optionally on TestPyPI
2. Configure Trusted Publishing on PyPI/TestPyPI for `rainzhang05/LocalAgentCLI`
3. Add matching GitHub environments named `testpypi` and `pypi`
4. Restrict release permissions and reviewers as desired in those environments
5. Confirm the package name, metadata, README rendering, and changelog content are correct

Trusted publishing is preferred over long-lived API tokens because GitHub Actions can obtain short-lived publish credentials directly from PyPI.

### Publish Flow

1. Bump the version in `pyproject.toml`
2. Update `CHANGELOG.md`
3. Commit the release changes
4. Tag the release: `git tag v0.2.0`
5. Push the branch and tag
6. Run the `Publish` workflow manually for TestPyPI, or let the tag trigger the PyPI publish path
7. Verify installation from the target index with `pipx install localagentcli`

If trusted publishing is not available yet, a maintainer can still perform a one-off manual upload with `twine upload dist/*`, but that path is a fallback rather than the preferred release mechanism.
