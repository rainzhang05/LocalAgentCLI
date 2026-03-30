# LocalAgentCLI — Model System

This document covers local model management: formats, sources, storage, registry, detection, versioning, the Model Abstraction Layer, and local inference backends. For remote model providers, see [remote-providers.md](remote-providers.md).

---

## Supported Model Formats

| Format | File Extension(s) | Backend | Platform |
|---|---|---|---|
| MLX | `.safetensors` (MLX layout), `config.json` with MLX markers | MLX Backend | macOS (Apple Silicon) |
| GGUF | `.gguf` | GGUF Backend | macOS, Linux, Windows |
| PyTorch safetensors | `.safetensors` (standard layout) | Safetensors Backend | macOS, Linux, Windows |

The system must distinguish between MLX-format safetensors and standard PyTorch safetensors by inspecting the model's `config.json` and directory structure.

---

## Model Sources

### Curated Hugging Face Picker
- Command: `/models`
- Opens a layered interactive picker for Hugging Face local models discovered live from the Hub API
- First chooses the runtime/backend family (`PyTorch / Safetensors`, `MLX` when supported, or `GGUF`)
- Then chooses the model family (`GPT-OSS`, `Qwen`, `Llama`, `Gemma`, `Mistral`, `Phi`, `DeepSeek`, `Granite`, etc.)
- Then chooses the exact repository returned by the Hugging Face API for that family/backend pair
- After download completes, the installed model becomes the active local model for the current session

### Hugging Face
- Command: `/models install hf <repo>`
- Downloads from the Hugging Face Hub using the `huggingface_hub` library
- Uses a live per-file progress display when the installed Hub client supports dry-run planning, so large model downloads keep updating continuously instead of waiting for coarse repo-level refreshes
- Supports private repos (with token authentication)
- `/hf-token` stores or replaces the Hugging Face token used for private Hub discovery and downloads
- Respects `.gitignore`-style patterns in repo to skip unnecessary files

### Direct URL
- Command: `/models install url <url>`
- Downloads a single model file or archive from a URL
- Download progress is rendered continuously with live bytes transferred, speed, and ETA updates
- Supports HTTP/HTTPS with resume capability
- Validates file integrity after download (checksum if available)

---

## Storage Layout

All local models are stored under `~/.localagent/models/`:

```
~/.localagent/models/
├── <model-name>/
│   ├── v1/
│   │   ├── model files...
│   │   └── metadata.json
│   └── v2/
│       ├── model files...
│       └── metadata.json
└── <another-model>/
    └── v1/
        └── ...
```

- Each model has a named directory
- Each version is a subdirectory (`v1`, `v2`, etc.)
- `metadata.json` within each version directory stores extracted metadata

---

## Model Registry

The registry lives at `~/.localagent/registry.json` and tracks all installed models.

### Registry Entry Schema

```json
{
  "name": "codellama-7b",
  "version": "v1",
  "format": "gguf",
  "path": "~/.localagent/models/codellama-7b/v1/",
  "size_bytes": 4123456789,
  "capabilities": {
    "tool_use": false,
    "reasoning": false,
    "streaming": true
  },
  "capability_provenance": {
    "tool_use": {
      "tier": "verified",
      "reason": "Local runtimes do not emit structured tool calls yet."
    },
    "reasoning": {
      "tier": "unknown",
      "reason": "Reasoning output has not been verified for this local runtime."
    },
    "streaming": {
      "tier": "verified",
      "reason": "Local runtimes stream token output directly."
    }
  },
  "metadata": {
    "source": "huggingface",
    "repo": "TheBloke/CodeLlama-7B-GGUF",
    "quantization": "Q4_K_M",
    "parameter_count": "7B",
    "installed_at": "2025-01-15T10:30:00Z",
    "backend": "gguf"
  }
}
```

### Capability Provenance

Registry entries keep two related views of local-model behavior:

- `capabilities`: the current boolean flags used by the runtime APIs (`supports_tools()`, `supports_reasoning()`, `supports_streaming()`)
- `capability_provenance`: why those booleans are set and how trustworthy each claim is

Local registry entries use these rules:

- `tool_use = false` with tier `verified`
- `streaming = true` with tier `verified`
- `reasoning = true` with tier `inferred` only when installer fingerprinting matched a reasoning-oriented family
- `reasoning = false` with tier `unknown` otherwise

Older registry entries that do not yet store provenance are normalized to the same local defaults when they are loaded.

### Registry Operations

The `ModelRegistry` class provides:

```python
class ModelRegistry:
    def list_models(self) -> list[ModelEntry]:
        """Return all registered models."""

    def get_model(self, name: str, version: str | None = None) -> ModelEntry:
        """Get a model by name. If version is None, return the latest version."""

    def register(self, entry: ModelEntry) -> None:
        """Add a new model to the registry. Writes to registry.json."""

    def unregister(self, name: str, version: str | None = None) -> None:
        """Remove a model from the registry. If version is None, remove all versions."""

    def update(self, name: str, updates: dict) -> None:
        """Update fields of an existing registry entry."""

    def search(self, query: str) -> list[ModelEntry]:
        """Search installed models by name or metadata."""
```

---

## Model Detection

When a model is installed, the system runs an automatic detection pipeline:

### Step 1: Format Detection
- Scan the downloaded files for format indicators:
  - `.gguf` file → GGUF format
  - `.safetensors` files + `config.json` with `"model_type"` containing MLX-specific keys → MLX format
  - `.safetensors` files + standard PyTorch `config.json` → Safetensors format
- If format is ambiguous, prompt the user to select

### Step 2: Backend Assignment
- GGUF → `GGUFBackend`
- MLX → `MLXBackend` (only on macOS; if on other platforms, fall back to Safetensors if possible, otherwise error)
- Safetensors → `SafetensorsBackend`

### Step 3: Validation
- Verify all required model files are present
- Check file integrity (sizes, basic structure)
- Attempt a minimal load to verify the model is functional

### Step 4: Metadata Extraction
- Read `config.json` for parameter count, architecture, quantization
- Determine capabilities (tool use, reasoning, streaming) from model architecture and metadata
- Record capability provenance alongside those booleans so user-facing commands can explain what is verified, inferred, or still unknown
- Capability inference is conservative: reasoning may be inferred from model family/metadata, but `tool_use` remains `False` unless the runtime can emit structured tool calls
- Calculate total size on disk
- Record source and installation timestamp

### Suggested Implementation

```python
# localagentcli/models/detector.py

class ModelDetector:
    def detect(self, model_path: Path) -> DetectionResult:
        """Run the full detection pipeline on a model directory."""
        format = self._detect_format(model_path)
        backend = self._assign_backend(format)
        self._validate(model_path, format)
        metadata = self._extract_metadata(model_path, format)
        return DetectionResult(format=format, backend=backend, metadata=metadata)

    def _detect_format(self, model_path: Path) -> ModelFormat:
        ...

    def _assign_backend(self, format: ModelFormat) -> str:
        ...

    def _validate(self, model_path: Path, format: ModelFormat) -> None:
        ...

    def _extract_metadata(self, model_path: Path, format: ModelFormat) -> dict:
        ...
```

---

## Versioning

- Multiple versions of the same model are allowed (e.g., different quantizations)
- Each version gets a unique identifier (`v1`, `v2`, ...)
- The registry tracks all versions per model name
- `/set` is the primary interactive way to switch to an installed local model
- `/models use <name>` remains available as a direct alias and loads the latest version by default
- `/models use <name>@v1` loads a specific version
- `/models remove <name>` removes all versions; `/models remove <name>@v1` removes a specific version

---

## Model Abstraction Layer

The Model Abstraction Layer provides a unified interface that hides all backend and provider differences from the rest of the system.

### Unified Interface

```python
# localagentcli/models/abstraction.py

class ModelAbstractionLayer:
    def __init__(self, backend: ModelBackend):
        self._backend = backend

    def generate(self, messages: list[Message], **kwargs) -> GenerationResult:
        """Collect the normalized streaming pipeline into one GenerationResult."""
        return collect_generation_result(self.stream_generate(messages, **kwargs))

    def stream_generate(self, messages: list[Message], **kwargs) -> Iterator[StreamChunk]:
        """Streaming generation. Yields chunks as they are produced."""
        yield from self._backend.stream_generate(messages, **kwargs)

    def supports_tools(self) -> bool:
        """Whether this model can handle tool-use schemas."""
        return self._backend.supports_tools()

    def supports_reasoning(self) -> bool:
        """Whether this model emits reasoning/thinking tokens."""
        return self._backend.supports_reasoning()

    def supports_streaming(self) -> bool:
        """Whether this model supports streaming output. Always True by design."""
        return self._backend.supports_streaming()

    def cancel(self) -> None:
        """Cancel an in-flight generation when the backend supports it."""
        self._backend.cancel()

    def prompt_profile(self) -> ProviderPromptProfile:
        """Provider-aware prompt assembly preferences (cacheability + system-block shape)."""
        ...
```

### Key Rules

1. **Streaming always enabled**: All output flows through `stream_generate()`. The `generate()` method exists for convenience but internally may collect stream output.
2. **Normalize all outputs**: Regardless of backend, output is normalized into ordered `StreamChunk` events with `kind`, `importance`, `transient`, and optional `payload` metadata.
3. **Hide backend differences**: Callers never know or care whether the model is local or remote. The same interface applies uniformly.
4. **Agent mode gate**: If `supports_tools()` returns `False`, the system must refuse to enter agent mode and display a clear message to the user.
5. **Provider-aware prompt hints**: `prompt_profile()` exposes provider-specific prompt-shaping hints (for example, structured Anthropic system blocks and stable-layer cacheability) while preserving generic fallback behavior for local and OpenAI-compatible paths.

### Normalized Output Schema

```python
@dataclass
class StreamChunk:
    text: str = ""
    kind: Literal[
        "final_text",
        "reasoning",
        "tool_call",
        "notification",
        "error",
        "done",
    ] = "final_text"
    importance: Literal["primary", "secondary"] = "primary"
    transient: bool = False
    payload: dict | None = None
    is_reasoning: bool = False   # legacy compatibility
    is_tool_call: bool = False   # legacy compatibility
    tool_call_data: dict | None = None
    is_done: bool = False        # legacy compatibility
    usage: dict | None = None

@dataclass
class GenerationResult:
    text: str
    reasoning: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    finish_reason: str = ""
    chunks: list[StreamChunk] = field(default_factory=list)
```

- `final_text` is the primary assistant response shown in the normal output area.
- `importance` determines whether an event is rendered in the high-contrast primary stream or the dimmed secondary details path.
- `reasoning` and `tool_call` events are normally secondary.
- `notification` and `error` events may be primary or secondary depending on severity; important runtime warnings stay high-contrast, while low-priority provider/model details remain dimmed.
- `done` terminates the stream and may carry final `usage` or `finish_reason` metadata.
- Local backends may emit raw in-band control markup (for example `<|channel|>analysis<|message|>...`). The abstraction layer must normalize those embedded channels into structured `StreamChunk` events before the shell renders them.

---

## Local Backends

### ModelBackend ABC

```python
# localagentcli/models/backends/base.py

from abc import ABC, abstractmethod

class ModelBackend(ABC):
    @abstractmethod
    def load(self, model_path: Path, **kwargs) -> None:
        """Load the model into memory."""

    @abstractmethod
    def unload(self) -> None:
        """Unload the model and free memory."""

    def cancel(self) -> None:
        """Cancel an in-flight generation if the backend supports it."""

    @abstractmethod
    def generate(self, messages: list[Message], **kwargs) -> GenerationResult:
        """Generate a complete response."""

    @abstractmethod
    def stream_generate(self, messages: list[Message], **kwargs) -> Iterator[StreamChunk]:
        """Generate a streaming response."""

    @abstractmethod
    def supports_tools(self) -> bool: ...

    @abstractmethod
    def supports_reasoning(self) -> bool: ...

    @abstractmethod
    def supports_streaming(self) -> bool: ...

    @abstractmethod
    def memory_usage(self) -> int:
        """Return current memory usage in bytes."""

    @abstractmethod
    def capabilities(self) -> dict:
        """Return a dict of all capability flags."""
```

### MLX Backend

- **Module**: `localagentcli/models/backends/mlx.py`
- **Class**: `MLXBackend(ModelBackend)`
- **Platform**: macOS only (Apple Silicon optimized, Intel supported with reduced performance)
- **Dependencies**: `mlx`, `mlx-lm`
- **Behavior**:
  - Automatically detects Apple Silicon and uses Metal acceleration
  - Loads model weights into unified memory
  - Handles tokenization using the model's bundled tokenizer
  - Supports streaming via token-by-token generation
  - Exposes a best-effort `cancel()` hook; MLX runtimes may only stop cleanly between generation chunks
  - Memory management: monitors unified memory pressure, warns when approaching limits
- **Graceful degradation**: If MLX is not available (non-macOS), the backend refuses to load and suggests alternative backends

### GGUF Backend

- **Module**: `localagentcli/models/backends/gguf.py`
- **Class**: `GGUFBackend(ModelBackend)`
- **Platform**: macOS, Linux, Windows
- **Dependencies**: `llama-cpp-python` (Python bindings for llama.cpp)
- **Behavior**:
  - Loads quantized GGUF models
  - Configures thread count based on available CPU cores
  - Uses GPU offloading when CUDA/Metal is available
  - Supports streaming via callback-based token generation
  - Exposes a best-effort `cancel()` hook; some llama.cpp-backed runtimes may finish the current callback chunk before stopping
  - Memory management: configures context size based on available RAM
- **Quantization support**: Q4_0, Q4_K_M, Q5_K_M, Q8_0, and other llama.cpp quantization formats

### Safetensors Backend

- **Module**: `localagentcli/models/backends/safetensors.py`
- **Class**: `SafetensorsBackend(ModelBackend)`
- **Platform**: macOS, Linux, Windows
- **Dependencies**: `torch`, `transformers`, `safetensors`
- **Behavior**:
  - Loads safetensors-format models using Hugging Face Transformers
  - Supports full precision and quantized inference (via bitsandbytes or GPTQ)
  - Uses CUDA if available, falls back to CPU
  - Streaming via `TextIteratorStreamer` from transformers
  - Supports active cancellation through a stopping-criteria hook around the threaded streamer
  - Memory management: uses `torch.cuda.memory_allocated()` or system RAM monitoring

### Backend Responsibilities Summary

| Responsibility | What It Means |
|---|---|
| Load model | Read model files, initialize weights, prepare for inference |
| Handle inference | Process input messages, generate output tokens |
| Normalize output | Emit normalized `StreamChunk` events so chat mode, agent mode, and providers share one rendering/collection pipeline |
| Manage memory | Track usage, warn on pressure, support unloading |
| Expose capabilities | Report `tool_use`, `reasoning`, and `streaming` accurately and conservatively |

---

## Hardware Detection and Warnings

At startup and when loading a model, the system must:

1. **Detect available hardware**: CPU cores, RAM, GPU (type, VRAM), Apple Silicon (yes/no)
2. **Estimate model requirements**: Based on parameter count and quantization level
3. **Warn if model is too heavy**: If estimated requirements exceed 80% of available resources, display a warning but allow the user to proceed
4. **Degrade gracefully**: If a model cannot be loaded due to hardware limitations, display a clear error with suggestions (e.g., "Try a smaller quantization" or "Use a remote provider instead")

```python
# localagentcli/models/detector.py

class HardwareDetector:
    def detect(self) -> HardwareInfo:
        """Detect available hardware capabilities."""

    def can_run_model(self, model_entry: ModelEntry) -> tuple[bool, list[str]]:
        """Check if the model can run on this hardware. Returns (can_run, warnings)."""
```
