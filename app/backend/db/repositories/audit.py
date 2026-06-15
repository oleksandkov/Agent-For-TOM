"""app/backend/db/repositories/audit.py

Append-only audit log. Used to answer questions like
"who deleted session s3 at 14:28?" after the fact.
"""
from __future__ import annotations

import json
from typing import Any

from .base import BaseRepository


class AuditLogRepository(BaseRepository):
    """Append-only audit log."""

    def log(
        self,
        *,
        actor: str,
        action: str,
        target_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        if actor not in {"ui", "pipeline", "cli", "system"}:
            raise ValueError(f"invalid actor: {actor!r}")
        self._execute(
            """INSERT INTO audit_log (at, actor, action, target_id, details)
               VALUES (?, ?, ?, ?, ?)""",
            (
                self.now(), actor, action, target_id,
                json.dumps(details, ensure_ascii=False, default=str) if details else None,
            ),
        )

    def list_recent(self, limit: int = 200) -> list[dict[str, Any]]:
        rows = self.rows_to_dicts(
            self._fetchall(
                "SELECT * FROM audit_log ORDER BY at DESC LIMIT ?", (limit,)
            )
        )
        for r in rows:
            try:
                r["details"] = json.loads(r.get("details") or "null")
            except (TypeError, ValueError):
                r["details"] = None
        return rows


__all__ = ["AuditLogRepository"]
