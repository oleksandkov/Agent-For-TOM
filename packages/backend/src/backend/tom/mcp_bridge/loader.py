"""MCP server manifest loader (Section 7).

Scans the data dir for ``manifest.json`` files in two trees:

- ``data_dir/skills/builtin/<name>/manifest.json`` — packaged MCP servers
- ``data_dir/skills/generated/<uuid>/manifest.json`` — only those whose
  matching ``skill.status`` in the DB is ``ACTIVE`` (matching the plan:
  "the latter only ``status='active'``")

Returns a list of :class:`McpServerManifest`. Each loader call is
idempotent — re-running just refreshes.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from backend.tom.db.models import SkillORM, SkillStatus
from backend.tom.db.paths import skills_builtin_dir, skills_generated_dir
from backend.tom.db.session import SessionLocal
from backend.tom.mcp_bridge.manifest import (
    ManifestError,
    McpServerManifest,
    load_manifest,
)


@dataclass
class _DiscoveredManifest:
    manifest: McpServerManifest
    manifest_path: Path
    skill_id: str | None  # present if generated-and-active
    origin: str  # "builtin" | "generated"


def discover_builtin() -> Iterable[Path]:
    """Yield every ``manifest.json`` under ``data_dir/skills/builtin/*``."""
    root = skills_builtin_dir()
    if not root.exists():
        return
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        manifest = child / "manifest.json"
        if manifest.exists():
            yield manifest


def discover_generated(active_only: bool = True) -> list[tuple[Path, str | None]]:
    """Yield ``(manifest.json, skill_id)`` pairs from ``generated/``.

    ``skill_id`` is the :class:`SkillORM.id` so the dispatcher can re-use
    catalog metadata (last_invocation, etc.). When ``active_only=True``
    the SQLCipher table filters out non-ACTIVE skills.
    """
    root = skills_generated_dir()
    if not root.exists():
        return []
    all_metas: list[tuple[Path, str | None]] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        manifest = child / "manifest.json"
        if not manifest.exists():
            continue
        candidate_id = child.name  # generated/<skill_id>/manifest.json
        all_metas.append((manifest, candidate_id))
    if not active_only:
        return all_metas
    valid_ids = _active_skill_ids()
    return [(p, sid) for p, sid in all_metas if sid in valid_ids]


def _active_skill_ids() -> set[str]:
    s = SessionLocal()
    try:
        rows = s.execute(
            select(SkillORM.id, SkillORM.status).where(SkillORM.source == "generated")
        ).all()
    finally:
        s.close()
    return {sid for sid, status in rows if status is SkillStatus.ACTIVE}


def load_all() -> list[_DiscoveredManifest]:
    """Return every loaded manifest from both trees."""
    results: list[_DiscoveredManifest] = []
    for path in discover_builtin():
        manifest = load_manifest(path)
        results.append(
            _DiscoveredManifest(
                manifest=manifest,
                manifest_path=path,
                skill_id=None,
                origin="builtin",
            )
        )
    for path, skill_id in discover_generated(active_only=True):
        manifest = load_manifest(path)
        results.append(
            _DiscoveredManifest(
                manifest=manifest,
                manifest_path=path,
                skill_id=skill_id,
                origin="generated",
            )
        )
    return results


def is_duplicate_name(
    discovered: Iterable[_DiscoveredManifest],
) -> dict[str, list[_DiscoveredManifest]]:
    """Group discoveries by name. The dispatcher fails on duplicates."""
    groups: dict[str, list[_DiscoveredManifest]] = {}
    for entry in discovered:
        groups.setdefault(entry.manifest.name, []).append(entry)
    return {name: entries for name, entries in groups.items() if len(entries) > 1}


def load_manifest_from_disk(path: str | Path) -> McpServerManifest:
    """Public alias for :func:`backend.tom.mcp_bridge.manifest.load_manifest`."""
    return load_manifest(path)


__all__: list[str] = [
    "ManifestError",
    "discover_builtin",
    "discover_generated",
    "is_duplicate_name",
    "load_all",
    "load_manifest_from_disk",
]


def write_builtin_manifest(builtin_root: Path, manifest: McpServerManifest) -> Path:
    """Helper for tests and the §8 scaffolder — materialise a manifest on disk.

    Returns the path written. Creates ``<builtin_root>/<name>/manifest.json``.
    """
    server_dir = builtin_root / manifest.name
    server_dir.mkdir(parents=True, exist_ok=True)
    out = server_dir / "manifest.json"
    out.write_text(
        json.dumps(manifest.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return out
