# LocalAgentCLI

LocalAgentCLI is a local-first AI command-line assistant with a persistent shell, chat and agent modes, remote and local model backends, tool execution, session restore, and a centralized safety layer.

## Features

- Interactive `localagent` shell with slash commands, history, tab completion, and streaming output
- Remote provider support for OpenAI-compatible, Anthropic, and generic REST APIs
- Local model support for MLX, GGUF, and safetensors backends
- Chat mode with context compaction and pinned instructions
- Agent mode with planning, tool execution, approvals, rollback, and undo support
- Persistent config, model registry, sessions, logs, and cache under `~/.localagent/`

## Installation

Recommended:

```bash
pipx install localagentcli
```

Development install:

```bash
git clone https://github.com/rainzhang05/LocalAgentCLI.git
cd LocalAgentCLI
pip install -e ".[dev]"
```

Optional backend extras can be installed manually:

```bash
pip install "localagentcli[mlx]"
pip install "localagentcli[gguf]"
pip install "localagentcli[torch]"
pip install "localagentcli[all]"
```

LocalAgentCLI also prompts to install missing backend dependencies automatically the first time you load a local model that needs them.

## Quick Start

Launch the shell:

```bash
localagent
```

On first launch the setup wizard creates `~/.localagent/config.toml`.

Common commands:

```text
/help
/status
/setup
/mode chat
/mode agent
/models list
/providers list
/session save my-work
/session load my-work
```

## Local and Remote Models

Remote providers:

- `/providers add`
- `/providers list`
- `/providers use <name>`
- `/providers test <name>`

Local models:

- `/models install hf <repo>`
- `/models install url <url>`
- `/models list`
- `/models use <name[@version]>`
- `/models inspect <name[@version]>`

## Development

Run the required checks locally:

```bash
python -m pytest --cov=localagentcli --cov-fail-under=80
ruff check .
ruff format --check .
mypy localagentcli/
python -m build
python -m twine check dist/*
```

## Documentation

Project documentation lives in the repository:

- [Architecture](https://github.com/rainzhang05/LocalAgentCLI/blob/main/docs/architecture.md)
- [Current State](https://github.com/rainzhang05/LocalAgentCLI/blob/main/docs/current-state.md)
- [Roadmap](https://github.com/rainzhang05/LocalAgentCLI/blob/main/docs/roadmap.md)
- [Packaging and Release](https://github.com/rainzhang05/LocalAgentCLI/blob/main/docs/packaging-and-release.md)
