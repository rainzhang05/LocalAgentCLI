"""Helpers for windowing conversation context while preserving critical system state."""

from __future__ import annotations

from localagentcli.models.backends.base import ModelMessage


def recent_context_with_system(
    context: list[ModelMessage],
    tail_limit: int,
) -> list[ModelMessage]:
    """Return recent context while preserving a leading system message when trimmed.

    When a caller slices only the tail of a long conversation, the original leading
    system context (instructions/environment) can be dropped. This helper keeps one
    leading system message if the tail window contains no system role entries.
    """
    if tail_limit <= 0:
        return []
    if len(context) <= tail_limit:
        return list(context)

    tail = list(context[-tail_limit:])
    if any(message.role == "system" for message in tail):
        return tail

    first_system = next((message for message in context if message.role == "system"), None)
    if first_system is None:
        return tail
    return [first_system, *tail]
