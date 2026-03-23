"""ModelInfo — normalized metadata about capabilities and limits."""

from dataclasses import dataclass, field


@dataclass
class ModelInfo:
    """Metadata about a model's capabilities and limits."""

    id: str
    name: str = ""
    context_window: int = 8192
    default_max_tokens: int = 4096
    supported_reasoning_levels: list[str] = field(default_factory=list)
    effective_context_window_percent: float = 0.8
    capabilities: dict = field(default_factory=dict)
    capability_provenance: dict = field(default_factory=dict)
    selection_state: str = "api_discovered"

    def __post_init__(self) -> None:
        if not self.name:
            self.name = self.id
