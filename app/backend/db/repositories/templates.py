"""app/backend/db/repositories/templates.py

CRUD for the ``templates`` table.

Templates can be:
  * built-in (id matches a hardcoded UUID, ``is_builtin=1``)
  * user-created (id is a fresh UUID, ``is_builtin=0``)
"""
from __future__ import annotations

import uuid
from typing import Any

from .base import BaseRepository


def _new_id() -> str:
    return str(uuid.uuid4())


class TemplateRepository(BaseRepository):
    """CRUD for the templates table."""

    def get(self, template_id: str) -> dict[str, Any] | None:
        return self.row_to_dict(
            self._fetchone("SELECT * FROM templates WHERE id = ?", (template_id,))
        )

    def get_by_name(self, name: str) -> dict[str, Any] | None:
        return self.row_to_dict(
            self._fetchone("SELECT * FROM templates WHERE name = ?", (name,))
        )

    def list_all(self) -> list[dict[str, Any]]:
        return self.rows_to_dicts(
            self._fetchall("SELECT * FROM templates ORDER BY name")
        )

    def list_builtin(self) -> list[dict[str, Any]]:
        return self.rows_to_dicts(
            self._fetchall(
                "SELECT * FROM templates WHERE is_builtin = 1 ORDER BY name"
            )
        )

    def create(
        self,
        name: str,
        *,
        display_name: str | None = None,
        description: str | None = None,
        script_path: str | None = None,
        instructions_path: str | None = None,
        is_builtin: bool = False,
        has_instructions: bool = True,
        supports_images: bool = True,
        placeholder_schema: dict | None = None,
        gap_schema: dict | None = None,
        source_file_id: str | None = None,
        template_id: str | None = None,
    ) -> dict[str, Any]:
        tid = template_id or _new_id()
        payload = {
            "id": tid,
            "name": name,
            "display_name": display_name,
            "description": description,
            "script_path": script_path,
            "instructions_path": instructions_path,
            "is_builtin": 1 if is_builtin else 0,
            "has_instructions": 1 if has_instructions else 0,
            "supports_images": 1 if supports_images else 0,
            "placeholder_schema": placeholder_schema,
            "gap_schema": gap_schema,
            "source_file_id": source_file_id,
        }
        payload = self.jsonify(payload)
        payload["created_at"] = self.now()
        payload["updated_at"] = payload["created_at"]
        self._execute(
            """INSERT INTO templates
               (id, name, display_name, description, script_path,
                instructions_path, is_builtin, has_instructions,
                supports_images, placeholder_schema, gap_schema,
                source_file_id, created_at, updated_at)
               VALUES
               (:id, :name, :display_name, :description, :script_path,
                :instructions_path, :is_builtin, :has_instructions,
                :supports_images, :placeholder_schema, :gap_schema,
                :source_file_id, :created_at, :updated_at)""",
            payload,
        )
        return self.get(tid) or {"id": tid, "name": name}

    def update(self, template_id: str, **fields: Any) -> None:
        """Patch any subset of columns on an existing template."""
        if not fields:
            return
        fields["updated_at"] = self.now()
        fields = self.jsonify(fields)
        set_clause = ", ".join(f"{k} = :{k}" for k in fields)
        params = dict(fields, id=template_id)
        self._execute(
            f"UPDATE templates SET {set_clause} WHERE id = :id",
            params,
        )

    def delete(self, template_id: str) -> None:
        self._execute("DELETE FROM templates WHERE id = ?", (template_id,))


__all__ = ["TemplateRepository"]
