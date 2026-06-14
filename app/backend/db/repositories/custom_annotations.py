"""app/backend/db/repositories/custom_annotations.py

Per-template annotations created by the custom-template builder.
Each row stores a JSON array of annotation objects; the shape of
those objects is defined in ``docs/database.md`` §
``custom_template_annotations``.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from .base import BaseRepository


def _new_id() -> str:
    return str(uuid.uuid4())


class CustomAnnotationsRepository(BaseRepository):
    """CRUD for the custom_template_annotations table."""

    def get(self, annotation_id: str) -> dict[str, Any] | None:
        row = self.row_to_dict(
            self._fetchone(
                "SELECT * FROM custom_template_annotations WHERE id = ?",
                (annotation_id,),
            )
        )
        if row is None:
            return None
        try:
            row["annotations"] = json.loads(row.get("annotations") or "[]")
        except (TypeError, ValueError):
            row["annotations"] = []
        return row

    def list_for_template(self, template_id: str) -> list[dict[str, Any]]:
        rows = self.rows_to_dicts(
            self._fetchall(
                "SELECT * FROM custom_template_annotations WHERE template_id = ?",
                (template_id,),
            )
        )
        for r in rows:
            try:
                r["annotations"] = json.loads(r.get("annotations") or "[]")
            except (TypeError, ValueError):
                r["annotations"] = []
        return rows

    def create(
        self,
        *,
        template_id: str,
        source_file_id: str,
        annotations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        new_id = _new_id()
        self._execute(
            """INSERT INTO custom_template_annotations
               (id, template_id, source_file_id, annotations, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (
                new_id, template_id, source_file_id,
                json.dumps(annotations, ensure_ascii=False, default=str),
                self.now(),
            ),
        )
        return self.get(new_id) or {"id": new_id, "template_id": template_id}

    def update_annotations(self, annotation_id: str, annotations: list[dict[str, Any]]) -> None:
        self._execute(
            "UPDATE custom_template_annotations SET annotations = ? WHERE id = ?",
            (json.dumps(annotations, ensure_ascii=False, default=str), annotation_id),
        )

    def delete(self, annotation_id: str) -> None:
        self._execute(
            "DELETE FROM custom_template_annotations WHERE id = ?",
            (annotation_id,),
        )


__all__ = ["CustomAnnotationsRepository"]
