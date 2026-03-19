"""Automatic context compaction for long-running sessions."""

from __future__ import annotations

from datetime import datetime

from localagentcli.models.abstraction import ModelAbstractionLayer
from localagentcli.models.backends.base import ModelMessage
from localagentcli.session.state import Message


class ContextCompactor:
    """Summarize older history when it approaches the model context limit."""

    def __init__(
        self,
        model: ModelAbstractionLayer,
        context_limit: int,
        threshold: float = 0.75,
        keep_recent: int = 10,
    ):
        self._model = model
        self._context_limit = max(context_limit, 1)
        self._threshold = threshold
        self._keep_recent = keep_recent
        self._last_compacted_count = 0

    @property
    def last_compacted_count(self) -> int:
        """Return the number of messages summarized by the last compaction."""
        return self._last_compacted_count

    def needs_compaction(self, messages: list[Message]) -> bool:
        """Return True when the history exceeds the configured threshold."""
        return self.estimate_tokens(messages) >= int(self._context_limit * self._threshold)

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
        """Estimate token count using a conservative text-length heuristic."""
        total_chars = 0
        for message in messages:
            total_chars += len(message.role) + len(message.content) + 8
        return max(total_chars // 4, 0)

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
        for message in messages:
            content = " ".join(message.content.split())
            lines.append(f"[{message.role}] {content}")
        return "\n".join(lines)

    @staticmethod
    def _fallback_summary(messages: list[Message]) -> str:
        """Fallback summary when model-based summarization is unavailable."""
        lines = [f"Summary of {len(messages)} earlier messages:"]
        for message in messages[-12:]:
            content = " ".join(message.content.split())
            if len(content) > 160:
                content = f"{content[:157]}..."
            lines.append(f"- {message.role}: {content}")
        return "\n".join(lines)
