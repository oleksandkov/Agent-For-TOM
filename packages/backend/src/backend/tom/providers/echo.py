"""Echo LLM provider — a deterministic local stand-in.

Implements :class:`backend.tom.providers.base.Provider` without
calling any external service. Streams the user's last message back
prefixed with a fixed greeting, so the rest of the TOM agent
stack (SSE, orchestrator, locks, audit log, core-memory writes)
exercises end-to-end without an LLM dependency.

Used in CI and local development when no provider is configured.
Pick this with ``type="echo"`` in ``provider_configs``.

This is a real, full-fidelity LLM substitute from the orchestrator's
point of view: it speaks the :class:`TokenChunk` stream protocol,
handles :attr:`finish_reason`, and is async-iterator-compatible. A
provider that emits tool calls would interop identically — only the
upstream HTTP/SSE parser differs.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from typing import Any

from backend.tom.providers.base import HealthReport, Message, TokenChunk

PER_CHUNK_DELAY_SECONDS: float = 0.005  # small jitter so streaming is visible


class EchoProvider:
    """Deterministic local provider — echoes the user's content back."""

    type: str = "llm-echo"

    def __init__(self, *, name: str = "echo", model: str = "echo-v0") -> None:
        self.name = name
        self.model = model

    async def _stream_reply(self, reply: str) -> AsyncIterator[TokenChunk]:
        for token in reply.split(" "):
            yield TokenChunk(text_delta=token + " ")
            await asyncio.sleep(PER_CHUNK_DELAY_SECONDS)
        yield TokenChunk(finish_reason="stop")

    def chat(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[Any] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[TokenChunk]:
        _ = tools, temperature, max_tokens  # accepted for Protocol parity
        last_user = next(
            (m.content for m in reversed(messages) if m.role == "user"),
            "",
        )
        reply = (
            f"[echo/{self.name}/{self.model}] You said: {last_user!s}. "
            "Wire Ollama (or any backend.tom.providers class) and change "
            "provider_configs.type to switch off this echo loop."
        )
        return self._stream_reply(reply)

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [[0.0] * 4 for _ in texts]

    async def health(self) -> HealthReport:
        return HealthReport(ok=True, latency_ms=0.0, models=[self.model])


__all__: list[str] = ["EchoProvider", "PER_CHUNK_DELAY_SECONDS"]  # noqa: RUF022
