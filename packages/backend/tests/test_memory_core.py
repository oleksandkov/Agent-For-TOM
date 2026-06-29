"""Tests for :mod:`backend.tom.memory.core`."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.tom.db.init_db import init_db
from backend.tom.db.paths import core_memory_file
from backend.tom.memory.core import (
    CoreMemoryConflict,
    read_core,
    write_core,
)
from backend.tom.memory.types import (
    CoreMemoryBlock,
    CoreMemoryPatch,
)


def _seed_default_block() -> CoreMemoryBlock:
    return CoreMemoryBlock(label="persona", text="v0")


def test_read_core_returns_default_on_empty_db(virtual_keyring: object, tmp_path: Path) -> None:
    init_db()
    core = read_core()
    assert core.version == 0
    assert core.blocks == [] or core.blocks[0].label == "persona"
    assert core.facts == []


def test_write_core_bumps_version(virtual_keyring: object, tmp_path: Path) -> None:
    init_db()
    initial = read_core()
    next_value = write_core(
        CoreMemoryPatch(
            expected_version=initial.version,
            add_facts=["user prefers terse replies"],
        )
    )
    assert next_value.version == initial.version + 1
    assert "user prefers terse replies" in next_value.facts


def test_write_core_replaces_blocks(virtual_keyring: object, tmp_path: Path) -> None:
    init_db()
    initial = read_core()
    next_value = write_core(
        CoreMemoryPatch(
            expected_version=initial.version,
            set_blocks=[_seed_default_block(), CoreMemoryBlock(label="human", text="dev")],
        )
    )
    labels = {b.label for b in next_value.blocks}
    assert {"persona", "human"}.issubset(labels)


def test_write_core_remove_facts_by_index(virtual_keyring: object, tmp_path: Path) -> None:
    init_db()
    initial = read_core()
    after_add = write_core(
        CoreMemoryPatch(
            expected_version=initial.version,
            add_facts=["a", "b", "c"],
        )
    )
    after_remove = write_core(
        CoreMemoryPatch(
            expected_version=after_add.version,
            remove_fact_indices=[1],
        )
    )
    assert after_remove.facts == ["a", "c"]


def test_write_core_conflict_on_stale_version(virtual_keyring: object, tmp_path: Path) -> None:
    init_db()
    initial = read_core()
    write_core(CoreMemoryPatch(expected_version=initial.version, add_facts=["x"]))
    with pytest.raises(CoreMemoryConflict):
        write_core(CoreMemoryPatch(expected_version=initial.version, add_facts=["y"]))


def test_core_memory_json_mirror_is_written(virtual_keyring: object, tmp_path: Path) -> None:
    init_db()
    initial = read_core()
    write_core(CoreMemoryPatch(expected_version=initial.version, add_facts=["user is dev"]))
    assert core_memory_file().exists()
    text = core_memory_file().read_text(encoding="utf-8")
    assert "user is dev" in text
    assert "version" in text


def test_core_memory_json_restore_on_empty_db(virtual_keyring: object, tmp_path: Path) -> None:
    """If DB has no rows yet but a JSON mirror exists, the mirror wins."""
    init_db()
    # Pre-write a JSON mirror from before the DB was ever populated.
    mirror_path = core_memory_file()
    mirror_path.write_text(
        '{"blocks": [{"label": "persona", "text": "from-json"}],'
        ' "facts": ["mirror-only-fact"], "version": 7, "updated_at": "2026-01-01T00:00:00+00:00"}',
        encoding="utf-8",
    )
    # Force fresh DB rows (none exist anyway, so just call read_core):
    core = read_core()
    assert core.version == 7
    assert "mirror-only-fact" in core.facts
