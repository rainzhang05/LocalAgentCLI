# LocalAgentCLI — Remote Provider System

This document covers remote model providers: types, configuration, API key management, model discovery, and the provider abstraction. For local models, see [model-system.md](model-system.md).

---

## Overview

Remote providers connect LocalAgentCLI to external API services, enabling use of cloud-hosted models (GPT-4, Claude, Mistral, etc.) through a unified interface. The provider system normalizes all provider-specific protocols into the same `ModelBackend` interface used by local backends.

---

## Supported Provider Types

### OpenAI-Compatible
- **Protocol**: OpenAI Chat Completions API (`/v1/chat/completions`)
- **Covers**: OpenAI, Azure OpenAI, Together AI, Fireworks AI, local servers (vLLM, Ollama with OpenAI mode), and any service implementing the OpenAI API spec
- **Features**: Streaming via SSE, tool/function calling, JSON mode, model-aware reasoning capability detection
- **Base URL**: Configurable (default: `https://api.openai.com/v1`)

### Anthropic-Style
- **Protocol**: Anthropic Messages API (`/v1/messages`)
- **Covers**: Anthropic Claude models
- **Features**: Streaming via SSE, tool use, extended thinking, mixed text/thinking/tool blocks preserved in order
- **Base URL**: Configurable (default: `https://api.anthropic.com`)
- **Auth**: `x-api-key` header + `anthropic-version` header

### Generic REST
- **Protocol**: User-defined REST endpoints
- **Covers**: Any API that accepts a JSON request body and returns a JSON response
- **Configuration**: Requires user to specify request/response field mappings
- **Features**: Basic SSE support plus optional mapped reasoning/tool-call fields when the API exposes them; no automatic capability inference beyond configured flags

---

## Provider Configuration

### Adding a Provider

The `/providers add` command launches an interactive wizard:

1. **Select type**: OpenAI-compatible, Anthropic, or Generic REST
2. **Enter name**: A user-friendly label (e.g., "openai", "my-local-server")
3. **Enter base URL**: The API endpoint (defaults provided for known types)
4. **Enter API key**: Stored securely (see Key Storage below)
5. **Configure options** (optional):
   - Custom headers
   - Timeout settings
    - Prompt caching controls (provider-specific)
   - For Generic REST: request/response field mappings
6. **Test connection**: Optional connectivity test

Provider-level default models are no longer part of the interactive configuration flow. Users choose the actual remote target through `/set` for the current session or `/set default` for the CLI-wide startup default.

### Provider Registry Entry

```json
{
  "name": "openai",
  "type": "openai",
  "base_url": "https://api.openai.com/v1",
  "options": {
    "timeout": 30,
    "custom_headers": {}
  },
  "status": "active",
  "added_at": "2025-01-15T10:30:00Z"
}
```

Provider entries are stored in `~/.localagent/config.toml` under the `[providers]` section. API keys are stored separately (see Key Storage).

### Request timeouts

For model calls, **`providers.<name>.options.timeout`** (seconds) overrides the global **`[timeouts].model_response`** value when set. The effective value is passed through generation options as `request_timeout` on the async request path. Slash-command and other sync provider entrypoints may still use a dedicated sync client so they do not nest `asyncio.run` inside an active event loop.

### Prompt caching (provider-specific)

Prompt caching is now available for remote provider payloads where supported.

- **Anthropic**:
    - set `providers.<name>.options.prompt_cache = true` to enable cache-control
        metadata for stable system prompt layers
    - optional `providers.<name>.options.prompt_cache_type` overrides the
        cache-control type (`ephemeral` by default)
    - when provider-aware prompt assembly is active, system context is segmented so
        stable layers (repository instructions, skills overlays, pinned instructions,
        long-horizon memory) can carry cache-control while dynamic layers
        (environment context and turn-level system history) remain non-cached
- **OpenAI-compatible**: optional pass-through fields are supported for
    compatible backends:
    - `providers.<name>.options.prompt_cache`
    - `providers.<name>.options.prompt_cache_key`
    - `providers.<name>.options.reasoning_effort` (`low` / `medium` / `high`)

For OpenAI-compatible services, these fields are forwarded only when explicitly
configured.

---

## API Key Storage

Keys are stored using a priority system:

### Priority 1: OS Keychain (Preferred)
- **macOS**: macOS Keychain via `keyring` library
- **Linux**: libsecret (GNOME Keyring) or KWallet via `keyring`
- **Windows**: Windows Credential Store via `keyring`
- **Service name**: `localagentcli`
- **Username**: Provider name (e.g., `openai`, `anthropic`)

### Priority 2: Encrypted Local Storage (Fallback)
- Location: `~/.localagent/secrets/`
- Encryption: AES-256-GCM with a key derived from machine-specific entropy
- Used when OS keychain is not available (headless servers, containers)

### Key Management Interface

```python
# localagentcli/providers/keys.py

class KeyManager:
    def store_key(self, provider_name: str, api_key: str) -> None:
        """Store an API key. Tries OS keychain first, falls back to encrypted file."""

    def retrieve_key(self, provider_name: str) -> str | None:
        """Retrieve a stored API key."""

    def delete_key(self, provider_name: str) -> None:
        """Delete a stored API key."""

    def has_key(self, provider_name: str) -> bool:
        """Check if a key exists for a provider."""
```

---

## Model Discovery

Remote providers can advertise their available models:

- **OpenAI-compatible**: `GET /v1/models` returns a list of available models
- **Anthropic**: `GET /v1/models` returns the models accessible to the current API key
- **Generic REST**: Attempts `GET /models` (or a configured models endpoint); optional legacy fallback to an older stored `default_model` remains only for backwards compatibility with pre-existing provider configs
- Provider instances are always bound to one active remote model id. Capability checks (`tool_use`, `reasoning`) are evaluated against that active model, not just the provider type.
- Discovered models also carry readiness metadata:
  - `selection_state = "api_discovered"` when the provider returned the model live from its discovery API
  - `selection_state = "legacy_fallback"` when discovery failed and the provider had to reuse its stored `default_model`
  - `capability_provenance` explains whether a capability claim is inferred, configured, or only a legacy fallback

```python
class RemoteProvider(ABC):
    @abstractmethod
    def list_models(self) -> list[RemoteModelInfo]:
        """Discover available models from this provider."""

    @abstractmethod
    def get_model_capabilities(self, model_name: str) -> dict:
        """Return capabilities for a specific model (tool_use, reasoning, streaming)."""
```

### Capability Confidence Rules

Remote model readiness uses provider-specific provenance:

- OpenAI-compatible and Anthropic models discovered from the provider API keep the existing capability booleans, but their tiers are marked `inferred`
- Generic REST models discovered from the provider API keep the configured capability booleans, and their tiers are marked `configured`
- Any model returned only from the older provider `default_model` fallback keeps its existing booleans, but every tier is marked `legacy_fallback`

This keeps the runtime APIs stable while making it explicit when the CLI is relying on heuristics, configured flags, or compatibility-only fallback data.

### Readiness posture and tradeoffs

Each selected remote model now carries a compact operator posture derived from
its discovery state and tool-use confidence:

- `ready`: agent mode can run tool steps on this target
- `degraded`: chat remains available, but agent mode is blocked until discovery
    and selection are refreshed
- `blocked`: chat remains available, but this target is not trusted or capable
    for agent tool execution

Operator-facing surfaces (`/mode agent`, dispatch-time runtime checks,
`/providers list`, and `/providers test`) include:

- readiness posture
- a one-line tradeoff summary (`chat still available` vs `agent blocked`)
- actionable next-step guidance

This keeps model selection transparent without changing the stable capability
fields consumed by existing runtime paths.

---

## Provider Scope

- **CLI-wide default target**: `provider.active_provider` + `model.active_model` in `~/.localagent/config.toml` store the startup target selected by `/set default`
- **Session override**: `/set` overrides the provider and remote model for the current session only. This override is held in memory and not persisted.
- **Precedence**: Session override > CLI-wide default target

---

## Unified Interface

Remote providers implement the same `ModelBackend` ABC as local backends:

```python
# localagentcli/providers/base.py

from localagentcli.models.backends.base import ModelBackend

class RemoteProvider(ModelBackend):
    """Base class for remote providers. Extends ModelBackend with provider-specific methods."""

    def set_active_model(self, model_name: str | None) -> None:
        """Bind the provider instance to a specific remote model id."""

    def prompt_profile(self) -> ProviderPromptProfile:
        """Provider-aware prompt assembly hints for system-layer formatting."""

    def close(self) -> None:
        """Close the underlying HTTP client."""

    @abstractmethod
    def test_connection(self) -> ConnectionTestResult:
        """Test connectivity to the provider. Returns success/failure with details."""

    @abstractmethod
    def list_models(self) -> list[RemoteModelInfo]:
        """List available models from this provider."""
```

This means the Model Abstraction Layer works identically whether the active model is local or remote.

Shared provider requirements:
- Requests must go through a bounded retry wrapper for timeout, connection-reset, and retryable HTTP status handling.
- Streaming requests must surface normalized `error` and `done` chunks instead of leaking raw transport exceptions into the shell.
- Async streaming requests enforce an optional idle timeout (`idle_stream_timeout` / per-call `stream_idle_timeout`) and normalize idle stalls into the same `error` + `done` stream contract.
- Async client lifetime policy is configurable per provider via `connection_policy` (`reuse` default, `close_after_turn` for one-turn connection reuse constraints).
- Provider HTTP clients must be closed when the active provider changes or the shell exits.
- New provider configs should not depend on provider-level `default_model` values; that field is legacy compatibility only.
- Agent-mode gating should trust only models whose tool-use readiness is backed by `verified`, `inferred`, or `configured` provenance.

---

## Provider Implementations

### OpenAIProvider

```python
# localagentcli/providers/openai.py

class OpenAIProvider(RemoteProvider):
    def __init__(self, name: str, base_url: str, api_key: str, default_model: str = ""):
        ...

    def load(self, model_path: Path, **kwargs) -> None:
        """No-op for remote providers (no local model to load)."""

    def unload(self) -> None:
        """No-op for remote providers."""

    def generate(self, messages: list[Message], **kwargs) -> GenerationResult:
        """Send request to /v1/chat/completions without streaming."""

    def stream_generate(self, messages: list[Message], **kwargs) -> Iterator[StreamChunk]:
        """Send request to /v1/chat/completions with stream=True. Yield normalized SSE chunks."""

    def supports_tools(self) -> bool:
        """Model-aware capability check for function calling."""

    def supports_reasoning(self) -> bool:
        """Model-aware capability check for o1/o3/o4/GPT-5-style reasoning models."""

    def supports_streaming(self) -> bool:
        return True

    def test_connection(self) -> ConnectionTestResult:
        """GET /v1/models and check for a valid response."""

    def list_models(self) -> list[RemoteModelInfo]:
        """GET /v1/models and parse the response."""
```

### AnthropicProvider

```python
# localagentcli/providers/anthropic.py

class AnthropicProvider(RemoteProvider):
    def __init__(self, name: str, base_url: str, api_key: str, default_model: str = ""):
        ...

    def stream_generate(self, messages: list[Message], **kwargs) -> Iterator[StreamChunk]:
        """POST /v1/messages with stream=True. Preserve mixed text/thinking/tool blocks in order."""

    def supports_tools(self) -> bool:
        """Model-aware capability check for the currently selected Claude model."""

    def supports_reasoning(self) -> bool:
        """Model-aware capability check for Claude models with extended thinking support."""

    def test_connection(self) -> ConnectionTestResult:
        """GET /v1/models and check for a valid response."""

    def list_models(self) -> list[RemoteModelInfo]:
        """GET /v1/models and parse the response."""
```

### GenericRESTProvider

```python
# localagentcli/providers/rest.py

class GenericRESTProvider(RemoteProvider):
    def __init__(self, name: str, base_url: str, api_key: str,
                 request_mapping: dict, response_mapping: dict):
        ...

    def stream_generate(self, messages: list[Message], **kwargs) -> Iterator[StreamChunk]:
        """Send request using configured mapping. Parse primary and optional secondary fields."""

    def list_models(self) -> list[RemoteModelInfo]:
        """Try a configured models endpoint, then use any legacy stored fallback only if present."""
```

The `request_mapping` and `response_mapping` dicts define how to translate between the unified message format and the provider's expected format:

```json
{
  "request_mapping": {
    "messages_field": "messages",
    "model_field": "model",
    "stream_field": "stream"
  },
  "response_mapping": {
    "content_field": "choices[0].message.content",
    "stream_content_field": "choices[0].delta.content",
    "reasoning_field": "choices[0].message.reasoning",
    "tool_calls_field": "choices[0].message.tool_calls",
    "stream_reasoning_field": "choices[0].delta.reasoning",
    "stream_tool_calls_field": "choices[0].delta.tool_calls"
  }
}
```

---

## Streaming Protocol

All providers must support streaming via SSE (Server-Sent Events):

1. Send the request with streaming enabled
2. Read the SSE stream line by line
3. Parse each `data:` line into a `StreamChunk`
4. Yield chunks to the caller
5. Handle `[DONE]` or equivalent termination signals
6. Handle connection errors with retry logic (configurable timeout, max retries)
7. Preserve secondary events such as reasoning, provider notifications, and streamed tool-call metadata

### StreamChunk Schema

```python
@dataclass
class StreamChunk:
    text: str = ""
    kind: Literal["final_text", "reasoning", "tool_call", "notification", "error", "done"]
    importance: Literal["primary", "secondary"] = "primary"
    transient: bool = False
    payload: dict | None = None
    is_reasoning: bool = False  # legacy compatibility
    is_tool_call: bool = False  # legacy compatibility
    tool_call_data: dict | None = None
    is_done: bool = False       # legacy compatibility
    usage: dict | None = None  # Token counts, present on final chunk
```

- `notification` may be primary or secondary. Important mid-process notices can stay high-contrast, while low-priority provider status remains dimmed.
- `error` may be primary or secondary depending on severity and is followed by a `done` chunk with `finish_reason="error"`.
- Ordered chunks are preserved into `GenerationResult.chunks` so the shell and session history can render or inspect the full sequence.

---

## Error Handling

| Error | Behavior |
|---|---|
| Invalid API key | Clear error message, suggest `/providers add` to reconfigure |
| Rate limit (429) | Display retry-after time, wait and retry automatically |
| Server error (5xx) | Retry up to 3 times with exponential backoff |
| Timeout | Display timeout message, suggest increasing timeout via `/config` |
| Network unreachable | Clear error, suggest checking connectivity |
| Model not found | List available models, suggest valid model names |

Retryable HTTP errors (`408`, `409`, `425`, `429`, `5xx`) and transient connection failures are retried automatically with bounded backoff before surfacing an error. All final failures are surfaced to the user through normalized secondary output and the Shell UI activity log.
