"""Tests for ``POST /v1/chat`` SSE streaming."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.tom.api.chat import router
from backend.tom.api.deps import set_orchestrator
from backend.tom.chat import ChatOrchestrator
from backend.tom.chat.tool_dispatcher import StubDispatcher
from backend.tom.db.init_db import init_db
from backend.tom.db.models import ProviderConfigORM, SessionORM
from backend.tom.db.session import SessionLocal
from backend.tom.providers.base import HealthReport, Provider, TokenChunk


class _StubProvider:
    def __init__(self, name: str = "stub") -> None:
        self.name = name

    @property
    def type(self) -> str:
        return "stub"

    def chat(self, messages, *, tools=None, temperature=0.7, max_tokens=None):
        async def _gen():
            yield TokenChunk(text_delta="hi ")
            yield TokenChunk(text_delta="there", finish_reason="stop")

        return _gen()

    async def embed(self, texts):
        return [[0.0] * 4]

    async def health(self):
        return HealthReport(ok=True)


class _Registry:
    def __init__(self, provider: Provider) -> None:
        self._provider = provider

    def get(self, name: str) -> Provider:
        return self._provider

    def get_default(self) -> Provider:
        return self._provider


class _Memory:
    def read_core(self):
        from datetime import UTC, datetime

        from backend.tom.memory.types import CoreMemory

        return CoreMemory(
            blocks=[],
            facts=[],
            version=0,
            updated_at=datetime.now(UTC),
        )


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    init_db()
    provider = _StubProvider()
    orchestrator = ChatOrchestrator(
        memory=_Memory(),  # type: ignore[arg-type]
        providers=_Registry(provider),  # type: ignore[arg-type]
        dispatcher=StubDispatcher(),
    )
    set_orchestrator(orchestrator)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _seed_session(name: str = "chat-session") -> None:
    s = SessionLocal()
    try:
        from sqlalchemy import delete

        s.execute(delete(ProviderConfigORM))
        s.execute(delete(SessionORM))
        s.add(
            ProviderConfigORM(
                name="stub",
                type="ollama",
                model="m",
                is_default=True,
                fallback_chain=[],
            )
        )
        s.add(SessionORM(id=name, title="chat"))
        s.commit()
    finally:
        s.close()


def test_post_chat_streams_sse_done(client: TestClient) -> None:
    _seed_session()
    with client.stream(
        "POST",
        "/v1/chat",
        json={"session_id": "chat-session", "content": "hi"},
    ) as r:
        assert r.status_code == 200
        body = "".join(r.iter_lines())
    # Each SSE message has "event: <type>" + "data: <json>" + blank line
    assert "event: token" in body
    assert "event: done" in body
    assert '"text_delta": "hi "' in body or '"text_delta": "hi"' in body


def test_post_chat_session_required_event(client: TestClient) -> None:
    # No session in DB → expect a session_required event with reason
    _seed_session(name="real")  # a different session exists; ours doesn't
    with client.stream(
        "POST",
        "/v1/chat",
        json={"session_id": "missing", "content": "hi"},
    ) as r:
        assert r.status_code == 200
        body = "".join(r.iter_lines())
    assert "session_required" in body
    assert "session_not_found" in body


def test_post_chat_validation_422(client: TestClient) -> None:
    r = client.post("/v1/chat", json={"content": "no session_id"})
    assert r.status_code == 422
