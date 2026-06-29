"""Tests for the FastAPI messages router."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.tom.db.init_db import init_db
from backend.tom.db.models import MessageORM, SessionORM
from backend.tom.db.session import SessionLocal


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    init_db()
    app = FastAPI()
    app.include_router(__import__("backend.tom.api.messages", fromlist=["router"]).router)
    return TestClient(app)


def _make_session_with_messages() -> str:
    s = SessionLocal()
    try:
        now = datetime.now(UTC)
        sid = "m-session"
        s.add(SessionORM(id=sid, title="m", created_at=now, updated_at=now))
        s.commit()
        s.add(MessageORM(session_id=sid, role="user", content="hi"))
        s.add(MessageORM(session_id=sid, role="assistant", content="hello"))
        s.commit()
    finally:
        s.close()
    return sid


def test_list_messages_404_for_unknown(client: TestClient) -> None:
    r = client.get("/v1/sessions/missing/messages")
    assert r.status_code == 404


def test_list_messages_returns_items(client: TestClient) -> None:
    sid = _make_session_with_messages()
    r = client.get(f"/v1/sessions/{sid}/messages")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 2
    assert [m["role"] for m in body["items"]] == ["user", "assistant"]
    assert body["items"][1]["content"] == "hello"


def test_list_messages_validates_limit(client: TestClient) -> None:
    sid = _make_session_with_messages()
    r = client.get(f"/v1/sessions/{sid}/messages?limit=0")
    assert r.status_code == 400
    r = client.get(f"/v1/sessions/{sid}/messages?limit=99999")
    assert r.status_code == 400
    r = client.get(f"/v1/sessions/{sid}/messages?offset=-1")
    assert r.status_code == 400
