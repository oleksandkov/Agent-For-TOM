"""Helpers shared by the Section-4 test files."""

from __future__ import annotations

import math
from datetime import UTC, datetime

from sqlalchemy import text

from backend.tom.db.session import SessionLocal
from backend.tom.memory.types import EMBEDDING_DIM


def unit_embedding(dims: list[int]) -> list[float]:
    """Return a normalised 384-dim unit vector with 1s at ``dims``."""
    out = [0.0] * EMBEDDING_DIM
    for k in dims:
        out[k] = 1.0
    norm = math.sqrt(sum(v * v for v in out))
    return [v / norm for v in out]


def make_session(session_id: str, *, title: str | None = None) -> str:
    """Insert a Session row so memory FK constraints are satisfied."""
    s = SessionLocal()
    try:
        now = datetime.now(UTC)
        s.execute(
            text(
                "INSERT INTO sessions"
                "(id, title, provider, model, status, total_tokens, created_at, updated_at) "
                "VALUES (:id, :title, '', '', 'open', 0, :now, :now)"
            ),
            {"id": session_id, "title": title or session_id, "now": now},
        )
        s.commit()
    finally:
        s.close()
    return session_id
