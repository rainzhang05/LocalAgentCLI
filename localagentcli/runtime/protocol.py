"""Typed runtime submissions and events shared across surfaces."""

from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import uuid4

from localagentcli.agents.events import AgentEvent
from localagentcli.models.backends.base import StreamChunk

ApprovalDecision = Literal["approve", "deny", "approve_all"]
ApprovalPolicy = Literal["shell", "deny", "auto"]
RuntimeEventType = Literal[
    "turn_started",
    "route_selected",
    "stream_chunk",
    "agent_event",
    "approval_requested",
    "turn_completed",
    "turn_failed",
    "turn_interrupted",
    "warning",
    "error",
    "shutdown",
]


@dataclass(frozen=True)
class RuntimeOp:
    """Base class for runtime submissions."""

    type: str


@dataclass(frozen=True)
class UserTurnOp(RuntimeOp):
    """Submit one user turn through the shared runtime."""

    prompt: str
    mode: str | None = None
    approval_policy: ApprovalPolicy = "shell"

    def __init__(
        self,
        prompt: str,
        mode: str | None = None,
        approval_policy: ApprovalPolicy = "shell",
    ) -> None:
        object.__setattr__(self, "type", "user_turn")
        object.__setattr__(self, "prompt", prompt)
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "approval_policy", approval_policy)


@dataclass(frozen=True)
class ApprovalDecisionOp(RuntimeOp):
    """Respond to a pending approval request."""

    decision: ApprovalDecision
    autonomous: bool = False

    def __init__(self, decision: ApprovalDecision, autonomous: bool = False) -> None:
        object.__setattr__(self, "type", "approval_decision")
        object.__setattr__(self, "decision", decision)
        object.__setattr__(self, "autonomous", autonomous)


@dataclass(frozen=True)
class InterruptOp(RuntimeOp):
    """Interrupt the active turn."""

    def __init__(self) -> None:
        object.__setattr__(self, "type", "interrupt")


@dataclass(frozen=True)
class ShutdownOp(RuntimeOp):
    """Shut down the runtime session."""

    def __init__(self) -> None:
        object.__setattr__(self, "type", "shutdown")


@dataclass
class Submission:
    """One submitted runtime operation."""

    op: RuntimeOp
    id: str = field(default_factory=lambda: str(uuid4()))
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "op": serialize_runtime_value(self.op),
        }


@dataclass
class RuntimeEvent:
    """A typed event emitted by the runtime."""

    type: RuntimeEventType
    submission_id: str
    data: Any = None
    message: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    @property
    def is_terminal(self) -> bool:
        return self.type in {"turn_completed", "turn_failed", "turn_interrupted", "shutdown"}

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "type": self.type,
            "submission_id": self.submission_id,
            "timestamp": self.timestamp,
        }
        if self.message:
            payload["message"] = self.message
        if self.data is not None:
            payload["data"] = serialize_runtime_value(self.data)
        return payload


def serialize_runtime_value(value: Any) -> Any:
    """Serialize a runtime payload into JSON-compatible data."""
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, StreamChunk):
        return value.to_dict()
    if isinstance(value, AgentEvent):
        return _serialize_dataclass_with_type(value)
    if isinstance(value, RuntimeOp):
        return _serialize_dataclass_with_type(value)
    if isinstance(value, Submission):
        return value.to_dict()
    if isinstance(value, RuntimeEvent):
        return value.to_dict()
    if is_dataclass(value):
        return _serialize_dataclass_with_type(value)
    if isinstance(value, dict):
        return {str(key): serialize_runtime_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [serialize_runtime_value(item) for item in value]
    return repr(value)


def _serialize_dataclass_with_type(value: Any) -> dict[str, object]:
    data: dict[str, object] = {}
    for item in fields(value):
        data[item.name] = serialize_runtime_value(getattr(value, item.name))
    data.setdefault("type", getattr(value, "type", value.__class__.__name__.lower()))
    return data
