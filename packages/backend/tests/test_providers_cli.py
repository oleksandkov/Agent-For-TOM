"""Tests for the ``tom providers`` CLI subcommands."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.tom.__main__ import main
from backend.tom.db.init_db import init_db
from backend.tom.db.session import SessionLocal
from backend.tom.providers.api import set_registry
from backend.tom.providers.base import HealthReport, TokenChunk
from backend.tom.providers.registry import ProviderRegistry


class _FakePrimary:
    name = "primary"
    type = "fake"
    model = "m"

    def chat(self, messages, *, tools=None, temperature=0.7, max_tokens=None):
        async def _gen():
            yield TokenChunk(text_delta="x")
            yield TokenChunk(finish_reason="stop")

        return _gen()

    async def embed(self, texts):
        return [[0.0]]

    async def health(self):
        return HealthReport(ok=True, latency_ms=7.0, models=["fake-model"])


def _seed_named_provider(name: str = "primary") -> None:
    from sqlalchemy import delete

    from backend.tom.db.models import ProviderConfigORM

    s = SessionLocal()
    try:
        s.execute(delete(ProviderConfigORM))
        s.add(
            ProviderConfigORM(
                name=name,
                type="ollama",
                model="qwen2:1.5b",
                is_default=True,
                fallback_chain=[],
            )
        )
        s.commit()
    finally:
        s.close()


def test_providers_health_cli_prints_json(
    virtual_keyring: object,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    init_db()
    _seed_named_provider("primary")
    reg = ProviderRegistry()
    reg._cache["primary"] = _FakePrimary()  # type: ignore[assignment]
    set_registry(reg)

    rc = main(["providers", "health", "primary"])
    assert rc == 0
    out = capsys.readouterr().out  # type: ignore[attr-defined]
    payload = json.loads(out)
    assert payload["ok"] is True


def test_providers_health_cli_unknown(
    virtual_keyring: object,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    init_db()
    rc = main(["providers", "health", "no-such"])
    assert rc == 2
