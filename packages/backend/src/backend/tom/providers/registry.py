"""Provider registry — config + keyring + factory + fallback chain.

Resolution order for a request like ``get_provider(name="local")``:

1. Look up ``provider_configs`` row by ``name``.
2. Fetch API key from OS keyring at slot ``tom/provider/{type}/{name}``
   (Ollama skips this — local daemons require no key).
3. Build the right :class:`Provider` instance from the ``type``.
4. Cache the instance for the lifetime of the registry (the next
   ``provider_configs`` PATCH should call :func:`clear_cache`).

The fallback chain is consulted by :class:`FallbackChain` —
:class:`Provider.chat` is tried sequentially; the first non-error
``finish_reason == "stop"`` chunk sequence wins.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Sequence
from typing import TYPE_CHECKING, cast

from sqlalchemy import select

from backend.tom.db.models import ProviderConfigORM
from backend.tom.db.session import SessionLocal
from backend.tom.providers.anthropic import AnthropicProvider
from backend.tom.providers.base import (
    HealthReport,
    Message,
    Provider,
    TokenChunk,
    ToolDef,
)
from backend.tom.providers.echo import EchoProvider
from backend.tom.providers.google import GoogleProvider
from backend.tom.providers.ollama import OllamaProvider
from backend.tom.providers.openai_compat import OpenAICompatProvider

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_KEYRING_PREFIX = "tom/provider"


def keyring_slot(provider_type: str, name: str) -> str:
    """Compose the OS keyring slot name for a provider's API key."""
    return f"{_KEYRING_PREFIX}/{provider_type}/{name}"


def set_provider_api_key(provider_type: str, name: str, key: str) -> None:
    """Write a provider key to the OS keyring (testing + setup)."""
    import keyring

    keyring.set_password(_KEYRING_PREFIX, f"{provider_type}/{name}", key)


def clear_provider_api_key(provider_type: str, name: str) -> None:
    """Delete a provider key from the OS keyring."""
    from contextlib import suppress

    import keyring
    from keyring.errors import PasswordDeleteError

    with suppress(PasswordDeleteError):
        keyring.delete_password(_KEYRING_PREFIX, f"{provider_type}/{name}")


def get_provider_api_key(provider_type: str, name: str) -> str | None:
    """Fetch a provider key from the OS keyring (returns None for Ollama)."""
    if provider_type == "ollama":
        return None
    import keyring
    from keyring.errors import KeyringError

    try:
        return keyring.get_password(_KEYRING_PREFIX, f"{provider_type}/{name}")
    except KeyringError:
        return None


class ProviderRegistry:
    """Maps names from the ``provider_configs`` table to live providers."""

    def __init__(self) -> None:
        self._cache: dict[str, Provider] = {}

    def clear_cache(self) -> None:
        """Drop every cached provider. Call after a config row is mutated."""
        self._cache.clear()

    def get(self, name: str) -> Provider:
        if name not in self._cache:
            self._cache[name] = self._build(name)
        return self._cache[name]

    def get_default(self) -> Provider:
        s = SessionLocal()
        try:
            row = s.execute(
                select(ProviderConfigORM).where(ProviderConfigORM.is_default.is_(True))
            ).scalar_one_or_none()
            if row is None:
                raise LookupError("no default provider configured")
            return self.get(row.name)
        finally:
            s.close()

    def list_names(self) -> list[str]:
        s = SessionLocal()
        try:
            return list(s.execute(select(ProviderConfigORM.name)).scalars().all())
        finally:
            s.close()

    def list_config(self) -> list[dict[str, object]]:
        s = SessionLocal()
        try:
            return [
                {
                    "type": row.type,
                    "name": row.name,
                    "base_url": row.base_url,
                    "model": row.model,
                    "is_default": row.is_default,
                    "fallback_chain": list(row.fallback_chain),
                }
                for row in s.execute(select(ProviderConfigORM)).scalars().all()
            ]
        finally:
            s.close()

    def close_all(self) -> None:
        for provider in self._cache.values():
            close = getattr(provider, "close", None)
            if close is not None:
                # Providers are async; close() is awaited on shutdown by the
                # web entry. Tests that use sync code can call .aclose()
                # via the per-provider methods directly.
                pass
        self._cache.clear()

    def _build(self, name: str) -> Provider:
        s = SessionLocal()
        try:
            row = s.execute(
                select(ProviderConfigORM).where(ProviderConfigORM.name == name)
            ).scalar_one_or_none()
        finally:
            s.close()
        if row is None:
            raise LookupError(f"no provider configured with name={name!r}")

        # Echo: no API key, no base URL — short-circuit before the API-key gate.
        if row.type == "echo":
            return cast(
                "Provider",
                EchoProvider(name=row.name, model=row.model or "echo-v0"),
            )

        api_key = get_provider_api_key(row.type, row.name)
        if row.type in {"openai", "anthropic", "google", "custom"} and not api_key:
            raise PermissionError(
                f"provider {row.name!r} ({row.type}) has no API key in OS keyring "
                f"({keyring_slot(row.type, row.name)})"
            )
        base_url = row.base_url or _default_base_url(row.type)

        if row.type in {"openai", "custom"}:
            return cast(
                "Provider",
                OpenAICompatProvider(
                    name=row.name,
                    model=row.model or "",
                    base_url=base_url,
                    api_key=api_key,
                ),
            )
        if row.type == "ollama":
            return cast(
                "Provider",
                OllamaProvider(
                    name=row.name,
                    model=row.model or "",
                    base_url=base_url,
                ),
            )
        if row.type == "anthropic":
            return cast(
                "Provider",
                AnthropicProvider(
                    name=row.name,
                    model=row.model or "",
                    api_key=api_key,
                    base_url=base_url,
                ),
            )
        if row.type == "google":
            return cast(
                "Provider",
                GoogleProvider(
                    name=row.name,
                    model=row.model or "",
                    api_key=api_key,
                    base_url=base_url,
                ),
            )
        raise ValueError(f"unknown provider type {row.type!r}")


def _default_base_url(provider_type: str) -> str:
    if provider_type == "openai":
        return "https://api.openai.com/v1"
    if provider_type == "ollama":
        return "http://localhost:11434"
    if provider_type == "anthropic":
        return "https://api.anthropic.com"
    if provider_type == "google":
        return "https://generativelanguage.googleapis.com/v1beta"
    if provider_type == "custom":
        return "http://localhost:8000/v1"
    raise ValueError(provider_type)


class FallbackChain:
    """Stream from the primary provider; switch to the chain on a hard error.

    "Hard error" = the stream raises before yielding a chunk with
    ``finish_reason``. If the primary yields anything (even a partial
    chunk), we never fall over to a fallback — preserving streaming UX.
    """

    def __init__(self, registry: ProviderRegistry, primary: str) -> None:
        self.registry = registry
        self.primary_name = primary
        self._chain: list[str] = []

    def with_chain(self, names: Sequence[str]) -> FallbackChain:
        self._chain = list(names)
        return self

    async def chat(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolDef] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[TokenChunk]:
        # Yield from primary first. If we see a hard exception before any
        # chunk, try the fallback(s).
        try:
            primary = self.registry.get(self.primary_name)
            emitted = False
            async for chunk in primary.chat(
                messages,
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
            ):
                emitted = True
                yield chunk
            return
        except Exception as exc:
            if "emitted" in locals() and emitted:
                # The primary already produced output — re-raising is safer
                # than continuing on a half-delivered stream.
                raise
            logger.warning(
                "primary provider %s failed: %s; trying fallback",
                self.primary_name,
                exc,
            )
        for name in self._chain:
            if name == self.primary_name:
                continue
            try:
                fallback = self.registry.get(name)
                async for chunk in fallback.chat(
                    messages,
                    tools=tools,
                    temperature=temperature,
                    max_tokens=max_tokens,
                ):
                    yield chunk
                return
            except Exception as exc:
                logger.warning("fallback provider %s failed: %s", name, exc)
        raise RuntimeError(
            f"all providers in chain failed (primary={self.primary_name}, fallbacks={self._chain})"
        )


__all__: list[str] = [
    "FallbackChain",
    "ProviderRegistry",
    "clear_provider_api_key",
    "get_provider_api_key",
    "keyring_slot",
    "set_provider_api_key",
]


async def _provider_health(registry: ProviderRegistry, name: str) -> HealthReport:
    return await registry.get(name).health()
