"""Automatic context compaction for long-running sessions."""

from __future__ import annotations

import json
from datetime import datetime

from localagentcli.models.abstraction import ModelAbstractionLayer
from localagentcli.models.backends.base import ModelMessage
from localagentcli.session.state import Message
from localagentcli.session.tokens import estimate_tokens_for_messages

_TRANSCRIPT_HEAD_MESSAGES = 16
_TRANSCRIPT_TAIL_MESSAGES = 24


def _default_generation_headroom(context_limit: int) -> int:
    """Reserve part of the window for the next model output before compacting.

    Capped at one quarter of the context limit so tiny windows (tests, small models)
    still get a usable budget; never exceeds 2048 tokens.
    """
    eighth = max(context_limit // 8, 128)
    quarter_cap = max(context_limit // 4, 1)
    return min(eighth, quarter_cap, 2048)


class ContextCompactor:
    """Summarize older history when it approaches the model context limit."""

    def __init__(
        self,
        model: ModelAbstractionLayer,
        context_limit: int,
        threshold: float = 0.75,
        keep_recent: int = 10,
        generation_headroom_tokens: int | None = None,
    ):
        self._model = model
        self._context_limit = max(context_limit, 1)
        self._threshold = threshold
        self._keep_recent = keep_recent
        if generation_headroom_tokens is None:
            self._generation_headroom = _default_generation_headroom(self._context_limit)
        else:
            self._generation_headroom = max(generation_headroom_tokens, 0)
        self._last_compacted_count = 0

    @property
    def last_compacted_count(self) -> int:
        """Return the number of messages summarized by the last compaction."""
        return self._last_compacted_count

    def needs_compaction(self, messages: list[Message]) -> bool:
        """Return True when the history exceeds the configured threshold."""
        effective = max(self._context_limit - self._generation_headroom, 1)
        return self.estimate_tokens(messages) >= int(effective * self._threshold)

    def compact(self, messages: list[Message], pinned: list[str]) -> list[Message]:
        """Replace older messages with a summary while keeping recent turns verbatim."""
        self._last_compacted_count = 0
        if not messages:
            return []
        if len(messages) <= self._keep_recent:
            return list(messages)

        recent = list(messages[-self._keep_recent :])
        older = list(messages[: -self._keep_recent])
        if not older:
            return list(messages)

        summary = self._summarize_messages(older, pinned)
        self._last_compacted_count = len(older)
        return [
            Message(
                role="system",
                content=summary,
                timestamp=datetime.now(),
                metadata={"summary_of": len(older)},
                is_summary=True,
            ),
            *recent,
        ]

    def estimate_tokens(self, messages: list[Message]) -> int:
        """Estimate tokens with a UTF-8 byte ceiling heuristic (coarse lower bound)."""
        return estimate_tokens_for_messages(messages)

    def _summarize_messages(self, messages: list[Message], pinned: list[str]) -> str:
        """Summarize old messages with the active model and fall back if needed."""
        transcript = self._format_transcript(messages)
        instructions = [
            "Summarize the conversation history for future continuation.",
            (
                "Preserve important facts, decisions, constraints, open questions, "
                "and unfinished work."
            ),
            "Write concise plain text with clear bullets.",
        ]
        prompt_parts = []
        if pinned:
            prompt_parts.append("Pinned instructions currently in force:")
            prompt_parts.extend(f"- {instruction}" for instruction in pinned)
        prompt_parts.extend(["Conversation history:", transcript])
        summary_prompt = "\n".join(prompt_parts)

        try:
            result = self._model.generate(
                [
                    ModelMessage(role="system", content="\n".join(instructions)),
                    ModelMessage(role="user", content=summary_prompt),
                ],
                temperature=0.2,
                max_tokens=min(512, max(self._context_limit // 8, 128)),
            )
            if result.text.strip():
                return result.text.strip()
        except Exception:
            pass

        return self._fallback_summary(messages)

    @staticmethod
    def _format_transcript(messages: list[Message]) -> str:
        """Convert messages into a compact transcript string."""
        lines: list[str] = []
        head, tail, omitted_middle = ContextCompactor._segment_messages_for_transcript(messages)
        for message in head:
            lines.append(ContextCompactor._format_message_line(message))
        if omitted_middle > 0:
            lines.append(
                f"[system] ... {omitted_middle} middle messages omitted during compaction ..."
            )
        for message in tail:
            lines.append(ContextCompactor._format_message_line(message))
        return "\n".join(lines)

    @staticmethod
    def _fallback_summary(messages: list[Message]) -> str:
        """Fallback summary when model-based summarization is unavailable."""
        lines = [f"Summary of {len(messages)} earlier messages:"]
        for message in messages[-12:]:
            rendered = ContextCompactor._format_message_line(message)
            if len(rendered) > 220:
                rendered = f"{rendered[:217]}..."
            lines.append(f"- {rendered}")
        return "\n".join(lines)

    @staticmethod
    def _segment_messages_for_transcript(
        messages: list[Message],
    ) -> tuple[list[Message], list[Message], int]:
        total = len(messages)
        if total <= _TRANSCRIPT_HEAD_MESSAGES + _TRANSCRIPT_TAIL_MESSAGES + 4:
            return list(messages), [], 0
        head = list(messages[:_TRANSCRIPT_HEAD_MESSAGES])
        tail = list(messages[-_TRANSCRIPT_TAIL_MESSAGES:])
        omitted = total - len(head) - len(tail)
        return head, tail, max(omitted, 0)

    @staticmethod
    def _format_message_line(message: Message) -> str:
        if message.role != "tool":
            content = " ".join(message.content.split())
            return f"[{message.role}] {content}"

        tool_name = str(message.metadata.get("tool_name", "tool") or "tool")
        status = str(message.metadata.get("status", "") or "")
        header = f"[tool:{tool_name}]" if not status else f"[tool:{tool_name} status={status}]"

        parsed = ContextCompactor._parse_tool_content(message.content)
        summary = ContextCompactor._compact_text(str(parsed.get("summary", "") or ""), 160)
        error = ContextCompactor._compact_text(str(parsed.get("error", "") or ""), 160)
        output_preview = ContextCompactor._compact_text(str(parsed.get("output", "") or ""), 220)

        details: list[str] = []
        if summary:
            details.append(f"summary={summary}")
        if error:
            details.append(f"error={error}")
        if output_preview:
            details.append(f"output={output_preview}")
        if not details:
            raw = ContextCompactor._compact_text(" ".join(message.content.split()), 220)
            if raw:
                details.append(f"raw={raw}")

        return f"{header} {'; '.join(details)}" if details else header

    @staticmethod
    def _parse_tool_content(content: str) -> dict:
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, dict):
            return parsed
        return {}

    @staticmethod
    def _compact_text(text: str, limit: int) -> str:
        if not text:
            return ""
        flattened = " ".join(text.split())
        if len(flattened) <= limit:
            return flattened
        return f"{flattened[: max(limit - 3, 0)]}..."
