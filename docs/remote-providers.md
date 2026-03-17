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
- **Features**: Streaming via SSE, tool/function calling, JSON mode
- **Base URL**: Configurable (default: `https://api.openai.com/v1`)

### Anthropic-Style
- **Protocol**: Anthropic Messages API (`/v1/messages`)
- **Covers**: Anthropic Claude models
- **Features**: Streaming via SSE, tool use, extended thinking
- **Base URL**: Configurable (default: `https://api.anthropic.com`)
- **Auth**: `x-api-key` header + `anthropic-version` header

### Generic REST
- **Protocol**: User-defined REST endpoints
- **Covers**: Any API that accepts a JSON request body and returns a JSON response
- **Configuration**: Requires user to specify request/response field mappings
- **Features**: Basic streaming support (if the API supports SSE), no automatic tool calling

---

## Provider Configuration

### Adding a Provider

The `/providers add` command launches an interactive wizard:

1. **Select type**: OpenAI-compatible, Anthropic, or Generic REST
2. **Enter name**: A user-friendly label (e.g., "openai", "my-local-server")
3. **Enter base URL**: The API endpoint (defaults provided for known types)
4. **Enter API key**: Stored securely (see Key Storage below)
5. **Configure options** (optional):
   - Default model name (e.g., "gpt-4o", "claude-sonnet-4-20250514")
   - Custom headers
   - Timeout settings
   - For Generic REST: request/response field mappings
6. **Test connection**: Optional connectivity test

### Provider Registry Entry

```json
{
  "name": "openai",
  "type": "openai",
  "base_url": "https://api.openai.com/v1",
  "default_model": "gpt-4o",
  "options": {
    "timeout": 30,
    "custom_headers": {}
  },
  "status": "active",
  "added_at": "2025-01-15T10:30:00Z"
}
```

Provider entries are stored in `~/.localagent/config.toml` under the `[providers]` section. API keys are stored separately (see Key Storage).

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
- **Anthropic**: Models are known statically (the API does not expose a model list endpoint); the system maintains a built-in list of Anthropic models with their capabilities
- **Generic REST**: No automatic discovery; user must specify the model name

```python
class RemoteProvider(ABC):
    @abstractmethod
    def list_models(self) -> list[RemoteModelInfo]:
        """Discover available models from this provider."""

    @abstractmethod
    def get_model_capabilities(self, model_name: str) -> dict:
        """Return capabilities for a specific model (tool_use, reasoning, streaming)."""
```

---

## Provider Scope

- **Global config**: The default provider is set in `~/.localagent/config.toml`
- **Session override**: `/providers use <name>` overrides the provider for the current session only. This override is held in memory and not persisted.
- **Precedence**: Session override > global config

---

## Unified Interface

Remote providers implement the same `ModelBackend` ABC as local backends:

```python
# localagentcli/providers/base.py

from localagentcli.models.backends.base import ModelBackend

class RemoteProvider(ModelBackend):
    """Base class for remote providers. Extends ModelBackend with provider-specific methods."""

    @abstractmethod
    def test_connection(self) -> ConnectionTestResult:
        """Test connectivity to the provider. Returns success/failure with details."""

    @abstractmethod
    def list_models(self) -> list[RemoteModelInfo]:
        """List available models from this provider."""
```

This means the Model Abstraction Layer works identically whether the active model is local or remote.

---

## Provider Implementations

### OpenAIProvider

```python
# localagentcli/providers/openai.py

class OpenAIProvider(RemoteProvider):
    def __init__(self, name: str, base_url: str, api_key: str, default_model: str):
        ...

    def load(self, model_path: Path, **kwargs) -> None:
        """No-op for remote providers (no local model to load)."""

    def unload(self) -> None:
        """No-op for remote providers."""

    def generate(self, messages: list[Message], **kwargs) -> GenerationResult:
        """Send request to /v1/chat/completions without streaming."""

    def stream_generate(self, messages: list[Message], **kwargs) -> Iterator[StreamChunk]:
        """Send request to /v1/chat/completions with stream=True. Yield SSE chunks."""

    def supports_tools(self) -> bool:
        """True for models that support function calling."""

    def supports_reasoning(self) -> bool:
        """True for o1/o3-style reasoning models."""

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
    def __init__(self, name: str, base_url: str, api_key: str, default_model: str):
        ...

    def stream_generate(self, messages: list[Message], **kwargs) -> Iterator[StreamChunk]:
        """POST /v1/messages with stream=True. Handle Anthropic SSE format."""

    def supports_tools(self) -> bool:
        return True  # All Claude models support tool use

    def supports_reasoning(self) -> bool:
        """True for Claude models with extended thinking support."""

    def test_connection(self) -> ConnectionTestResult:
        """Send a minimal messages request and check for valid response."""

    def list_models(self) -> list[RemoteModelInfo]:
        """Return built-in list of known Anthropic models."""
```

### GenericRESTProvider

```python
# localagentcli/providers/rest.py

class GenericRESTProvider(RemoteProvider):
    def __init__(self, name: str, base_url: str, api_key: str,
                 request_mapping: dict, response_mapping: dict):
        ...

    def stream_generate(self, messages: list[Message], **kwargs) -> Iterator[StreamChunk]:
        """Send request using configured mapping. Parse response using response mapping."""
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
    "stream_content_field": "choices[0].delta.content"
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

### StreamChunk Schema

```python
@dataclass
class StreamChunk:
    text: str = ""
    is_reasoning: bool = False
    is_tool_call: bool = False
    tool_call_data: dict | None = None
    is_done: bool = False
    usage: dict | None = None  # Token counts, present on final chunk
```

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

All errors are surfaced to the user via the Shell UI activity log.
