"""Tests for :mod:`backend.tom.chat.locks`."""

from __future__ import annotations

import asyncio

import pytest

from backend.tom.chat.locks import SessionLocks


@pytest.mark.asyncio
async def test_acquire_serialization_same_session() -> None:
    locks = SessionLocks()
    order: list[str] = []

    async def worker(label: str) -> None:
        async with locks.acquire(label):
            order.append(f"start:{label}")
            await asyncio.sleep(0.05)
            order.append(f"end:{label}")

    await asyncio.gather(worker("a"), worker("a"), worker("a"))

    # Each "start" must be immediately followed by its matching "end"
    # because the second/third worker waits on the first.
    starts = [i for i, v in enumerate(order) if v.startswith("start:")]
    ends = [i for i, v in enumerate(order) if v.startswith("end:")]
    for s, e in zip(starts, ends, strict=False):
        assert e == s + 1, order


@pytest.mark.asyncio
async def test_different_sessions_dont_block() -> None:
    locks = SessionLocks()
    barrier = asyncio.Event()

    async def hold(label: str) -> None:
        async with locks.acquire(label):
            if not barrier.is_set():
                barrier.set()
            await asyncio.sleep(0.2)

    async def gate() -> None:
        await barrier.wait()
        # give the holder time to actually be inside the lock
        await asyncio.sleep(0.05)
        async with locks.acquire("other"):
            return None

    await asyncio.gather(hold("a"), gate())


def test_has_lock_reports_state() -> None:

    locks = SessionLocks()
    assert locks.has_lock("a") is False
    lock = locks.get("a")
    assert lock.locked() is False
    assert locks.has_lock("a") is False


@pytest.mark.asyncio
async def test_drop_forgets_lock() -> None:
    locks = SessionLocks()
    locks.get("a")
    locks.drop("a")
    assert "a" not in locks._locks


@pytest.mark.asyncio
async def test_reset_clears_all() -> None:
    locks = SessionLocks()
    locks.get("a")
    locks.get("b")
    locks.reset()
    assert locks._locks == {}
