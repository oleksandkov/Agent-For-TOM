"""Core memory CRUD (DB-canonical, JSON-mirrored).

Two stores are kept in sync:

- SQLCipher-encrypted ``memory_records`` row (tier='core') is the
  **canonical** store. Reads always come from here.
- ``data_dir/core_memory.json`` is the **mirror** that the user can
  edit by hand. It is regenerated on every successful PATCH; the JSON
  file is also re-read on first read of a fresh DB.

Optimistic concurrency: ``CoreMemoryPatch.expected_version`` must
match the current ``CoreMemory.version`` or the request is rejected
with :class:`CoreMemoryConflictError`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import select

from backend.tom.db.models import MemoryRecordORM, MemoryTier
from backend.tom.db.paths import core_memory_file, ensure_dirs
from backend.tom.db.session import SessionLocal
from backend.tom.memory.types import (
    CoreMemory,
    CoreMemoryBlock,
    CoreMemoryPatch,
    default_core_memory,
)


class CoreMemoryConflictError(Exception):
    """Raised when a PATCH's expected_version does not match the stored one."""

    def __init__(self, *, current: CoreMemory, expected: int) -> None:
        super().__init__(f"expected_version={expected} does not match stored={current.version}")
        self.current = current
        self.expected = expected


# Backwards-compatible alias (renamed to satisfy ruff N818; existing call
# sites in tests and API still resolve via the alias).
CoreMemoryConflict = CoreMemoryConflictError


def _load_from_json() -> CoreMemory | None:
    """Read the JSON mirror if it exists and parses cleanly."""
    path = core_memory_file()
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    try:
        return CoreMemory.model_validate(raw)
    except ValueError:
        return None


def _load_latest_from_db() -> CoreMemory | None:
    """Read the newest core-memory row from the SQLCipher DB."""
    s = SessionLocal()
    try:
        row = s.execute(
            select(MemoryRecordORM)
            .where(MemoryRecordORM.tier == MemoryTier.CORE)
            .order_by(MemoryRecordORM.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if row is None:
            return None
        return CoreMemory.model_validate_json(row.content)
    finally:
        s.close()


def _save_to_db(core: CoreMemory) -> None:
    """Append a new core-memory row (full snapshot)."""
    payload = core.model_dump_json()
    s = SessionLocal()
    try:
        s.add(
            MemoryRecordORM(
                tier=MemoryTier.CORE,
                content=payload,
                confidence=1.0,
            )
        )
        s.commit()
    finally:
        s.close()


def _save_to_json(core: CoreMemory) -> None:
    ensure_dirs()
    path = core_memory_file()
    payload = core.model_dump(mode="json", exclude_none=True)
    payload["updated_at"] = core.updated_at.isoformat()
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _apply_patch(current: CoreMemory, patch: CoreMemoryPatch) -> CoreMemory:
    """Mutate a copy of ``current`` according to ``patch``."""
    next_blocks: list[CoreMemoryBlock] = list(current.blocks)
    next_facts: list[str] = list(current.facts)
    if patch.set_blocks is not None:
        next_blocks = list(patch.set_blocks)
    if patch.add_facts:
        next_facts.extend(patch.add_facts)
    if patch.remove_fact_indices:
        keep = sorted(set(range(len(next_facts))) - set(patch.remove_fact_indices))
        next_facts = [next_facts[i] for i in keep]
    return CoreMemory(
        blocks=next_blocks,
        facts=next_facts,
        version=current.version,
        updated_at=datetime.now(UTC),
    )


def read_core() -> CoreMemory:
    """Read the canonical core memory. Falls back to default if DB is empty.

    On first read of a fresh DB we opportunistically re-import the JSON
    mirror (so a hand-edited ``core_memory.json`` survives a reinstall
    of just the DB file).
    """
    db_value = _load_latest_from_db()
    if db_value is not None:
        return db_value
    json_value = _load_from_json()
    if json_value is not None:
        _save_to_db(json_value)
        return json_value
    return default_core_memory()


def write_core(patch: CoreMemoryPatch) -> CoreMemory:
    """Apply ``patch`` with optimistic concurrency.

    Raises :class:`CoreMemoryConflictError` on version mismatch.
    On success the new value is written to DB and to the JSON mirror.
    """
    current = read_core()
    if patch.expected_version != current.version:
        raise CoreMemoryConflictError(current=current, expected=patch.expected_version)
    next_value = _apply_patch(current, patch)
    next_value = next_value.model_copy(update={"version": current.version + 1})
    _save_to_db(next_value)
    _save_to_json(next_value)
    return next_value


__all__: list[str] = [
    "CoreMemoryConflict",
    "read_core",
    "write_core",
]
