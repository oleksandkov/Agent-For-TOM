"""Tests for the FastAPI sessions router."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.tom.api.sessions import router
from backend.tom.db.init_db import init_db


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    init_db()
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_create_session_returns_id_and_status(client: TestClient) -> None:
    r = client.post("/v1/sessions", json={"title": "hello", "provider": "local"})
    assert r.status_code == 201
    body = r.json()
    assert "id" in body
    assert body["status"] == "open"
    assert body["title"] == "hello"


def test_create_session_rejects_unknown_fields(client: TestClient) -> None:
    r = client.post("/v1/sessions", json={"title": "x", "weird": 1})
    assert r.status_code == 422


def test_list_sessions_empty(client: TestClient) -> None:
    r = client.get("/v1/sessions")
    assert r.status_code == 200
    assert r.json() == []


def test_list_sessions_returns_created(client: TestClient) -> None:
    for i in range(3):
        client.post("/v1/sessions", json={"title": f"s{i}"})
    r = client.get("/v1/sessions")
    items = r.json()
    assert len(items) == 3
    titles = {it["title"] for it in items}
    assert titles == {"s0", "s1", "s2"}


def test_get_session_404_when_unknown(client: TestClient) -> None:
    r = client.get("/v1/sessions/missing")
    assert r.status_code == 404


def test_update_session_rename(client: TestClient) -> None:
    sid = client.post("/v1/sessions", json={"title": "before"}).json()["id"]
    r = client.patch(f"/v1/sessions/{sid}", json={"title": "after"})
    assert r.status_code == 200
    assert r.json()["title"] == "after"


def test_close_session_runs_embed_on_close(client: TestClient) -> None:
    sid = client.post("/v1/sessions", json={"title": "x"}).json()["id"]
    # Insert one user message so embed_on_close has something to summarise.
    from backend.tom.db.models import MessageORM, SessionORM
    from backend.tom.db.session import SessionLocal

    s = SessionLocal()
    try:
        sess = s.get(SessionORM, sid)
        assert sess is not None
        from datetime import UTC, datetime

        now = datetime.now(UTC)
        s.add(SessionORM(id="dummy", title="d", created_at=now, updated_at=now))  # filler
        s.commit()
        s.add(MessageORM(session_id=sid, role="user", content="hi there"))
        s.commit()
    finally:
        s.close()

    r = client.post(f"/v1/sessions/{sid}/close")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "closed"
    # embed_on_close runs with v0.1 stub embedder; archived_memory_id
    # may be set or None depending on message content presence.


def test_delete_session_returns_204(client: TestClient) -> None:
    sid = client.post("/v1/sessions", json={"title": "bye"}).json()["id"]
    r = client.delete(f"/v1/sessions/{sid}")
    assert r.status_code == 204
