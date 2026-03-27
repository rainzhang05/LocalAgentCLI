"""Tests for localagentcli.session.compactor."""

from __future__ import annotations

import json
from datetime import datetime

from localagentcli.models.backends.base import GenerationResult
from localagentcli.session.compactor import ContextCompactor
from localagentcli.session.state import Message


class FakeModel:
    """Simple fake model for summary generation."""

    def __init__(self, text: str = "summary", fail: bool = False) -> None:
        self.text = text
        self.fail = fail
        self.calls: list[tuple[list, dict]] = []

    def generate(self, messages: list, **kwargs):
        self.calls.append((messages, kwargs))
        if self.fail:
            raise RuntimeError("boom")
        return GenerationResult(text=self.text)


def _messages(count: int, content: str = "message") -> list[Message]:
    return [
        Message(role="user", content=f"{content} {index}", timestamp=datetime.now())
        for index in range(count)
    ]


class TestContextCompactor:
    def test_needs_compaction_false_for_small_history(self):
        compactor = ContextCompactor(FakeModel(), context_limit=1000)
        assert compactor.needs_compaction(_messages(2, "short")) is False

    def test_needs_compaction_true_for_large_history(self):
        compactor = ContextCompactor(FakeModel(), context_limit=50)
        assert compactor.needs_compaction(_messages(3, "x" * 80)) is True

    def test_needs_compaction_uses_usage_snapshot_when_larger_than_heuristic(self):
        compactor = ContextCompactor(FakeModel(), context_limit=1000)

        assert (
            compactor.needs_compaction(
                _messages(2, "short"),
                usage_snapshot={"latest_prompt_tokens": 900},
            )
            is True
        )

    def test_needs_compaction_uses_heuristic_when_usage_snapshot_is_stale(self):
        compactor = ContextCompactor(FakeModel(), context_limit=50)

        assert (
            compactor.needs_compaction(
                _messages(3, "x" * 80),
                usage_snapshot={"latest_prompt_tokens": 1},
            )
            is True
        )

    def test_compact_replaces_older_messages_with_summary(self):
        compactor = ContextCompactor(FakeModel(text="Compacted summary"), context_limit=100)
        messages = _messages(12, "content " * 20)

        compacted = compactor.compact(messages, ["Pinned rule"])

        assert len(compacted) == 11
        assert compacted[0].is_summary is True
        assert compacted[0].content == "Compacted summary"
        assert compactor.last_compacted_count == 2

    def test_compact_falls_back_when_model_summary_fails(self):
        compactor = ContextCompactor(FakeModel(fail=True), context_limit=100)
        messages = _messages(12, "content " * 20)

        compacted = compactor.compact(messages, [])

        assert compacted[0].is_summary is True
        assert compacted[0].content.startswith("Summary of 2 earlier messages:")

    def test_format_transcript_omits_redundant_middle_messages(self):
        transcript = ContextCompactor._format_transcript(_messages(60, "payload"))

        assert "middle messages omitted during compaction" in transcript
        assert "[user] payload 0" in transcript
        assert "[user] payload 59" in transcript

    def test_format_transcript_preserves_tool_schema_fields(self):
        tool_message = Message(
            role="tool",
            content=json.dumps(
                {
                    "tool": "file_read",
                    "status": "success",
                    "summary": "Read file",
                    "output": "alpha beta gamma",
                }
            ),
            metadata={"tool_name": "file_read", "status": "success"},
            timestamp=datetime.now(),
        )

        transcript = ContextCompactor._format_transcript([tool_message])

        assert "[tool:file_read status=success]" in transcript
        assert "summary=Read file" in transcript
        assert "output=alpha beta gamma" in transcript
