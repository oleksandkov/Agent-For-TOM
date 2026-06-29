"""Archival memory (vector store).

Each archival write goes into two places:

1. ``memory_records`` (SQLCipher-encrypted BLOB of the float32 vector
   for redundancy and easy inspection).
2. ``memory_records_vec`` (the vec0 virtual table created in
   ``db/migrations/versions/0001_initial.py``) used for KNN search.

Reads use vec0's ``vec_distance_cosine`` and return ranked
:class:`ArchivalHit` rows.

The dimension is fixed at migration time (see
``EMBEDDING_DIM`` in :mod:`backend.tom.memory.types`).
Changing it requires a new migration.
"""

from __future__ import annotations

import struct
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select, text

from backend.tom.db.models import MemoryRecordORM, MemoryTier
from backend.tom.db.session import SessionLocal
from backend.tom.memory.types import (
    EMBEDDING_DIM,
    ArchivalHit,
    MemoryRecord,
    Tier,
)

if TYPE_CHECKING:
    from collections.abc import Sequence


class EmbeddingDimensionError(ValueError):
    """Raised when an embedding has the wrong length."""


def _pack(vec: Sequence[float]) -> bytes:
    """Pack a float sequence as little-endian float32 bytes for vec0."""
    if len(vec) != EMBEDDING_DIM:
        msg = f"embedding must have dim={EMBEDDING_DIM}, got {len(vec)}"
        raise EmbeddingDimensionError(msg)
    return struct.pack(f"<{len(vec)}f", *vec)


def _unpack(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob))


def _to_public(row: MemoryRecordORM, *, has_embedding: bool) -> MemoryRecord:
    return MemoryRecord(
        id=row.id,
        tier=Tier(row.tier.value),
        content=row.content,
        embedding_dim=row.embedding_dim,
        source_session_id=row.source_session_id,
        confidence=row.confidence,
        created_at=row.created_at,
        has_embedding=has_embedding,
    )


def add_archival(
    *,
    session_id: str,
    content: str,
    embedding: Sequence[float],
    confidence: float = 1.0,
) -> MemoryRecord:
    """Persist an archival row + its vec0 mirror. Atomic-ish per session."""
    packed = _pack(embedding)
    now = datetime.now(UTC)
    s = SessionLocal()
    try:
        record = MemoryRecordORM(
            tier=MemoryTier.ARCHIVAL,
            content=content,
            embedding_blob=packed,
            embedding_dim=EMBEDDING_DIM,
            source_session_id=session_id,
            confidence=confidence,
            created_at=now,
        )
        s.add(record)
        s.flush()
        s.execute(
            text("INSERT INTO memory_records_vec(memory_id, embedding) VALUES (:id, :vec)"),
            {"id": record.id, "vec": packed},
        )
        s.commit()
        s.refresh(record)
        return _to_public(record, has_embedding=True)
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def search_archival(
    *,
    query_embedding: Sequence[float],
    k: int = 10,
) -> list[ArchivalHit]:
    """Return the ``k`` closest archival rows by cosine distance."""
    if k <= 0:
        return []
    packed = _pack(query_embedding)
    s = SessionLocal()
    try:
        rows = s.execute(
            text(
                """
                SELECT m.id AS id,
                       m.content AS content,
                       m.tier AS tier,
                       m.embedding_dim AS embedding_dim,
                       m.source_session_id AS source_session_id,
                       m.confidence AS confidence,
                       m.created_at AS created_at,
                       vec_distance_cosine(mv.embedding, :q) AS distance
                FROM memory_records_vec mv
                JOIN memory_records m ON m.id = mv.memory_id
                WHERE m.tier = 'archival'
                ORDER BY distance ASC
                LIMIT :k
                """
            ),
            {"q": packed, "k": k},
        ).all()
    finally:
        s.close()

    hits: list[ArchivalHit] = []
    for row in rows:
        record = MemoryRecord(
            id=row.id,
            tier=Tier(row.tier),
            content=row.content,
            embedding_dim=row.embedding_dim,
            source_session_id=row.source_session_id,
            confidence=row.confidence,
            created_at=row.created_at,
            has_embedding=True,
        )
        hits.append(ArchivalHit(record=record, distance=float(row.distance)))
    return hits


def count_archival() -> int:
    """Diagnostic — number of archival rows currently stored."""
    s = SessionLocal()
    try:
        from sqlalchemy import func

        return int(
            s.execute(
                select(func.count(MemoryRecordORM.id)).where(
                    MemoryRecordORM.tier == MemoryTier.ARCHIVAL
                )
            ).scalar_one()
        )
    finally:
        s.close()


__all__: list[str] = [
    "EmbeddingDimensionError",
    "add_archival",
    "count_archival",
    "search_archival",
]
