"""Tests for the providers FastAPI router."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.tom.db.init_db import init_db
from backend.tom.db.session import SessionLocal
from backend.tom.providers.api import router, set_registry
from backend.tom.providers.base import HealthReport, TokenChunk
from backend.tom.providers.registry import ProviderRegistry


class _StubProvider:
    def __init__(self, *, name: str, type_: str, model: str = "m") -> None:
        self.name = name
        self._type = type_
        self.model = model

    @property
    def type(self) -> str:
        return self._type

    def chat(self, messages, *, tools=None, temperature=0.7, max_tokens=None):
        async def _gen():
            yield TokenChunk(text_delta="hi")
            yield TokenChunk(finish_reason="stop")

        return _gen()

    async def embed(self, texts):
        return [[0.1, 0.2]]

    async def health(self):
        return HealthReport(ok=True, latency_ms=12.0, models=["stub-model"])


@pytest.fixture
def client(virtual_keyring: object, tmp_path: Path) -> TestClient:
    init_db()
    # Seed two providers in DB
    s = SessionLocal()
    try:
        from sqlalchemy import delete

        from backend.tom.db.models import ProviderConfigORM

        s.execute(delete(ProviderConfigORM))
        s.add(
            ProviderConfigORM(
                name="local",
                type="ollama",
                model="qwen2:1.5b",
                is_default=True,
                fallback_chain=[],
            )
        )
        s.add(
            ProviderConfigORM(
                name="openai-prod",
                type="openai",
                model="gpt-4o-mini",
                fallback_chain=[],
            )
        )
        s.commit()
    finally:
        s.close()

    reg = ProviderRegistry()
    reg._cache["local"] = _StubProvider(name="local", type_="ollama")  # type: ignore[assignment]
    reg._cache["openai-prod"] = _StubProvider(name="openai-prod", type_="openai")  # type: ignore[assignment]
    set_registry(reg)

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_list_providers(client: TestClient) -> None:
    r = client.get("/v1/providers")
    assert r.status_code == 200
    body = r.json()
    names = {p["name"] for p in body["providers"]}
    assert names == {"local", "openai-prod"}
    assert body["default"] == "local"


def test_provider_health_ok(client: TestClient) -> None:
    r = client.get("/v1/providers/local/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["latency_ms"] == 12.0


def test_provider_health_404_for_unknown(client: TestClient) -> None:
    r = client.get("/v1/providers/nope/health")
    assert r.status_code == 404
