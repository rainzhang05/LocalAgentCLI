"""Session and Message dataclasses for runtime state."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Message:
    """A single message in the conversation history."""

    role: str  # "user" | "assistant" | "system" | "tool"
    content: str
    timestamp: datetime
    metadata: dict = field(default_factory=dict)
    is_summary: bool = False

    def to_dict(self) -> dict:
        """Serialize to a JSON-compatible dict."""
        return {
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata,
            "is_summary": self.is_summary,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Message:
        """Deserialize from a dict."""
        return cls(
            role=data["role"],
            content=data["content"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            metadata=data.get("metadata", {}),
            is_summary=data.get("is_summary", False),
        )


@dataclass
class Session:
    """Complete runtime state of an application session."""

    id: str  # UUID string
    name: str | None
    mode: str  # "chat" | "agent"
    model: str
    provider: str
    workspace: str
    history: list[Message] = field(default_factory=list)
    tasks: list = field(default_factory=list)  # TaskPlan (future phases)
    pinned_instructions: list[str] = field(default_factory=list)
    config_overrides: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    metadata: dict = field(default_factory=dict)

    @property
    def is_modified(self) -> bool:
        """Check if the session has been modified since creation."""
        return len(self.history) > 0 or self.updated_at > self.created_at

    def touch(self) -> None:
        """Update the session modification timestamp."""
        self.updated_at = datetime.now()

    def to_dict(self) -> dict:
        """Serialize the full session to a JSON-compatible dict."""
        return {
            "id": self.id,
            "name": self.name,
            "mode": self.mode,
            "model": self.model,
            "provider": self.provider,
            "workspace": self.workspace,
            "history": [m.to_dict() for m in self.history],
            "tasks": self.tasks,
            "pinned_instructions": self.pinned_instructions,
            "config_overrides": self.config_overrides,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Session:
        """Deserialize a session from a dict."""
        return cls(
            id=data["id"],
            name=data.get("name"),
            mode=data["mode"],
            model=data.get("model", ""),
            provider=data.get("provider", ""),
            workspace=data.get("workspace", "."),
            history=[Message.from_dict(m) for m in data.get("history", [])],
            tasks=data.get("tasks", []),
            pinned_instructions=data.get("pinned_instructions", []),
            config_overrides=data.get("config_overrides", {}),
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            metadata=data.get("metadata", {}),
        )
