"""Ollama provider — local LLM runtime at ``http://localhost:11434``.

Implements the :class:`backend.tom.providers.base.Provider` protocol
via HTTP calls to the public Ollama REST API:

- ``POST /api/chat``        — streaming NDJSON
- ``POST /api/embeddings``  — batch embeddings
- ``GET  /api/tags``        — model discovery
- ``GET  /``                — liveness / version

No API key is required; the provider is meant for local single-user use.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx

from backend.tom.providers.base import (
    HealthReport,
    Message,
    TokenChunk,
    ToolCall,
    ToolDef,
)


class OllamaProvider:
    """Provider for a local Ollama daemon."""

    def __init__(
        self,
        *,
        name: str,
        model: str,
        base_url: str = "http://localhost:11434",
        client: httpx.AsyncClient | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.name = name
        self.model = model
        self._base_url = base_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._timeout = timeout

    @property
    def type(self) -> str:
        return "ollama"

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def chat(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolDef] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[TokenChunk]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [_to_ollama_message(m) for m in messages],
            "stream": True,
            "options": {"temperature": temperature},
        }
        if max_tokens is not None:
            payload["options"]["num_predict"] = max_tokens
        if tools:
            payload["tools"] = [_tool_to_ollama(t) for t in tools]

        async with self._client.stream(
            "POST", f"{self._base_url}/api/chat", json=payload
        ) as response:
            response.raise_for_status()
            buffer = ""
            async for raw in response.aiter_text():
                buffer += raw
                while True:
                    if "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            chunk = json.loads(line)
                        except ValueError:
                            buffer = line + "\n" + buffer
                            break
                        for c in _ollama_to_chunks(chunk):
                            yield c
                        continue
                    stripped = buffer.strip()
                    if not stripped:
                        buffer = ""
                        break
                    try:
                        chunk = json.loads(stripped)
                    except ValueError:
                        break
                    buffer = ""
                    for c in _ollama_to_chunks(chunk):
                        yield c

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        response = await self._client.post(
            f"{self._base_url}/api/embeddings",
            json={"model": self.model, "prompt": list(texts)},
        )
        response.raise_for_status()
        body = response.json()
        if "embeddings" in body:
            return [list(map(float, vec)) for vec in body["embeddings"]]
        single = body.get("embedding")
        if single is not None:
            return [list(map(float, single))]
        raise RuntimeError("Ollama /api/embeddings response missing embedding payload")

    async def health(self) -> HealthReport:
        start = time.monotonic()
        try:
            response = await self._client.get(f"{self._base_url}/api/tags")
            response.raise_for_status()
            models = [m["name"] for m in response.json().get("models", [])]
            return HealthReport(
                ok=True,
                latency_ms=(time.monotonic() - start) * 1000.0,
                models=models,
            )
        except Exception as exc:
            return HealthReport(
                ok=False,
                latency_ms=(time.monotonic() - start) * 1000.0,
                error=f"{type(exc).__name__}: {exc}",
            )


def _ollama_to_chunks(chunk: dict[str, Any]) -> list[TokenChunk]:
    text = ((chunk.get("message") or {}) or {}).get("content") or ""
    raw_tool_calls = (chunk.get("message") or {}).get("tool_calls") or []
    done = chunk.get("done", False)
    return [
        TokenChunk(
            text_delta=text,
            tool_calls=[_tool_call_from_ollama(tc) for tc in raw_tool_calls],
            finish_reason="stop" if done else None,
        )
    ]


def _to_ollama_message(m: Message) -> dict[str, Any]:
    out: dict[str, Any] = {"role": m.role, "content": m.content}
    if m.tool_calls:
        out["tool_calls"] = [
            {"function": {"name": tc.name, "arguments": tc.arguments}} for tc in m.tool_calls
        ]
    return out


def _tool_to_ollama(t: ToolDef) -> dict[str, Any]:
    return {"type": "function", "function": {"name": t.name, "description": t.description}}


def _tool_call_from_ollama(tc: dict[str, Any]) -> ToolCall:
    fn = tc.get("function", {})
    args = fn.get("arguments") or {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            args = {"_raw": args}
    return ToolCall(
        id=str(tc.get("id") or f"ollama-{fn.get('name', 'tool')}"),
        name=fn.get("name", ""),
        arguments=dict(args),
    )


__all__: list[str] = ["OllamaProvider"]
