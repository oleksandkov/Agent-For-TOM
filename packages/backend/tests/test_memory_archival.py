"""Tests for :mod:`backend.tom.memory.archival`."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.tom.db.init_db import init_db
from backend.tom.memory.archival import (
    EmbeddingDimensionError,
    add_archival,
    count_archival,
    search_archival,
)
from tests._memory_helpers import make_session, unit_embedding


def test_add_archival_roundtrip(virtual_keyring: object, tmp_path: Path) -> None:
    init_db()
    make_session("s-archival-rt")
    vec = unit_embedding([0, 1, 2])
    rec = add_archival(
        session_id="s-archival-rt",
        content="hello",
        embedding=vec,
    )
    assert rec.tier.value == "archival"
    assert rec.source_session_id == "s-archival-rt"
    assert rec.embedding_dim == 384
    assert rec.has_embedding is True
    assert rec.content == "hello"


def test_search_archival_returns_top_k_by_distance(virtual_keyring: object, tmp_path: Path) -> None:
    init_db()
    for sid in ("sa", "sb", "sc", "sd"):
        make_session(sid)
    # Embeddings chosen so distances are well-separated:
    #   exact match → distance 0
    #   shares one axis with query → ~0.293
    #   shares zero axes → 1.0
    add_archival(
        session_id="sa",
        content="matches-query",
        embedding=unit_embedding([5, 4]),
    )
    add_archival(
        session_id="sb",
        content="somewhat-close",
        embedding=unit_embedding([5]),
    )
    add_archival(
        session_id="sc",
        content="orthogonal",
        embedding=unit_embedding([1, 2]),
    )
    add_archival(
        session_id="sd",
        content="other-far",
        embedding=unit_embedding([200]),
    )
    hits = search_archival(query_embedding=unit_embedding([5, 4]), k=3)
    assert len(hits) == 3
    assert hits[0].record.content == "matches-query"
    assert hits[0].distance < hits[1].distance < hits[2].distance


def test_search_archival_filters_tier(virtual_keyring: object, tmp_path: Path) -> None:
    init_db()
    make_session("s-filter")
    add_archival(
        session_id="s-filter",
        content="archival",
        embedding=unit_embedding([10]),
    )
    # Manually insert a core row that should never come back from search:
    from backend.tom.db.models import MemoryRecordORM, MemoryTier
    from backend.tom.db.session import SessionLocal

    s = SessionLocal()
    try:
        s.add(
            MemoryRecordORM(
                tier=MemoryTier.CORE,
                content='{"blocks":[], "facts":[], "version":1, "updated_at":"2026-01-01T00:00:00+00:00"}',
            )
        )
        s.commit()
    finally:
        s.close()
    hits = search_archival(query_embedding=unit_embedding([10]), k=10)
    assert all(h.record.tier.value == "archival" for h in hits)


def test_add_archival_rejects_wrong_dim(virtual_keyring: object, tmp_path: Path) -> None:
    init_db()
    make_session("s-bad-dim")
    with pytest.raises(EmbeddingDimensionError):
        add_archival(session_id="s-bad-dim", content="x", embedding=[0.1] * 100)


def test_search_archival_rejects_wrong_dim(virtual_keyring: object, tmp_path: Path) -> None:
    init_db()
    with pytest.raises(EmbeddingDimensionError):
        search_archival(query_embedding=[0.1] * 100, k=1)


def test_count_archival_grows_with_inserts(virtual_keyring: object, tmp_path: Path) -> None:
    init_db()
    make_session("s-count-1")
    make_session("s-count-2")
    assert count_archival() == 0
    add_archival(session_id="s-count-1", content="a", embedding=unit_embedding([0]))
    add_archival(session_id="s-count-2", content="b", embedding=unit_embedding([1]))
    assert count_archival() == 2
