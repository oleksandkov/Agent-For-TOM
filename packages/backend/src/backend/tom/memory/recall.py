"""Per-session recall buffer.

Process-local, bounded, thread-safe. ``drain_for_session`` consumes and
clears — designed to be called once when a session closes, with the
drained items handed off to :mod:`backend.tom.memory.embed_on_close`.

Why not in the DB? Recall is "recency window", not durable knowledge.
Persisting it would just create write amplification on every chat
turn; the canonical historical record is ``messages`` (Section 6).
"""

from __future__ import annotations

import threading
from collections import defaultdict, deque
from collections.abc import Iterable
from datetime import datetime
from typing import TYPE_CHECKING

from backend.tom.memory.types import RecallMessage

if TYPE_CHECKING:
    pass


class RecallBuffer:
    """Thread-safe FIFO with per-session isolation."""

    def __init__(self, maxlen: int = 128) -> None:
        self._lock = threading.Lock()
        self._buffers: dict[str, deque[RecallMessage]] = defaultdict(lambda: deque(maxlen=maxlen))
        self._maxlen = maxlen

    @property
    def maxlen(self) -> int:
        return self._maxlen

    def push(self, msg: RecallMessage) -> None:
        with self._lock:
            self._buffers[msg.session_id].append(msg)

    def drain_for_session(self, session_id: str) -> list[RecallMessage]:
        """Return and clear all buffered messages for ``session_id``."""
        with self._lock:
            buf = self._buffers.get(session_id)
            if not buf:
                return []
            items = list(buf)
            buf.clear()
            return items

    def peek(self, session_id: str) -> list[RecallMessage]:
        """Read-only view (does not clear)."""
        with self._lock:
            buf = self._buffers.get(session_id)
            return list(buf) if buf else []

    def sessions(self) -> Iterable[str]:
        with self._lock:
            return list(self._buffers.keys())

    def reset(self) -> None:
        """Drop every buffer. Test-only."""
        with self._lock:
            self._buffers.clear()


__all__: list[str] = ["RecallBuffer"]


def make_recall_message(
    *, session_id: str, role: str, content: str, created_at: datetime | None = None
) -> RecallMessage:
    """Convenience helper; defaults ``created_at`` to now(UTC)."""
    from datetime import UTC

    return RecallMessage(
        session_id=session_id,
        role=role,
        content=content,
        created_at=created_at or datetime.now(UTC),
    )
