"""Tests for coarse UTF-8 token estimation helpers."""

from __future__ import annotations

from datetime import datetime

import pytest

from localagentcli.session.state import Message
from localagentcli.session.tokens import approx_token_count, estimate_tokens_for_messages


def test_approx_token_count_ascii_one_token_per_four_bytes():
    assert approx_token_count("") == 0
    assert approx_token_count("a") == 1
    assert approx_token_count("abcd") == 1
    assert approx_token_count("abcde") == 2


def test_approx_token_count_utf8_uses_byte_length():
    # "é" is two UTF-8 bytes → ceil(2/4) = 1
    assert approx_token_count("é") == 1
    # Four UTF-8 bytes (e.g. emoji) → ceil(4/4) = 1
    assert approx_token_count("🙂") == 1


def test_estimate_tokens_for_messages_counts_metadata():
    ts = datetime.now()
    base = Message(role="user", content="x", timestamp=ts)
    with_meta = Message(role="user", content="x", timestamp=ts, metadata={"k": "v"})
    assert estimate_tokens_for_messages([with_meta]) > estimate_tokens_for_messages([base])


def test_estimate_tokens_for_messages_is_additive():
    ts = datetime.now()
    one = Message(role="user", content="hi", timestamp=ts)
    two = Message(role="assistant", content="bye", timestamp=ts)
    assert estimate_tokens_for_messages([one, two]) == estimate_tokens_for_messages(
        [one]
    ) + estimate_tokens_for_messages([two])


def test_estimate_tokens_for_messages_rejects_wrong_type():
    with pytest.raises(TypeError):
        estimate_tokens_for_messages([object()])  # type: ignore[list-item]
