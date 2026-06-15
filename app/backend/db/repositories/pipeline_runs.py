"""app/backend/db/repositories/pipeline_runs.py

Per-stage timing rows for one session. The orchestrator writes one
row per stage; the UI can later render a Gantt-like breakdown.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from .base import BaseRepository


def _new_id() -> str:
    return str(uuid.uuid4())


class PipelineRunsRepository(BaseRepository):
    """CRUD for the pipeline_runs table."""

    def start(
        self,
        session_id: str,
        stage: str,
        *,
        run_id: str | None = None,
    ) -> str:
        """Insert a `started` row and return its id."""
        rid = run_id or _new_id()
        self._execute(
            """INSERT INTO pipeline_runs
               (id, session_id, stage, status, started_at)
               VALUES (?, ?, ?, 'started', ?)""",
            (rid, session_id, stage, self.now()),
        )
        return rid

    def finish(
        self,
        run_id: str,
        *,
        status: str = "ok",
        error_message: str | None = None,
        log_excerpt: str | None = None,
        metrics: dict[str, Any] | None = None,
    ) -> None:
        if status not in {"ok", "warn", "error"}:
            raise ValueError(f"invalid finish status: {status!r}")
        # Compute duration from the started row.
        row = self._fetchone(
            "SELECT started_at FROM pipeline_runs WHERE id = ?", (run_id,)
        )
        if row is None:
            return
        from datetime import datetime
        try:
            started = datetime.fromisoformat(row["started_at"])
            now = datetime.fromisoformat(self.now())
            duration_ms = int((now - started).total_seconds() * 1000)
        except ValueError:
            duration_ms = None
        self._execute(
            """UPDATE pipeline_runs
               SET status = ?, ended_at = ?, duration_ms = ?,
                   error_message = ?, log_excerpt = ?, metrics = ?
               WHERE id = ?""",
            (
                status, self.now(), duration_ms, error_message,
                (log_excerpt or "")[:2048] if log_excerpt else None,
                json.dumps(metrics, ensure_ascii=False, default=str) if metrics else None,
                run_id,
            ),
        )

    def list_for_session(self, session_id: str) -> list[dict[str, Any]]:
        rows = self.rows_to_dicts(
            self._fetchall(
                "SELECT * FROM pipeline_runs WHERE session_id = ? "
                "ORDER BY started_at",
                (session_id,),
            )
        )
        for r in rows:
            try:
                r["metrics"] = json.loads(r.get("metrics") or "{}")
            except (TypeError, ValueError):
                r["metrics"] = {}
        return rows


__all__ = ["PipelineRunsRepository"]
