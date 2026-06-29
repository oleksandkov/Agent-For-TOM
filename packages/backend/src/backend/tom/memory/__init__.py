"""TOM memory subsystem.

Backend-imp.md §4 — three tiers (core / archival / recall) with a
single :class:`TomMemory` facade. The FastAPI router lives in
:mod:`backend.tom.memory.api`; the embed-on-close hook lives in
:mod:`backend.tom.memory.embed_on_close`.
"""

from __future__ import annotations

from typing import Any

from backend.tom.memory.types import (
    EMBEDDING_DIM,
    ArchivalHit,
    CoreMemory,
    CoreMemoryBlock,
    CoreMemoryPatch,
    MemoryRecord,
    RecallMessage,
    Tier,
    default_core_memory,
)

__all__: list[str] = [
    "EMBEDDING_DIM",
    "ArchivalHit",
    "CoreMemory",
    "CoreMemoryBlock",
    "CoreMemoryPatch",
    "MemoryRecord",
    "RecallMessage",
    "Tier",
    "TomMemory",
    "default_core_memory",
]


def __getattr__(name: str) -> Any:  # lazy import for the facade
    if name == "TomMemory":
        from backend.tom.memory.tom_memory import TomMemory

        return TomMemory
    raise AttributeError(name)
