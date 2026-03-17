# LocalAgentCLI — Packaging and Release

This document covers installation, packaging, dependency management, entry points, testing requirements, and the definition of done.

---

## Installation

### Primary Method

```bash
pipx install localagentcli
```

- `pipx` is the recommended installation method because it creates an isolated virtual environment for the application while making the `localagent` command globally available.
- The package name on PyPI is `localagentcli`.

### Alternative Methods

```bash
# Standard pip install (not recommended for CLI tools)
pip install localagentcli

# Development install from source
git clone <repo-url>
cd LocalAgentCLI
pip install -e ".[dev]"
```

---

## Entry Point

```bash
localagent
```

The single command launches the interactive shell. No subcommands are needed at the CLI level — all functionality is accessed through slash commands inside the shell.

### Entry Point Configuration

```toml
# pyproject.toml
[project.scripts]
localagent = "localagentcli.__main__:main"
```

```python
# localagentcli/__main__.py

def main():
    """Entry point for the localagent CLI."""
    from localagentcli.shell.ui import ShellUI
    from localagentcli.config.manager import ConfigManager
    from localagentcli.storage.manager import StorageManager

    storage = StorageManager()
    storage.initialize()

    config = ConfigManager(storage.config_path)
    config.load()

    shell = ShellUI(config=config, storage=storage)
    shell.run()

if __name__ == "__main__":
    main()
```

---

## Package Structure

```toml
# pyproject.toml

[build-system]
requires = ["setuptools>=68.0", "wheel"]
build-backend = "setuptools.backends._legacy:_Backend"

[project]
name = "localagentcli"
version = "0.1.0"
description = "A production-grade, local-first AI CLI"
requires-python = ">=3.11"
license = {text = "MIT"}

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
    "localagentcli[mlx,gguf,torch]",
]
dev = [
    "pytest>=7.0",
    "pytest-asyncio>=0.21",
    "pytest-cov>=4.0",
    "ruff>=0.1",
    "mypy>=1.5",
]

[project.scripts]
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
2. Display: `"The MLX backend requires the 'mlx' package. Install it now? [Y/n]"`
3. On confirmation, run `pip install localagentcli[mlx]` (or equivalent)
4. Verify the installation succeeded
5. Proceed with model loading

```python
# localagentcli/models/backends/base.py

def check_backend_dependencies(backend: str) -> tuple[bool, list[str]]:
    """Check if required packages for a backend are installed.
    Returns (all_installed, missing_packages).
    """
    requirements = {
        "mlx": ["mlx", "mlx_lm"],
        "gguf": ["llama_cpp"],
        "safetensors": ["torch", "transformers", "safetensors"],
    }
    missing = []
    for pkg in requirements.get(backend, []):
        try:
            importlib.import_module(pkg)
        except ImportError:
            missing.append(pkg)
    return len(missing) == 0, missing
```

---

## Testing Requirements

### Test Categories

| Category | What It Tests | Tools |
|---|---|---|
| Unit tests | Individual classes and functions | `pytest` |
| Integration tests | Component interactions (e.g., command router + session manager) | `pytest` |
| CLI tests | End-to-end CLI behavior (input → output) | `pytest` + subprocess |
| Agent workflow tests | Full agent loop (task → plan → tools → result) | `pytest` with mock models |
| Safety tests | Approval enforcement, boundary checks, rollback | `pytest` |

### Critical Test Flows

These flows must be tested end-to-end and must pass before any release:

1. **Install → Launch → Setup**
   - `pipx install localagentcli` completes without errors
   - `localagent` launches the interactive shell
   - First-run `/setup` wizard completes successfully
   - Config file is created with valid defaults

2. **Model Install/Use**
   - `/models install hf <repo>` downloads and registers a model
   - `/models list` shows the installed model
   - `/models use <name>` loads the model
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
   - Ctrl+C during agent execution pauses the agent
   - Partial output is displayed and not corrupted
   - The shell returns to the input prompt

5. **Session Restore**
   - `/session save test` saves successfully
   - `/session list` shows the saved session
   - `/exit` and relaunch
   - `/session load test` restores full state
   - Conversation history is intact

### Test Organization

```
tests/
├── unit/
│   ├── test_command_router.py
│   ├── test_config_manager.py
│   ├── test_model_registry.py
│   ├── test_safety_layer.py
│   ├── test_workspace_boundary.py
│   ├── test_rollback_manager.py
│   ├── test_context_compactor.py
│   └── test_logger.py
├── integration/
│   ├── test_session_lifecycle.py
│   ├── test_model_install_flow.py
│   ├── test_provider_connection.py
│   └── test_agent_tool_execution.py
├── cli/
│   ├── test_shell_launch.py
│   ├── test_command_execution.py
│   └── test_interrupt_handling.py
├── agent/
│   ├── test_agent_loop.py
│   ├── test_plan_generation.py
│   └── test_tool_batching.py
├── safety/
│   ├── test_approval_modes.py
│   ├── test_boundary_enforcement.py
│   ├── test_high_risk_detection.py
│   └── test_rollback.py
└── conftest.py                  # Shared fixtures
```

---

## Definition of Done

A release is complete when a user can:

1. **Install via pipx**: `pipx install localagentcli` succeeds on macOS, Linux, and Windows
2. **Launch CLI**: `localagent` opens the interactive shell
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

1. All tests pass (`pytest --cov`)
2. Type checking passes (`mypy localagentcli/`)
3. Linting passes (`ruff check localagentcli/`)
4. Version bumped in `pyproject.toml`
5. Changelog updated
6. Tagged in git (`git tag v0.1.0`)
7. Built (`python -m build`)
8. Published to PyPI (`twine upload dist/*`)
9. Verified with `pipx install localagentcli` on a clean system
