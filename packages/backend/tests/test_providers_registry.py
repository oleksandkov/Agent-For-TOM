"""Tests for :mod:`backend.tom.providers.registry`."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from backend.tom.db.init_db import init_db
from backend.tom.db.models import ProviderConfigORM
from backend.tom.db.session import SessionLocal
from backend.tom.providers.anthropic import AnthropicProvider
from backend.tom.providers.google import GoogleProvider
from backend.tom.providers.ollama import OllamaProvider
from backend.tom.providers.openai_compat import OpenAICompatProvider
from backend.tom.providers.registry import (
    FallbackChain,
    ProviderRegistry,
    clear_provider_api_key,
    get_provider_api_key,
    keyring_slot,
    set_provider_api_key,
)


def _upsert_provider(
    *,
    name: str,
    type_: str,
    model: str = "m",
    base_url: str | None = None,
    is_default: bool = False,
    fallback_chain: list[str] | None = None,
) -> None:
    s = SessionLocal()
    try:
        existing = s.execute(
            select(ProviderConfigORM).where(ProviderConfigORM.name == name)
        ).scalar_one_or_none()
        if existing:
            s.delete(existing)
            s.commit()
        s.add(
            ProviderConfigORM(
                name=name,
                type=type_,
                model=model,
                base_url=base_url,
                is_default=is_default,
                fallback_chain=fallback_chain or [],
            )
        )
        s.commit()
    finally:
        s.close()


def test_keyring_slot_format() -> None:
    assert keyring_slot("ollama", "local") == "tom/provider/ollama/local"
    assert keyring_slot("openai", "prod") == "tom/provider/openai/prod"


def test_set_and_get_provider_api_key(virtual_keyring: object, tmp_path: Path) -> None:
    set_provider_api_key("openai", "prod", "sk-fake")
    assert get_provider_api_key("openai", "prod") == "sk-fake"
    clear_provider_api_key("openai", "prod")
    assert get_provider_api_key("openai", "prod") is None


def test_get_provider_api_key_returns_none_for_ollama(
    virtual_keyring: object, tmp_path: Path
) -> None:
    # Even if something is in keyring, Ollama should never return it.
    set_provider_api_key("ollama", "local", "shouldnt-be-used")
    try:
        assert get_provider_api_key("ollama", "local") is None
    finally:
        clear_provider_api_key("ollama", "local")


def test_registry_builds_ollama(virtual_keyring: object, tmp_path: Path) -> None:
    init_db()
    _upsert_provider(
        name="local", type_="ollama", model="qwen2:1.5b", base_url="http://localhost:11434"
    )
    reg = ProviderRegistry()
    try:
        provider = reg.get("local")
        assert isinstance(provider, OllamaProvider)
        assert provider.model == "qwen2:1.5b"
        assert provider._base_url == "http://localhost:11434"
    finally:
        reg.close_all()


def test_registry_builds_openai_compat_with_keyring_key(
    virtual_keyring: object, tmp_path: Path
) -> None:
    init_db()
    set_provider_api_key("openai", "prod", "sk-fake")
    try:
        _upsert_provider(name="prod", type_="openai", model="gpt-4o-mini")
        reg = ProviderRegistry()
        try:
            provider = reg.get("prod")
            assert isinstance(provider, OpenAICompatProvider)
            assert provider._api_key == "sk-fake"
        finally:
            reg.close_all()
    finally:
        clear_provider_api_key("openai", "prod")


def test_registry_raises_when_api_key_missing(virtual_keyring: object, tmp_path: Path) -> None:
    init_db()
    _upsert_provider(name="anthropic-no-key", type_="anthropic", model="claude")
    reg = ProviderRegistry()
    reg.clear_cache()
    with pytest.raises(PermissionError):
        reg.get("anthropic-no-key")


def test_registry_builds_anthropic_and_google(virtual_keyring: object, tmp_path: Path) -> None:
    init_db()
    set_provider_api_key("anthropic", "a", "ant-key")
    set_provider_api_key("google", "g", "AIza")
    try:
        _upsert_provider(name="a", type_="anthropic", model="claude")
        _upsert_provider(name="g", type_="google", model="gemini-2.0-flash")
        reg = ProviderRegistry()
        try:
            assert isinstance(reg.get("a"), AnthropicProvider)
            assert isinstance(reg.get("g"), GoogleProvider)
        finally:
            reg.close_all()
    finally:
        clear_provider_api_key("anthropic", "a")
        clear_provider_api_key("google", "g")


def test_registry_get_default_requires_a_default(virtual_keyring: object, tmp_path: Path) -> None:
    init_db()
    reg = ProviderRegistry()
    with pytest.raises(LookupError):
        reg.get_default()


def test_registry_get_default_returns_marked_provider(
    virtual_keyring: object, tmp_path: Path
) -> None:
    init_db()
    _upsert_provider(name="primary", type_="ollama", model="m", is_default=True)
    _upsert_provider(name="other", type_="ollama", model="m")
    reg = ProviderRegistry()
    try:
        provider = reg.get_default()
        assert provider.name == "primary"
    finally:
        reg.close_all()


@pytest.mark.asyncio
async def test_fallback_chain_primary_success(virtual_keyring: object, tmp_path: Path) -> None:
    """If the primary emits chunks, the fallback is never consulted."""
    init_db()
    from backend.tom.providers.base import TokenChunk

    class _StubPrimary:
        name = "p"
        type = "stub"
        model = "m"

        async def chat(self, messages, *, tools=None, temperature=0.7, max_tokens=None):
            yield TokenChunk(text_delta="from-primary")
            yield TokenChunk(finish_reason="stop")

        async def embed(self, texts):
            return [[0.0] * 4]

        async def health(self):
            from backend.tom.providers.base import HealthReport

            return HealthReport(ok=True)

    reg = ProviderRegistry()
    chain = FallbackChain(reg, primary="p").with_chain(["never"])
    chain.registry._cache["p"] = _StubPrimary()  # type: ignore[assignment]

    pieces: list[str] = []
    async for chunk in chain.chat([]):
        pieces.append(chunk.text_delta)
    assert "".join(pieces) == "from-primary"


@pytest.mark.asyncio
async def test_fallback_chain_skips_to_fallback(virtual_keyring: object, tmp_path: Path) -> None:
    """If the primary raises before any chunk, fallback is used."""
    init_db()
    from backend.tom.providers.base import HealthReport, TokenChunk

    class _Broken:
        name = "broken"
        type = "stub"
        model = "m"

        async def chat(self, messages, *, tools=None, temperature=0.7, max_tokens=None):
            raise RuntimeError("upstream down")
            yield  # pragma: no cover

        async def embed(self, texts):
            return [[0.0] * 4]

        async def health(self):
            return HealthReport(ok=False, error="down")

    class _Fallback:
        name = "fallback"
        type = "stub"
        model = "m"

        async def chat(self, messages, *, tools=None, temperature=0.7, max_tokens=None):
            yield TokenChunk(text_delta="from-fallback")
            yield TokenChunk(finish_reason="stop")

        async def embed(self, texts):
            return [[0.0] * 4]

        async def health(self):
            return HealthReport(ok=True)

    reg = ProviderRegistry()
    chain = FallbackChain(reg, primary="broken").with_chain(["fallback"])
    chain.registry._cache["broken"] = _Broken()  # type: ignore[assignment]
    chain.registry._cache["fallback"] = _Fallback()  # type: ignore[assignment]

    pieces: list[str] = []
    async for chunk in chain.chat([]):
        pieces.append(chunk.text_delta)
    assert "".join(pieces) == "from-fallback"


@pytest.mark.asyncio
async def test_fallback_chain_raises_when_all_fail(virtual_keyring: object, tmp_path: Path) -> None:
    init_db()
    from backend.tom.providers.base import HealthReport

    class _Bad:
        name = "x"
        type = "stub"
        model = "m"

        async def chat(self, messages, *, tools=None, temperature=0.7, max_tokens=None):
            raise RuntimeError("nope")
            yield  # pragma: no cover

        async def embed(self, texts):
            return [[0.0] * 4]

        async def health(self):
            return HealthReport(ok=False)

    reg = ProviderRegistry()
    chain = FallbackChain(reg, primary="x").with_chain(["y"])
    chain.registry._cache["x"] = _Bad()  # type: ignore[assignment]
    chain.registry._cache["y"] = _Bad()  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="all providers"):
        async for _ in chain.chat([]):
            pass  # pragma: no cover
