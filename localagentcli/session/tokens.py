"""Coarse token estimates for context budgeting (UTF-8 byte heuristic)."""

from __future__ import annotations

import json

from localagentcli.session.state import Message

# Match codex-rs `truncate::APPROX_BYTES_PER_TOKEN` — ceiling(bytes/4), not tokenizer-exact.
APPROX_BYTES_PER_TOKEN = 4

# Per-message framing cost (roles, JSON envelope) so compaction does not ignore structure.
_PER_MESSAGE_OVERHEAD_TOKENS = 4

# Cap attributed to metadata so pathological tool payloads do not dominate unfairly.
_METADATA_TOKEN_CAP = 128


def approx_token_count(text: str) -> int:
    """Approximate token count from UTF-8 length using a byte ceiling divisor.

    Mirrors the production heuristic used in codex-rs `truncate::approx_token_count`:
    a coarse lower bound on model-visible text cost, not a real tokenizer count.
    """
    byte_len = len(text.encode("utf-8"))
    return (byte_len + APPROX_BYTES_PER_TOKEN - 1) // APPROX_BYTES_PER_TOKEN


def _metadata_token_estimate(metadata: dict) -> int:
    if not metadata:
        return 0
    try:
        raw = json.dumps(metadata, default=str, separators=(",", ":"))
    except TypeError:
        raw = repr(metadata)
    return min(approx_token_count(raw), _METADATA_TOKEN_CAP)


def estimate_tokens_for_messages(messages: list[Message]) -> int:
    """Sum coarse token estimates for a list of session messages."""
    total = 0
    for message in messages:
        if not isinstance(message, Message):
            raise TypeError("messages must be localagentcli.session.state.Message instances")
        total += approx_token_count(message.role)
        total += approx_token_count(message.content)
        total += _PER_MESSAGE_OVERHEAD_TOKENS
        total += _metadata_token_estimate(message.metadata)
    return total
