"""app/backend/db/repositories/sessions.py

The most complex repository: the ``sessions`` table has many status
transitions and stores both an immutable input snapshot and
mutable result fields. Every public method maps to a single status
update so the orchestrator can call them as the pipeline progresses.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from .base import BaseRepository


def _new_id() -> str:
    return str(uuid.uuid4())


class SessionRepository(BaseRepository):
    """CRUD + status transitions for the sessions table."""

    # ---- read ----------------------------------------------------------

    def get(self, session_id: str) -> dict[str, Any] | None:
        return self.unjsonify(
            self.row_to_dict(self._fetchone("SELECT * FROM sessions WHERE id = ?", (session_id,))),
            json_fields=("input_snapshot", "validation_result", "token_usage"),
        )

    def list_recent(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.rows_to_dicts(
            self._fetchall(
                "SELECT * FROM sessions ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
        )
        return [self.unjsonify(r, ("input_snapshot", "validation_result", "token_usage")) for r in rows]

    def list_by_status(self, status: str, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.rows_to_dicts(
            self._fetchall(
                "SELECT * FROM sessions WHERE status = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            )
        )
        return [self.unjsonify(r, ("input_snapshot", "validation_result", "token_usage")) for r in rows]

    # ---- write ---------------------------------------------------------

    def create(
        self,
        *,
        template_id: str,
        name: str,
        input_snapshot: dict[str, Any],
        session_id: str | None = None,
        output_dir: str | None = None,
    ) -> dict[str, Any]:
        sid = session_id or _new_id()
        now = self.now()
        self._execute(
            """INSERT INTO sessions
               (id, name, template_id, status, input_snapshot, output_dir,
                image_count, created_at)
               VALUES (?, ?, ?, 'draft', ?, ?, 0, ?)""",
            (
                sid, name, template_id,
                json.dumps(input_snapshot, ensure_ascii=False, default=str),
                output_dir, now,
            ),
        )
        return self.get(sid) or {"id": sid, "name": name, "status": "draft"}

    def update_status(
        self,
        session_id: str,
        status: str,
        *,
        error_stage: str | None = None,
        error_message: str | None = None,
    ) -> None:
        """Update ``status`` (and optionally record the error). Also
        sets the matching timestamp column.
        """
        ts_col = {
            "processing": "started_at",
            "completed": "completed_at",
            "cancelled": "cancelled_at",
            "failed": "failed_at",
        }.get(status)
        if ts_col is None:
            self._execute(
                "UPDATE sessions SET status = ?, error_stage = COALESCE(?, error_stage), "
                "error_message = COALESCE(?, error_message) WHERE id = ?",
                (status, error_stage, error_message, session_id),
            )
        else:
            self._execute(
                f"UPDATE sessions SET status = ?, {ts_col} = ?, "
                "error_stage = COALESCE(?, error_stage), "
                "error_message = COALESCE(?, error_message) WHERE id = ?",
                (status, self.now(), error_stage, error_message, session_id),
            )

    def set_completed(
        self,
        session_id: str,
        *,
        duration_ms: int,
        docx_path: str | None = None,
        pdf_path: str | None = None,
        image_count: int = 0,
        token_usage: dict[str, Any] | None = None,
        validation_result: dict[str, Any] | None = None,
    ) -> None:
        self._execute(
            """UPDATE sessions
               SET status = 'completed',
                   completed_at = ?,
                   duration_ms = ?,
                   docx_path = ?,
                   pdf_path = ?,
                   image_count = ?,
                   token_usage = ?,
                   validation_result = ?,
                   error_stage = NULL,
                   error_message = NULL
               WHERE id = ?""",
            (
                self.now(), duration_ms, docx_path, pdf_path,
                image_count,
                json.dumps(token_usage, ensure_ascii=False, default=str) if token_usage else None,
                json.dumps(validation_result, ensure_ascii=False, default=str) if validation_result else None,
                session_id,
            ),
        )

    def set_started(self, session_id: str) -> None:
        self._execute(
            "UPDATE sessions SET status = 'processing', started_at = ? WHERE id = ?",
            (self.now(), session_id),
        )

    def delete(self, session_id: str) -> None:
        # CASCADE removes session_files, pipeline_runs rows.
        self._execute("DELETE FROM sessions WHERE id = ?", (session_id,))


__all__ = ["SessionRepository"]
