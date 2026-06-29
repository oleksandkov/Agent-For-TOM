"""Audit log helper.

Used by every state-changing API in §6+ to leave a forensic trail.
Stored in the SQLCipher-encrypted ``audit_log`` table (Section 3).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from backend.tom.db.models import AuditLogORM
from backend.tom.db.session import SessionLocal


def write_audit_log(
    *,
    action: str,
    actor: str = "system",
    target_type: str | None = None,
    target_id: str | None = None,
    payload: dict[str, Any] | None = None,
    ts: datetime | None = None,
) -> int:
    """Append a single audit-log row. Returns the new row id."""
    s = SessionLocal()
    try:
        row = AuditLogORM(
            ts=ts or datetime.now(UTC),
            actor=actor,
            action=action,
            target_type=target_type,
            target_id=target_id,
            payload=payload or {},
        )
        s.add(row)
        s.commit()
        s.refresh(row)
        return int(row.id)
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


__all__: list[str] = ["write_audit_log"]
