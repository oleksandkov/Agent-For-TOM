"""Tests for :mod:`backend.tom.memory.tom_memory`."""

from __future__ import annotations

from pathlib import Path

from backend.tom.db.init_db import init_db
from backend.tom.memory.recall import RecallBuffer
from backend.tom.memory.tom_memory import TomMemory
from backend.tom.memory.types import (
    CoreMemoryBlock,
    CoreMemoryPatch,
    RecallMessage,
    Tier,
)
from tests._memory_helpers import make_session, unit_embedding


def test_facade_exposes_three_tiers(virtual_keyring: object, tmp_path: Path) -> None:
    init_db()
    make_session("s-facade")
    tom = TomMemory()
    tom.read_core()
    tom.add_archival(session_id="s-facade", summary="hi", embedding=unit_embedding([0]))
    hits = tom.search_archival(query_embedding=unit_embedding([0]), k=1)
    assert hits and hits[0].record.content == "hi"
    tom.push_recall(session_id="s", role="user", content="u")
    assert tom.peek_recall("s")[0].role == "user"
    drained = tom.drain_recall_for_session("s")
    assert drained and isinstance(drained[0], RecallMessage)


def test_facade_drain_clears_buffer(virtual_keyring: object, tmp_path: Path) -> None:
    init_db()
    recall = RecallBuffer(maxlen=10)
    tom = TomMemory(recall=recall)
    tom.push_recall(session_id="s", role="user", content="hi")
    tom.push_recall(session_id="s", role="assistant", content="hello")
    drained = tom.drain_recall_for_session("s")
    assert len(drained) == 2
    assert tom.drain_recall_for_session("s") == []


def test_facade_write_core_bumps_version(virtual_keyring: object, tmp_path: Path) -> None:
    init_db()
    tom = TomMemory()
    initial = tom.read_core()
    next_v = tom.write_core(
        CoreMemoryPatch(
            expected_version=initial.version,
            set_blocks=[
                CoreMemoryBlock(label="persona", text="updated"),
            ],
        )
    )
    assert next_v.version == initial.version + 1


def test_facade_search_archival_orders_by_distance(virtual_keyring: object, tmp_path: Path) -> None:
    init_db()
    for sid in ("fa", "fb", "fc"):
        make_session(sid)
    tom = TomMemory()
    tom.add_archival(session_id="fa", summary="exact", embedding=unit_embedding([100]))
    tom.add_archival(session_id="fb", summary="close", embedding=unit_embedding([100, 101]))
    tom.add_archival(session_id="fc", summary="far", embedding=unit_embedding([300]))
    hits = tom.search_archival(query_embedding=unit_embedding([100]), k=2)
    assert len(hits) == 2
    assert hits[0].record.content == "exact"


def test_facade_record_has_correct_tier(virtual_keyring: object, tmp_path: Path) -> None:
    init_db()
    make_session("s-tier")
    tom = TomMemory()
    rec = tom.add_archival(session_id="s-tier", summary="x", embedding=unit_embedding([0]))
    assert rec.tier is Tier.ARCHIVAL
