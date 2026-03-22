# LocalAgentCLI — MCP (Model Context Protocol)

This document describes how LocalAgentCLI connects to **stdio** MCP servers, how discovered tools appear to the model, and how safety rules apply. Configuration keys live under `[mcp_servers]` in the main config file; see [session-and-config.md](session-and-config.md) for the TOML layout. For built-in tools and the shared tool router, see [tool-system.md](tool-system.md). For approvals and sandbox modes, see [safety-and-permissions.md](safety-and-permissions.md).

---

## What is supported

- **Transport**: stdio only (the CLI spawns a subprocess and speaks JSON-RPC over newline-delimited messages).
- **Protocol**: `initialize`, `notifications/initialized`, `tools/list`, and `tools/call` against protocol version `2024-11-05`.
- **Models**: MCP tools are ordinary function tools in the model’s tool list. They work with **local** and **remote** targets the same way as built-in tools.

---

## Configuration

Each server is a nested table under `[mcp_servers.<name>]`:

| Field | Required | Description |
|---|---|---|
| `command` | Yes | Executable to run (for example `python` or a full path). |
| `args` | No | Argument list passed after `command`. |
| `cwd` | No | Working directory for the child process. |
| `env` | No | Extra environment variables. When set, values are **merged on top of** the parent process environment so inherited variables such as `PATH` remain available unless overridden. |
| `timeout` | No | Per-request I/O timeout in seconds for reading MCP responses (default `15`). If a read times out, the server process is terminated and the next tool use starts a fresh connection. |

Empty or invalid server entries are skipped when the manager is built.

---

## Tool naming and schemas

- Discovered tools are exposed to the model under names of the form **`mcp__<server>__<sanitized_tool>`**, where `<server>` is the config table key and `<sanitized_tool>` is the MCP tool name with non-alphanumeric characters replaced by `_`.
- If two different MCP tool names sanitize to the same string, the second and later tools receive an extra deterministic suffix so every registered name stays unique.
- Parameter schemas from the server must pass the same JSON Schema subset as built-in tools (see [tool-system.md](tool-system.md)); invalid schemas cause registration to fail when the router is built.

---

## Read-only hints and approvals

MCP tools may include a boolean **`readOnlyHint`** in `annotations` (MCP tool metadata):

- If `readOnlyHint` is **true**, the tool is treated as **read-only**: it does not require approval in balanced mode (unless classified high-risk by other rules) and may participate in **parallel read-only batches** in the agent loop when all other eligibility checks pass.
- If `readOnlyHint` is **false** or **omitted**, the tool is treated as potentially side-effecting: it follows the same **balanced vs autonomous** approval rules as built-in mutating tools.

High-risk classification still applies where relevant (for example sensitive paths on built-in file tools). External MCP tools are not workspace file tools, so path-based high-risk patterns usually do not apply unless future integration adds them.

---

## Sandbox mode

`safety.sandbox_mode` applies to MCP tools the same way as to built-in tools:

- **`read-only`**: any tool with `is_read_only == false` is **blocked** before execution, including MCP tools without `readOnlyHint: true`.
- **`workspace-write`** and **`danger-full-access`**: follow the normal approval and boundary rules described in [safety-and-permissions.md](safety-and-permissions.md).

---

## Project guidance and “skills”

LocalAgentCLI does **not** ship a separate skills pack installer or marketplace. The supported way to give the model **project-specific guidance** is:

- Repository-root **`AGENTS.md`** (auto-loaded when present), and  
- **Pinned instructions** on the session.

Treat optional future “skill packs” (curated prompt overlays or tool bundles) as **not part of the current product surface** until explicitly documented and implemented.

---

## Limitations (current release)

- **No HTTP or SSE MCP transports** — only stdio subprocess servers.
- **No OAuth**, resource subscriptions, or interactive **elicitation** flows.
- **No guarantee** that a misbehaving or malicious MCP server is confined beyond process boundaries; operators should only configure servers they trust.

---

## Implementation references (for contributors)

- Client and discovery: `localagentcli/mcp/client.py` (`StdioMcpClient`, `McpManager`).
- Runtime wiring: `localagentcli/runtime/core.py` (`RuntimeServices.build_tool_router`).
