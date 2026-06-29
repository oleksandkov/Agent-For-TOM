"""Tests for the Ollama provider via :class:`httpx.MockTransport`."""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from backend.tom.providers.base import (
    HealthReport,
    Message,
)
from backend.tom.providers.ollama import OllamaProvider


def _fake_async_client(handler: Callable) -> httpx.AsyncClient:
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport)


@pytest.mark.asyncio
async def test_ollama_chat_streams_chunks() -> None:
    chunks = [
        {"message": {"role": "assistant", "content": "hi "}, "done": False},
        {"message": {"role": "assistant", "content": "there"}, "done": True},
    ]

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/chat"
        body = json.loads(request.content)
        assert body["model"] == "qwen2:1.5b"
        assert body["stream"] is True
        ndjson = "\n".join(json.dumps(c) for c in chunks) + "\n"
        return httpx.Response(200, content=ndjson.encode("utf-8"))

    provider = OllamaProvider(
        name="local-ollama",
        model="qwen2:1.5b",
        base_url="http://localhost:11434",
        client=_fake_async_client(handler),
    )
    pieces: list[str] = []
    finish = None
    async for chunk in provider.chat([Message(role="user", content="hello")]):
        pieces.append(chunk.text_delta)
        if chunk.finish_reason:
            finish = chunk.finish_reason
    assert "".join(pieces) == "hi there"
    assert finish == "stop"


@pytest.mark.asyncio
async def test_ollama_embed_returns_vectors() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/embeddings"
        body = json.loads(request.content)
        assert body["model"] == "nomic-embed-text"
        assert body["prompt"] == ["a", "b"]
        return httpx.Response(
            200,
            json={"embeddings": [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]},
        )

    provider = OllamaProvider(
        name="local-ollama",
        model="nomic-embed-text",
        client=_fake_async_client(handler),
    )
    out = await provider.embed(["a", "b"])
    assert out == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]


@pytest.mark.asyncio
async def test_ollama_embed_single_prompt_legacy() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"embedding": [0.1, 0.2]})

    provider = OllamaProvider(
        name="local-ollama",
        model="m",
        client=_fake_async_client(handler),
    )
    out = await provider.embed(["hi"])
    assert out == [[0.1, 0.2]]


@pytest.mark.asyncio
async def test_ollama_health_reports_models_and_latency() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/tags"
        return httpx.Response(
            200,
            json={"models": [{"name": "qwen2:1.5b"}, {"name": "llama3:8b"}]},
        )

    provider = OllamaProvider(
        name="local-ollama",
        model="qwen2:1.5b",
        client=_fake_async_client(handler),
    )
    report = await provider.health()
    assert isinstance(report, HealthReport)
    assert report.ok is True
    assert report.models == ["qwen2:1.5b", "llama3:8b"]
    assert report.error is None


@pytest.mark.asyncio
async def test_ollama_health_reports_failure() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="service unavailable")

    provider = OllamaProvider(
        name="local-ollama",
        model="qwen2:1.5b",
        client=_fake_async_client(handler),
    )
    report = await provider.health()
    assert report.ok is False
    assert report.error and "503" in report.error


@pytest.mark.asyncio
async def test_ollama_chat_drains_buffer_without_trailing_newline() -> None:
    chunk_text = json.dumps({"message": {"role": "assistant", "content": "tail"}, "done": True})

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=chunk_text.encode("utf-8"))

    provider = OllamaProvider(
        name="local-ollama",
        model="m",
        client=_fake_async_client(handler),
    )
    finish: str | None = None
    text: list[str] = []
    async for chunk in provider.chat([Message(role="user", content="hi")]):
        text.append(chunk.text_delta)
        if chunk.finish_reason:
            finish = chunk.finish_reason
    assert "".join(text) == "tail"
    assert finish == "stop"
