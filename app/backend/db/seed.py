"""app/backend/db/seed.py

Idempotent initial data. Runs once after the schema is applied.

The SQL-level seed (built-in templates + default app_settings) lives
in ``app/db/schema/004_seeds.sql`` and is executed by the migration
runner. This Python module adds the things that SQL cannot do cleanly:

  * Reads the content of ``app/instructions/global_instructions.md``
    and inserts it as the active global instruction.
  * Inserts versioned `special` instructions for the built-in
    templates (lab1, lab2) by reading
    ``app/instructions/template-ins/labN_fill.md``.
  * Inserts a `user_style` row marked ``is_empty=1`` so the partial
    unique index has a stable target.
"""
from __future__ import annotations

import logging
from pathlib import Path

from .connection import Database
from .repositories.instructions import InstructionRepository
from .repositories.user_style import UserStyleRepository

log = logging.getLogger("agent_for_tom.db.seed")


INSTRUCTIONS_DIR = Path(__file__).resolve().parents[2] / "instructions"
GLOBAL_INSTRUCTIONS_FILE = INSTRUCTIONS_DIR / "global_instructions.md"
TEMPLATE_INS_DIR = INSTRUCTIONS_DIR / "template-ins"

BUILTIN_TEMPLATES = {
    "lab1": "00000000-0000-0000-0000-000000000001",
    "lab2": "00000000-0000-0000-0000-000000000002",
}


def seed_initial_data(db: Database) -> None:
    """Idempotently insert initial content rows. Safe to call repeatedly."""
    _seed_global_instruction(db)
    _seed_special_instructions(db)
    _seed_user_style(db)
    log.info("seed complete")


def _seed_global_instruction(db: Database) -> None:
    if not GLOBAL_INSTRUCTIONS_FILE.is_file():
        log.warning("global instructions file missing: %s", GLOBAL_INSTRUCTIONS_FILE)
        return
    content = GLOBAL_INSTRUCTIONS_FILE.read_text(encoding="utf-8")
    repo = InstructionRepository(db)
    if repo.get_active("global") is not None:
        return
    repo.save_new_version(
        type_="global",
        content=content,
        content_path=str(GLOBAL_INSTRUCTIONS_FILE.relative_to(INSTRUCTIONS_DIR.parent)),
    )
    log.info("seeded global instruction")


def _seed_special_instructions(db: Database) -> None:
    repo = InstructionRepository(db)
    for name, template_id in BUILTIN_TEMPLATES.items():
        md_path = TEMPLATE_INS_DIR / f"{name}_fill.md"
        if not md_path.is_file():
            log.warning("template-instructions file missing: %s", md_path)
            continue
        if repo.get_active("special", template_id=template_id) is not None:
            continue
        content = md_path.read_text(encoding="utf-8")
        try:
            rel = str(md_path.relative_to(INSTRUCTIONS_DIR.parent))
        except ValueError:
            rel = str(md_path)
        repo.save_new_version(
            type_="special",
            content=content,
            content_path=rel,
            template_id=template_id,
        )
        log.info("seeded special instruction for %s", name)


def _seed_user_style(db: Database) -> None:
    repo = UserStyleRepository(db)
    if repo.get_active() is not None:
        return
    repo.save_new_version(content="")
    log.info("seeded empty user_style row")


__all__ = ["seed_initial_data"]
