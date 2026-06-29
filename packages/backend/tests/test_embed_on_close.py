"""Tests for :mod:`backend.tom.memory.embed_on_close`."""

from __future__ import annotations

import math
from collections.abc import Sequence
from pathlib import Path

from backend.tom.db.init_db import init_db
from backend.tom.db.models import MessageORM
from backend.tom.db.session import SessionLocal
from backend.tom.memory.archival import count_archival
from backend.tom.memory.embed_on_close import _v1_embed, _v1_summarise, embed_on_close


def _unit(dims: Sequence[int]) -> list[float]:
    out = [0.0] * 384
    for k in dims:
        out[k] = 1.0
    norm = math.sqrt(sum(v * v for v in out))
    return [v / norm for v in out]


def _seed_session(session_id: str, msgs: list[tuple[str, str]]) -> None:
    _make_session(session_id)
    s = SessionLocal()
    try:
        for role, content in msgs:
            s.add(
                MessageORM(
                    session_id=session_id,
                    role=role,
                    content=content,
                )
            )
        s.commit()
    finally:
        s.close()


def test_v1_summarise_truncates_and_picks_extremes() -> None:
    rows = [
        MessageORM(
            session_id="s",
            role="user",
            content="a" * 500,
        ),
        MessageORM(
            session_id="s",
            role="assistant",
            content="b" * 500,
        ),
    ]
    summary = _v1_summarise(rows)
    assert "user=" in summary
    assert "assistant=" in summary
    # Length should not exceed ~280 chars
    assert len(summary) < 400


def test_v1_embed_is_deterministic_and_unit() -> None:
    a = _v1_embed("hello")
    b = _v1_embed("hello")
    c = _v1_embed("different")
    assert a == b
    assert a != c
    assert len(a) == 384
    norm = math.sqrt(sum(v * v for v in a))
    assert math.isclose(norm, 1.0, rel_tol=1e-5)


def _make_session(session_id: str) -> None:
    from backend.tom.db.models import SessionORM

    s = SessionLocal()
    try:
        s.add(SessionORM(id=session_id, title=session_id))
        s.commit()
    finally:
        s.close()


def test_embed_on_close_returns_none_when_no_messages(
    virtual_keyring: object, tmp_path: Path
) -> None:
    init_db()
    _make_session("empty-session")
    before = count_archival()
    result = embed_on_close("nonexistent-session")
    assert result is None
    assert count_archival() == before


def test_embed_on_close_archives_a_session(virtual_keyring: object, tmp_path: Path) -> None:
    init_db()
    _seed_session(
        "s-archive",
        [("user", "how are you?"), ("assistant", "fine, thanks")],
    )
    before = count_archival()
    record = embed_on_close("s-archive")
    assert record is not None
    assert record.tier.value == "archival"
    assert count_archival() == before + 1


def test_embed_on_close_uses_injected_embedder(virtual_keyring: object, tmp_path: Path) -> None:
    init_db()
    _seed_session("s-injected", [("user", "ping"), ("assistant", "pong")])
    seen: list[str] = []

    def fake_embedder(text: str) -> list[float]:
        seen.append(text)
        return _unit([42])

    def stub_summary(_rows: Sequence[MessageORM]) -> str:
        return "MARKER-summary"

    record = embed_on_close(
        "s-injected",
        embedder=fake_embedder,
        summarizer=stub_summary,
    )
    assert record is not None
    assert seen == ["MARKER-summary"]


def test_v1_summariser_handles_empty_input() -> None:
    assert _v1_summarise([]) == ""
