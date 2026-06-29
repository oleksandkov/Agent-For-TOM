"""Tests for :mod:`backend.tom.mcp_bridge.loader`."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.tom.db.init_db import init_db
from backend.tom.db.models import SkillORM, SkillStatus
from backend.tom.db.session import SessionLocal
from backend.tom.mcp_bridge import loader as loader_module
from backend.tom.mcp_bridge.loader import (
    discover_builtin,
    discover_generated,
    is_duplicate_name,
    load_all,
    write_builtin_manifest,
)
from backend.tom.mcp_bridge.manifest import McpServerManifest


def _make_manifest(name: str) -> McpServerManifest:
    return McpServerManifest(
        name=name,
        version="0.1.0",
        description=f"{name} test server",
        entrypoint=loader_module.load_manifest.__globals__["EntrypointModel"](
            command="python", args=["-m", f"{name}.server"]
        ),
        capabilities=["tools"],
    )


@pytest.fixture
def tmp_skills(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point skills_builtin_dir / skills_generated_dir at tmp_path."""
    builtin = tmp_path / "builtin"
    generated = tmp_path / "generated"
    builtin.mkdir()
    generated.mkdir()
    monkeypatch.setattr(loader_module, "skills_builtin_dir", lambda: builtin)
    monkeypatch.setattr(loader_module, "skills_generated_dir", lambda: generated)
    return tmp_path


def test_discover_builtin_finds_manifests(
    tmp_skills: Path,
) -> None:
    write_builtin_manifest(
        tmp_skills / "builtin",
        _make_manifest("tom-docx"),
    )
    write_builtin_manifest(
        tmp_skills / "builtin",
        _make_manifest("tom-fmt"),
    )
    found = list(discover_builtin())
    names = [p.parent.name for p in found]
    assert set(names) == {"tom-docx", "tom-fmt"}


def test_discover_generated_filters_active_only(
    tmp_skills: Path,
) -> None:
    init_db()
    generated = tmp_skills / "generated"

    # Two generated skills in folders matching their skill_id
    write_builtin_manifest(
        generated,  # write into generated/ root; helper mkdirs by manifest.name
        _make_manifest("active-id"),
    )
    write_builtin_manifest(
        generated,
        _make_manifest("draft-id"),
    )

    # DB rows
    s = SessionLocal()
    try:
        s.add(
            SkillORM(
                id="active-id",
                name="active-id",
                version="0.1.0",
                manifest={},
                source="generated",
                status=SkillStatus.ACTIVE,
            )
        )
        s.add(
            SkillORM(
                id="draft-id",
                name="draft-id",
                version="0.1.0",
                manifest={},
                source="generated",
                status=SkillStatus.DRAFT,
            )
        )
        s.commit()
    finally:
        s.close()

    discovered = discover_generated(active_only=True)
    pairs = {p.parent.name: sid for p, sid in discovered}
    assert pairs == {"active-id": "active-id"}


def test_load_all_returns_only_active_generated(tmp_skills: Path) -> None:
    init_db()
    builtin = tmp_skills / "builtin"
    generated = tmp_skills / "generated"
    write_builtin_manifest(builtin, _make_manifest("builtin-x"))
    write_builtin_manifest(generated, _make_manifest("g-active"))
    s = SessionLocal()
    try:
        s.add(
            SkillORM(
                id="g-active",
                name="g-active",
                version="0.1.0",
                manifest={},
                source="generated",
                status=SkillStatus.ACTIVE,
            )
        )
        s.commit()
    finally:
        s.close()

    results = load_all()
    names = [r.manifest.name for r in results]
    assert "builtin-x" in names
    assert "g-active" in names


def test_is_duplicate_name_flags_clashes(tmp_skills: Path) -> None:
    init_db()
    builtin = tmp_skills / "builtin"
    generated = tmp_skills / "generated"
    write_builtin_manifest(builtin, _make_manifest("clash"))
    write_builtin_manifest(generated, _make_manifest("clash"))

    # Seed generated/<id>=<manifest-name> as ACTIVE so ``load_all``
    # actually discovers both copies.
    s = SessionLocal()
    try:
        s.add(
            SkillORM(
                id="clash",
                name="clash",
                version="0.1.0",
                manifest={},
                source="generated",
                status=SkillStatus.ACTIVE,
            )
        )
        s.commit()
    finally:
        s.close()

    discovered = load_all()
    dups = is_duplicate_name(discovered)
    assert "clash" in dups
    assert len(dups["clash"]) == 2


def test_orphan_generated_folder_is_skipped(tmp_skills: Path) -> None:
    init_db()
    # A folder without a matching DB row → not discovered
    generated = tmp_skills / "generated"
    write_builtin_manifest(generated, _make_manifest("orphan"))
    # Don't seed DB
    discovered = discover_generated(active_only=True)
    assert discovered == []
