"""Public types for the TOM memory subsystem.

Imports are kept dependency-light so these models can be re-used by
the FastAPI router, ORMs, providers, and the desktop colleague without
pulling in ``sqlcipher3`` or ``letta``.

Re-exports the :class:`Tier` enum (mirrors ``backend.tom.db.models.MemoryTier``)
so callers outside the DB layer never need to import ORM types.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

EMBEDDING_DIM = 384


class Tier(StrEnum):
    CORE = "core"
    ARCHIVAL = "archival"
    RECALL = "recall"


class CoreMemoryBlock(BaseModel):
    """One user-editable structured text block (a "section" of the persona/human split)."""

    model_config = ConfigDict(extra="forbid")

    label: str
    text: str


class CoreMemory(BaseModel):
    """Top-level user-editable structured memory.

    Encrypted at rest by being persisted into SQLCipher via the
    ``memory_records`` table; mirrored to ``core_memory.json`` so the
    user can inspect/edit it offline.
    """

    model_config = ConfigDict(extra="forbid")

    blocks: list[CoreMemoryBlock] = Field(default_factory=list)
    facts: list[str] = Field(default_factory=list)
    version: int = 0
    updated_at: datetime


class CoreMemoryPatch(BaseModel):
    """Body of ``PATCH /v1/memory/core``.

    ``expected_version`` enforces optimistic concurrency: backend
    replies ``409 Conflict`` if the stored version has moved on.
    """

    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=0)
    set_blocks: list[CoreMemoryBlock] | None = None
    add_facts: list[str] | None = None
    remove_fact_indices: list[int] | None = None


class MemoryRecord(BaseModel):
    """Public view of a memory row."""

    id: str
    tier: Tier
    content: str
    embedding_dim: int | None = None
    source_session_id: str | None = None
    confidence: float = 1.0
    created_at: datetime
    has_embedding: bool = False


class ArchivalHit(BaseModel):
    """One row in a KNN result, with cosine distance (lower == closer)."""

    record: MemoryRecord
    distance: float


class RecallMessage(BaseModel):
    """A message kept in the per-session recall buffer."""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    role: str
    content: str
    created_at: datetime


def default_core_memory() -> CoreMemory:
    """Return the seed core memory for a fresh DB."""
    from datetime import UTC, datetime

    return CoreMemory(
        blocks=[
            CoreMemoryBlock(
                label="persona",
                text="TOM — local-first personal AI agent",
            ),
        ],
        facts=[],
        version=0,
        updated_at=datetime.now(UTC),
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
    "default_core_memory",
]
