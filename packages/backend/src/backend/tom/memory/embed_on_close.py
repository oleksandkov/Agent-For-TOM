"""embed-on-close hook.

Called by the chat API (Section 6) when a session transitions from
``open`` to ``closed``:

1. Read every message for the session from the DB.
2. Summarise them (provider call in Section 5; v0.1 stub here).
3. Embed the summary (provider call in Section 5; deterministic
   v0.1 stub here).
4. Write the resulting ``MemoryRecord`` into the archival tier.

The summariser + embedder are passed in (or default to the v0.1
stubs). Tests inject deterministic stand-ins; production code wires
in the provider module from Section 5.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

from backend.tom.db.models import MessageORM
from backend.tom.db.session import SessionLocal
from backend.tom.memory.tom_memory import TomMemory
from backend.tom.memory.types import EMBEDDING_DIM, MemoryRecord

if TYPE_CHECKING:
    pass


Summarizer = Callable[[Sequence[MessageORM]], str]
Embedder = Callable[[str], "list[float]"]


def _v1_summarise(messages: Sequence[MessageORM]) -> str:
    """Stub summariser: take first user + last assistant content.

    Real provider module lands in Section 5; the callable signature
    is stable so this can be swapped without touching call sites.
    """
    if not messages:
        return ""
    first_user = next((m.content for m in messages if m.role == "user"), "")
    last_assistant = ""
    for m in reversed(messages):
        if m.role == "assistant":
            last_assistant = m.content
            break
    return (
        f"session({len(messages)} msgs): user={first_user[:120]!r} "
        f"assistant={last_assistant[:120]!r}"
    )


def _v1_embed(text: str) -> list[float]:
    """Stub embedder: deterministic sha256-seeded dim-EMBEDDING_DIM vector.

    Same shape as the Section 5 ``Provider.embed`` method, so swapping is
    mechanical when the provider module lands.
    """
    seed = int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "little", signed=False)
    raw = []
    for i in range(EMBEDDING_DIM):
        # Cheap pseudo-random; deterministic per (text, i).
        bit = ((seed >> (i % 64)) + i * 2654435761) & 0xFFFFFFFF
        raw.append((bit / 0xFFFFFFFF) * 2.0 - 1.0)
    norm = sum(v * v for v in raw) ** 0.5
    if norm == 0:
        return raw
    return [v / norm for v in raw]


def embed_on_close(
    session_id: str,
    *,
    memory: TomMemory | None = None,
    summarizer: Summarizer | None = None,
    embedder: Embedder | None = None,
    now: datetime | None = None,
) -> MemoryRecord | None:
    """Archive a closed session. Returns the record, or ``None`` if empty.

    On a session with no messages, returns ``None`` without touching
    the archival tier — prevents empty / zero-length summaries from
    polluting embeddings.
    """
    s = SessionLocal()
    try:
        msgs = (
            s.execute(
                select(MessageORM)
                .where(MessageORM.session_id == session_id)
                .order_by(MessageORM.created_at)
            )
            .scalars()
            .all()
        )
    finally:
        s.close()

    if not msgs:
        return None

    summary = (summarizer or _v1_summarise)(msgs)
    embedding = (embedder or _v1_embed)(summary)
    tom = memory or TomMemory()
    record = tom.add_archival(
        session_id=session_id,
        summary=summary,
        embedding=embedding,
    )
    if now is not None:
        # Override `created_at` only when the caller explicitly hands one in.
        # Surface the timestamp through the record; downstream codepaths
        # should not assume settability. Tests use this knob.
        record = record.model_copy(update={"created_at": now})
    return record


__all__: list[str] = [
    "_v1_embed",
    "_v1_summarise",
    "embed_on_close",
]
