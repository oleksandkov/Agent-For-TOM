"""Per-session async lock manager.

Why: messages within one chat session must be appended in the order they
arrive. Two concurrent ``POST /v1/chat`` calls against the same
``session_id`` would otherwise interleave provider streaming + message
INSERTs and corrupt history. Different sessions are independent.

Scope (v0.1): in-process dict of locks. Works for the single-process
``tom serve`` deployment. A multi-process deployment would need a Redis
lock or per-process queue — out of scope here; §12 documents the gap.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


class SessionLocks:
    """Per-session :class:`asyncio.Lock` registry."""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._registry_lock = asyncio.Lock()

    def has_lock(self, session_id: str) -> bool:
        lock = self._locks.get(session_id)
        return lock is not None and lock.locked()

    def get(self, session_id: str) -> asyncio.Lock:
        """Return the lock for ``session_id``, creating one if needed.

        The registry protects against two coroutines racing to install
        the very first lock for a brand-new session id.
        """
        existing = self._locks.get(session_id)
        if existing is not None:
            return existing
        new_lock = asyncio.Lock()
        self._locks[session_id] = new_lock
        return new_lock

    @asynccontextmanager
    async def acquire(self, session_id: str) -> AsyncIterator[asyncio.Lock]:
        lock = self.get(session_id)
        async with lock:
            yield lock

    def drop(self, session_id: str) -> None:
        """Forget a lock once the session is closed (frees memory)."""
        self._locks.pop(session_id, None)

    def reset(self) -> None:
        """Drop every lock. Tests-only."""
        self._locks.clear()


__all__: list[str] = ["SessionLocks"]
