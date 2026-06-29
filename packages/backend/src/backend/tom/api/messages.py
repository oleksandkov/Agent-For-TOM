"""`GET /v1/sessions/{id}/messages` (Section 6).

Returns the message history for one session, oldest-first.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from backend.tom.db.models import MessageORM, SessionORM
from backend.tom.db.session import SessionLocal

router = APIRouter(prefix="/v1/sessions", tags=["sessions"])


def _emit_message(row: MessageORM) -> dict[str, Any]:
    return {
        "id": row.id,
        "session_id": row.session_id,
        "role": row.role,
        "content": row.content,
        "tokens": int(row.tokens or 0),
        "tool_calls": row.tool_calls,
        "created_at": row.created_at.isoformat(),
    }


@router.get("/{session_id}/messages")
def list_messages(session_id: str, limit: int = 200, offset: int = 0) -> dict[str, Any]:
    if limit < 1 or limit > 1000:
        raise HTTPException(status_code=400, detail="limit_out_of_range")
    if offset < 0:
        raise HTTPException(status_code=400, detail="offset_negative")
    s = SessionLocal()
    try:
        if s.get(SessionORM, session_id) is None:
            raise HTTPException(status_code=404, detail="session_not_found")
        rows = (
            s.execute(
                select(MessageORM)
                .where(MessageORM.session_id == session_id)
                .order_by(MessageORM.created_at.asc())
                .limit(limit)
                .offset(offset)
            )
            .scalars()
            .all()
        )
        items = [_emit_message(r) for r in rows]
        return {
            "session_id": session_id,
            "count": len(items),
            "limit": limit,
            "offset": offset,
            "items": items,
        }
    finally:
        s.close()


__all__: list[str] = ["router"]
