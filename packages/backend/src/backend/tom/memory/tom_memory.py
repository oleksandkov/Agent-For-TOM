"""The single public memory facade for TOM.

Wraps the three tiers (core/archival/recall) so the rest of the backend
calls only ``TomMemory`` — no Letta types, no raw SQLCipher, no DB
sessions leak out.

Constructor accepts a :class:`RecallBuffer` (defaults to a new one).
Tests can pass a stub buffer; the embed-on-close hook supplies its
own archival session by injecting a memory instance.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from backend.tom.memory.archival import add_archival, search_archival
from backend.tom.memory.core import read_core, write_core
from backend.tom.memory.recall import RecallBuffer, make_recall_message
from backend.tom.memory.types import (
    ArchivalHit,
    CoreMemory,
    CoreMemoryPatch,
    MemoryRecord,
    RecallMessage,
)


class TomMemory:
    """Public memory facade used by the API, chat orchestrator, and tests."""

    def __init__(self, *, recall: RecallBuffer | None = None) -> None:
        self._recall = recall or RecallBuffer()

    @property
    def recall(self) -> RecallBuffer:
        return self._recall

    def read_core(self) -> CoreMemory:
        return read_core()

    def write_core(self, patch: CoreMemoryPatch) -> CoreMemory:
        return write_core(patch)

    def add_archival(
        self,
        *,
        session_id: str,
        summary: str,
        embedding: Sequence[float],
        confidence: float = 1.0,
    ) -> MemoryRecord:
        return add_archival(
            session_id=session_id,
            content=summary,
            embedding=embedding,
            confidence=confidence,
        )

    def search_archival(
        self, *, query_embedding: Sequence[float], k: int = 10
    ) -> list[ArchivalHit]:
        return search_archival(query_embedding=query_embedding, k=k)

    def push_recall(
        self,
        *,
        session_id: str,
        role: str,
        content: str,
        created_at: datetime | None = None,
    ) -> RecallMessage:
        msg = make_recall_message(
            session_id=session_id,
            role=role,
            content=content,
            created_at=created_at,
        )
        self._recall.push(msg)
        return msg

    def drain_recall_for_session(self, session_id: str) -> list[RecallMessage]:
        return self._recall.drain_for_session(session_id)

    def peek_recall(self, session_id: str) -> list[RecallMessage]:
        return self._recall.peek(session_id)


__all__: list[str] = ["TomMemory"]


def _now() -> datetime:
    return datetime.now(UTC)
