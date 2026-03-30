"""Validated agent path primitives for multi-agent routing."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, order=True)
class AgentPath:
    """Canonical absolute agent path.

    Paths always use `/root` as the tree root and only allow path segments
    containing lowercase letters, digits, and underscores.
    """

    value: str

    ROOT = "/root"
    ROOT_SEGMENT = "root"

    def __post_init__(self) -> None:
        validate_absolute_path(self.value)

    @classmethod
    def root(cls) -> AgentPath:
        """Return the canonical root agent path."""
        return cls(cls.ROOT)

    @classmethod
    def from_string(cls, path: str) -> AgentPath:
        """Build a validated path from a string."""
        return cls(path)

    def as_str(self) -> str:
        """Return the raw path string."""
        return self.value

    def is_root(self) -> bool:
        """Whether this path points at the root agent."""
        return self.value == self.ROOT

    def name(self) -> str:
        """Return the final path segment (or `root` for the root path)."""
        if self.is_root():
            return self.ROOT_SEGMENT
        segment = self.value.rsplit("/", maxsplit=1)[-1]
        return segment or self.ROOT_SEGMENT

    def join(self, agent_name: str) -> AgentPath:
        """Append one validated segment and return the new absolute path."""
        validate_agent_name(agent_name)
        return AgentPath.from_string(f"{self}/{agent_name}")

    def resolve(self, reference: str) -> AgentPath:
        """Resolve a relative or absolute reference from this path."""
        if not reference:
            raise ValueError("agent path must not be empty")
        if reference == self.ROOT:
            return AgentPath.root()
        if reference.startswith("/"):
            return AgentPath.from_string(reference)

        validate_relative_reference(reference)
        return AgentPath.from_string(f"{self}/{reference}")

    def __str__(self) -> str:
        return self.value


def resolve_agent_reference(
    current_agent_path: AgentPath | str | None,
    agent_reference: str,
    *,
    allow_root: bool = False,
) -> AgentPath:
    """Resolve a target reference relative to a caller path.

    Args:
        current_agent_path: Current caller path, defaults to `/root`.
        agent_reference: Relative or absolute target reference.
        allow_root: Whether resolving to `/root` is permitted.

    Raises:
        ValueError: On invalid paths or disallowed root references.
    """
    current = (
        AgentPath.root() if current_agent_path is None else _coerce_agent_path(current_agent_path)
    )
    resolved = current.resolve(agent_reference)
    if resolved.is_root() and not allow_root:
        raise ValueError("root is not a spawned agent")
    return resolved


def _coerce_agent_path(value: AgentPath | str) -> AgentPath:
    if isinstance(value, AgentPath):
        return value
    return AgentPath.from_string(value)


def validate_agent_name(agent_name: str) -> None:
    """Validate one path segment."""
    if not agent_name:
        raise ValueError("agent_name must not be empty")
    if agent_name == AgentPath.ROOT_SEGMENT:
        raise ValueError("agent_name `root` is reserved")
    if agent_name in {".", ".."}:
        raise ValueError(f"agent_name `{agent_name}` is reserved")
    if "/" in agent_name:
        raise ValueError("agent_name must not contain `/`")
    if not all(_is_valid_agent_name_char(ch) for ch in agent_name):
        raise ValueError("agent_name must use only lowercase letters, digits, and underscores")


def validate_absolute_path(path: str) -> None:
    """Validate an absolute agent path."""
    if not path.startswith("/"):
        raise ValueError("absolute agent paths must start with `/root`")

    stripped = path[1:]
    segments = stripped.split("/")
    root = segments[0] if segments else ""
    if root != AgentPath.ROOT_SEGMENT:
        raise ValueError("absolute agent paths must start with `/root`")
    if stripped.endswith("/"):
        raise ValueError("absolute agent path must not end with `/`")

    for segment in segments[1:]:
        validate_agent_name(segment)


def validate_relative_reference(reference: str) -> None:
    """Validate a relative agent reference."""
    if reference.endswith("/"):
        raise ValueError("relative agent path must not end with `/`")
    for segment in reference.split("/"):
        validate_agent_name(segment)


def _is_valid_agent_name_char(ch: str) -> bool:
    return ("a" <= ch <= "z") or ("0" <= ch <= "9") or ch == "_"
