"""Chat controller for conversational mode."""

from __future__ import annotations

from datetime import datetime
from typing import Iterator

from localagentcli.models.abstraction import ModelAbstractionLayer
from localagentcli.models.backends.base import ModelMessage, StreamChunk
from localagentcli.session.compactor import ContextCompactor
from localagentcli.session.instructions import (
    build_instruction_messages,
    build_system_instructions,
)
from localagentcli.session.state import Message, Session


class ChatController:
    """Handle plain-text chat interactions against the active model."""

    def __init__(
        self,
        model: ModelAbstractionLayer,
        session: Session,
        context_limit: int = 8192,
        generation_config: dict[str, object] | None = None,
    ) -> None:
        self._model = model
        self._session = session
        self._compactor = ContextCompactor(model, context_limit)
        self._generation_config = generation_config or {}
        self._last_compaction_count = 0

    @property
    def last_compaction_count(self) -> int:
        """Return the number of messages summarized in the last compaction."""
        return self._last_compaction_count

    @property
    def last_compaction_message(self) -> str | None:
        """Return a user-facing compaction message when compaction occurred."""
        if not self._last_compaction_count:
            return None
        return f"Context compacted: summarized {self._last_compaction_count} messages"

    def handle_input(
        self,
        user_input: str,
        generation_options: dict[str, object] | None = None,
    ) -> Iterator[StreamChunk]:
        """Process one chat turn and yield model stream chunks."""
        self._session.history.append(
            Message(role="user", content=user_input, timestamp=datetime.now())
        )
        self._session.touch()
        self.compact_if_needed()

        messages = self._build_messages()
        options = dict(self._generation_config)
        if generation_options:
            options.update(generation_options)
        return self._stream_response(messages, options)

    def compact_if_needed(self) -> int:
        """Compact session history if it exceeds the configured threshold."""
        self._last_compaction_count = 0
        if not self._compactor.needs_compaction(self._messages_for_token_estimation()):
            return 0

        compacted = self._compactor.compact(
            self._session.history,
            build_system_instructions(self._session),
        )
        self._last_compaction_count = self._compactor.last_compacted_count
        if not self._last_compaction_count:
            return 0

        self._session.history = compacted
        self._session.metadata["last_compaction"] = {
            "count": self._last_compaction_count,
            "timestamp": datetime.now().isoformat(),
        }
        self._session.metadata["compaction_count"] = (
            int(self._session.metadata.get("compaction_count", 0)) + 1
        )
        self._session.touch()
        return self._last_compaction_count

    def pin_instruction(self, instruction: str) -> None:
        """Add a pinned instruction that survives compaction."""
        cleaned = instruction.strip()
        if not cleaned:
            return
        self._session.pinned_instructions.append(cleaned)
        self._session.touch()

    def unpin_instruction(self, index: int) -> None:
        """Remove a pinned instruction by index."""
        del self._session.pinned_instructions[index]
        self._session.touch()

    def _build_messages(self) -> list[ModelMessage]:
        """Build the model input with pinned instructions and session history."""
        system_parts = build_system_instructions(self._session)
        conversation: list[ModelMessage] = []

        for message in self._session.history:
            if message.role == "system":
                system_parts.append(message.content)
                continue
            conversation.append(
                ModelMessage(
                    role=message.role,
                    content=message.content,
                    metadata=dict(message.metadata),
                )
            )

        if system_parts:
            return [ModelMessage(role="system", content="\n\n".join(system_parts)), *conversation]
        return conversation

    def _messages_for_token_estimation(self) -> list[Message]:
        """Build the full context that counts against the model window."""
        return [*build_instruction_messages(self._session), *self._session.history]

    def _stream_response(
        self,
        messages: list[ModelMessage],
        generation_options: dict[str, object],
    ) -> Iterator[StreamChunk]:
        """Stream the assistant response and write it back to session history."""
        assistant_parts: list[str] = []
        reasoning_parts: list[str] = []
        chunks: list[StreamChunk] = []

        for chunk in self._model.stream_generate(messages, **generation_options):
            chunks.append(chunk)
            if chunk.text:
                if chunk.kind == "reasoning":
                    reasoning_parts.append(chunk.text)
                elif chunk.kind == "final_text":
                    assistant_parts.append(chunk.text)
            yield chunk

        assistant_text = "".join(assistant_parts).strip()
        reasoning_text = "".join(reasoning_parts).strip()
        if assistant_text or reasoning_text:
            metadata: dict[str, object] = {
                "chunks": [chunk.to_dict() for chunk in chunks if not chunk.is_done],
            }
            if reasoning_text:
                metadata["reasoning"] = reasoning_text
            self._session.history.append(
                Message(
                    role="assistant",
                    content=assistant_text,
                    timestamp=datetime.now(),
                    metadata=metadata,
                )
            )
            self._session.touch()
