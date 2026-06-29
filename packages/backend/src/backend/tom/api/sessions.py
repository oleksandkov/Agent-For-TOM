"""`/v1/sessions*` REST endpoints (Section 6).

Contract (frozen here):

- ``POST   /v1/sessions``              → open a new session
- ``GET    /v1/sessions``              → list sessions
- ``GET    /v1/sessions/{id}``         → get one session
- ``PATCH  /v1/sessions/{id}``         → rename via ``{"title": ...}``
- ``DELETE /v1/sessions/{id}``         → delete (force-close path)
- ``POST   /v1/sessions/{id}/close``   → graceful close (triggers
  :func:`backend.tom.memory.embed_on_close.embed_on_close`)
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from backend.tom.db.audit import write_audit_log
from backend.tom.db.models import SessionORM, SessionStatus
from backend.tom.db.session import SessionLocal
from backend.tom.memory.embed_on_close import embed_on_close

router = APIRouter(prefix="/v1/sessions", tags=["sessions"])


def _emit_session(sess: SessionORM) -> dict[str, Any]:
    return {
        "id": sess.id,
        "title": sess.title,
        "provider": sess.provider,
        "model": sess.model,
        "status": sess.status.value if hasattr(sess.status, "value") else str(sess.status),
        "total_tokens": int(sess.total_tokens or 0),
        "created_at": sess.created_at.isoformat(),
        "updated_at": sess.updated_at.isoformat(),
    }


class CreateSession(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = ""
    provider: str | None = None
    model: str | None = None


class UpdateSession(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    provider: str | None = None
    model: str | None = None


@router.post("", response_model=dict, status_code=status.HTTP_201_CREATED)
def create_session(body: CreateSession) -> dict[str, Any]:
    import uuid

    s = SessionLocal()
    try:
        now = datetime.now(UTC)
        sess = SessionORM(
            id=str(uuid.uuid4()),
            title=body.title or "",
            provider=body.provider or "",
            model=body.model or "",
            status=SessionStatus.OPEN,
            total_tokens=0,
            created_at=now,
            updated_at=now,
        )
        s.add(sess)
        s.commit()
        s.refresh(sess)
        write_audit_log(
            action="session.create",
            target_type="session",
            target_id=sess.id,
            payload={"provider": sess.provider, "model": sess.model},
        )
        return _emit_session(sess)
    finally:
        s.close()


@router.get("")
def list_sessions() -> list[dict[str, Any]]:
    s = SessionLocal()
    try:
        rows = s.execute(select(SessionORM).order_by(SessionORM.updated_at.desc())).scalars().all()
        return [_emit_session(r) for r in rows]
    finally:
        s.close()


@router.get("/{session_id}")
def get_session(session_id: str) -> dict[str, Any]:
    s = SessionLocal()
    try:
        sess = s.get(SessionORM, session_id)
    finally:
        s.close()
    if sess is None:
        raise HTTPException(status_code=404, detail="session_not_found")
    return _emit_session(sess)


@router.patch("/{session_id}")
def update_session(session_id: str, body: UpdateSession) -> dict[str, Any]:
    s = SessionLocal()
    try:
        sess = s.get(SessionORM, session_id)
        if sess is None:
            raise HTTPException(status_code=404, detail="session_not_found")
        for field_name in ("title", "provider", "model"):
            value = getattr(body, field_name)
            if value is not None:
                setattr(sess, field_name, value)
        sess.updated_at = datetime.now(UTC)
        s.commit()
        s.refresh(sess)
        write_audit_log(
            action="session.update",
            target_type="session",
            target_id=sess.id,
            payload={
                "fields": [
                    k for k in ("title", "provider", "model") if getattr(body, k) is not None
                ]
            },
        )
        return _emit_session(sess)
    finally:
        s.close()


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_session(session_id: str) -> None:
    s = SessionLocal()
    try:
        sess = s.get(SessionORM, session_id)
        if sess is not None:
            s.delete(sess)
            s.commit()
            write_audit_log(action="session.delete", target_type="session", target_id=session_id)
    finally:
        s.close()


@router.post("/{session_id}/close")
def close_session(session_id: str) -> dict[str, Any]:
    s = SessionLocal()
    try:
        sess = s.get(SessionORM, session_id)
        if sess is None:
            raise HTTPException(status_code=404, detail="session_not_found")
        sess.status = SessionStatus.CLOSED
        sess.updated_at = datetime.now(UTC)
        s.commit()
        s.refresh(sess)
    finally:
        s.close()
    # Trigger embed-on-close *after* marking the row closed so the
    # hook reads messages against an accurate state. ``embed_on_close``
    # is synchronous; FastAPI runs sync handlers in a thread pool, so
    # calling it directly here doesn't block the event loop.
    archived = embed_on_close(session_id)
    archived_id: str | None = archived.id if archived is not None else None
    write_audit_log(
        action="session.close",
        target_type="session",
        target_id=session_id,
        payload={"archived_memory_id": archived_id} if archived_id else {},
    )
    return {**_emit_session(sess), "archived_memory_id": archived_id}


__all__: list[str] = ["router"]
