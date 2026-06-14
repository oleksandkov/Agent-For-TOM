"""app/backend/db/repositories/instructions.py

Versioned instructions. The semantics are spelled out in
``docs/database.md``:

  * One row per (template_id, type) can be active (``is_active=1``)
    at a time — enforced by a partial unique index in 003_indexes.sql.
  * Saving a new version of an instruction flips the old row to
    ``is_active=0`` and inserts a new row with ``is_active=1``.
"""
from __future__ import annotations

import hashlib
import uuid
from typing import Any, Iterable

from .base import BaseRepository


def _new_id() -> str:
    return str(uuid.uuid4())


def _content_hash(content: str) -> str:
    return hashlib.sha256((content or "").encode("utf-8")).hexdigest()


class InstructionRepository(BaseRepository):
    """Versioned instructions."""

    def get(self, instruction_id: str) -> dict[str, Any] | None:
        return self.row_to_dict(
            self._fetchone("SELECT * FROM instructions WHERE id = ?", (instruction_id,))
        )

    def get_active(
        self,
        type_: str,
        *,
        template_id: str | None = None,
    ) -> dict[str, Any] | None:
        if template_id is None:
            return self.row_to_dict(
                self._fetchone(
                    "SELECT * FROM instructions "
                    "WHERE type = ? AND template_id IS NULL AND is_active = 1",
                    (type_,),
                )
            )
        return self.row_to_dict(
            self._fetchone(
                "SELECT * FROM instructions "
                "WHERE type = ? AND template_id = ? AND is_active = 1",
                (type_, template_id),
            )
        )

    def list_active(self) -> list[dict[str, Any]]:
        return self.rows_to_dicts(
            self._fetchall(
                "SELECT * FROM instructions WHERE is_active = 1 ORDER BY type, template_id"
            )
        )

    def list_versions(
        self,
        type_: str,
        *,
        template_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if template_id is None:
            return self.rows_to_dicts(
                self._fetchall(
                    "SELECT * FROM instructions "
                    "WHERE type = ? AND template_id IS NULL "
                    "ORDER BY version DESC",
                    (type_,),
                )
            )
        return self.rows_to_dicts(
            self._fetchall(
                "SELECT * FROM instructions "
                "WHERE type = ? AND template_id = ? "
                "ORDER BY version DESC",
                (type_, template_id),
            )
        )

    def save_new_version(
        self,
        *,
        type_: str,
        content: str,
        content_path: str | None = None,
        template_id: str | None = None,
    ) -> dict[str, Any]:
        """Save `content` as the new active version. Deactivates the
        previous active row (if any) and inserts a new one with an
        incremented version number.
        """
        if type_ not in {"global", "special", "user_created"}:
            raise ValueError(f"unknown instruction type: {type_!r}")
        # Find the next version number.
        existing = self.list_versions(type_, template_id=template_id)
        next_version = 1 if not existing else int(existing[0]["version"]) + 1
        # Deactivate the current active row.
        if template_id is None:
            self._execute(
                "UPDATE instructions SET is_active = 0 "
                "WHERE type = ? AND template_id IS NULL AND is_active = 1",
                (type_,),
            )
        else:
            self._execute(
                "UPDATE instructions SET is_active = 0 "
                "WHERE type = ? AND template_id = ? AND is_active = 1",
                (type_, template_id),
            )
        # Insert the new version.
        new_id = _new_id()
        now = self.now()
        self._execute(
            """INSERT INTO instructions
               (id, template_id, type, content_hash, content_path,
                content, is_active, version, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)""",
            (
                new_id, template_id, type_,
                _content_hash(content), content_path, content,
                next_version, now,
            ),
        )
        return self.get(new_id) or {"id": new_id, "type": type_}

    def deactivate(self, instruction_id: str) -> None:
        self._execute(
            "UPDATE instructions SET is_active = 0 WHERE id = ?",
            (instruction_id,),
        )


__all__ = ["InstructionRepository"]
