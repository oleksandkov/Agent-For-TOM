"""Tests for :mod:`backend.tom.memory.recall`."""

from __future__ import annotations

from datetime import UTC, datetime

from backend.tom.memory.recall import RecallBuffer, make_recall_message
from backend.tom.memory.types import RecallMessage


def test_recall_push_and_drain() -> None:
    buf = RecallBuffer(maxlen=10)
    buf.push(make_recall_message(session_id="s", role="user", content="hi"))
    buf.push(make_recall_message(session_id="s", role="assistant", content="hello"))
    items = buf.drain_for_session("s")
    assert [m.role for m in items] == ["user", "assistant"]
    assert buf.drain_for_session("s") == []  # drain clears


def test_recall_is_per_session() -> None:
    buf = RecallBuffer()
    buf.push(make_recall_message(session_id="a", role="user", content="1"))
    buf.push(make_recall_message(session_id="b", role="user", content="2"))
    assert [m.content for m in buf.drain_for_session("a")] == ["1"]
    assert [m.content for m in buf.drain_for_session("b")] == ["2"]


def test_recall_respects_maxlen() -> None:
    buf = RecallBuffer(maxlen=2)
    for i in range(5):
        buf.push(make_recall_message(session_id="s", role="user", content=f"m{i}"))
    items = buf.peek("s")
    assert len(items) == 2
    assert [m.content for m in items] == ["m3", "m4"]


def test_recall_peek_does_not_clear() -> None:
    buf = RecallBuffer()
    buf.push(make_recall_message(session_id="s", role="user", content="x"))
    buf.peek("s")
    assert len(buf.peek("s")) == 1


def test_make_recall_message_defaults_created_at() -> None:
    msg = make_recall_message(session_id="s", role="user", content="hi")
    assert isinstance(msg.created_at, datetime)
    assert msg.created_at.tzinfo is not None


def test_recall_message_roundtrip_pydantic() -> None:
    msg = RecallMessage(
        session_id="s",
        role="user",
        content="hi",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    payload = msg.model_dump_json()
    restored = RecallMessage.model_validate_json(payload)
    assert restored == msg
