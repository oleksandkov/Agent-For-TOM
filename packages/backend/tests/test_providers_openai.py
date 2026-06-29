"""Tests for the OpenAI-compat provider via :class:`httpx.MockTransport`."""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from backend.tom.providers.base import (
    Message,
)
from backend.tom.providers.openai_compat import OpenAICompatProvider


def _fake_async_client(handler: Callable) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _sse(events: list[dict]) -> bytes:
    """Format a list of dicts as an SSE stream with chunked-style breaks."""
    body = []
    for evt in events:
        body.append(f"data: {json.dumps(evt)}")
        body.append("")
    body.append("data: [DONE]")
    body.append("")
    return "\n".join(body).encode("utf-8")


@pytest.mark.asyncio
async def test_openai_chat_streams_text_and_tool_calls() -> None:
    events = [
        {
            "choices": [
                {"delta": {"content": "Hel"}, "finish_reason": None},
            ],
        },
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {"id": "t1", "function": {"name": "echo", "arguments": "{}"}},
                        ],
                    },
                    "finish_reason": None,
                },
            ],
        },
        {
            "choices": [
                {"delta": {"content": "lo"}, "finish_reason": "tool_calls"},
            ],
        },
    ]

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/v1/chat/completions"
        body = json.loads(request.content)
        assert body["model"] == "gpt-4o-mini"
        assert body["stream"] is True
        assert body["temperature"] == 0.2
        assert "Authorization" in request.headers
        return httpx.Response(200, content=_sse(events))

    provider = OpenAICompatProvider(
        name="openai-prod",
        model="gpt-4o-mini",
        base_url="https://api.openai.com/v1",
        api_key="sk-fake",
        client=_fake_async_client(handler),
    )

    pieces: list[str] = []
    finishes: list[str | None] = []
    tool_calls_seen: list = []
    async for chunk in provider.chat(
        [Message(role="user", content="hi")],
        temperature=0.2,
        max_tokens=50,
    ):
        pieces.append(chunk.text_delta)
        finishes.append(chunk.finish_reason)
        tool_calls_seen.extend(chunk.tool_calls)

    assert "".join(pieces) == "Hello"
    assert "tool_calls" in [f for f in finishes if f]
    assert tool_calls_seen and tool_calls_seen[0].name == "echo"


@pytest.mark.asyncio
async def test_openai_chat_handles_done_marker_first() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"data: [DONE]\n\n")

    provider = OpenAICompatProvider(
        name="openai-prod",
        model="m",
        api_key="x",
        client=_fake_async_client(handler),
    )
    finish: str | None = None
    async for chunk in provider.chat([Message(role="user", content="hi")]):
        if chunk.finish_reason:
            finish = chunk.finish_reason
    assert finish == "stop"


@pytest.mark.asyncio
async def test_openai_embed_returns_per_item_vectors() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/embeddings"
        body = json.loads(request.content)
        assert body["input"] == ["a", "b"]
        return httpx.Response(
            200,
            json={"data": [{"embedding": [0.1, 0.2]}, {"embedding": [0.3, 0.4]}]},
        )

    provider = OpenAICompatProvider(
        name="openai-prod",
        model="text-embedding-3-small",
        api_key="sk-fake",
        client=_fake_async_client(handler),
    )
    out = await provider.embed(["a", "b"])
    assert out == [[0.1, 0.2], [0.3, 0.4]]


@pytest.mark.asyncio
async def test_openai_health_ok() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/models"
        return httpx.Response(
            200,
            json={"data": [{"id": "gpt-4o-mini"}, {"id": "text-embedding-3-small"}]},
        )

    provider = OpenAICompatProvider(
        name="openai-prod",
        model="m",
        api_key="x",
        client=_fake_async_client(handler),
    )
    report = await provider.health()
    assert report.ok is True
    assert "gpt-4o-mini" in report.models


@pytest.mark.asyncio
async def test_openai_health_falls_back_to_models_key() -> None:
    """Some compatible servers use ``models`` array directly (e.g., vLLM)."""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"models": [{"id": "llama"}, {"name": "mixtral"}]},
        )

    provider = OpenAICompatProvider(
        name="custom",
        model="m",
        api_key="x",
        client=_fake_async_client(handler),
    )
    report = await provider.health()
    assert report.ok is True
    assert set(report.models) == {"llama", "mixtral"}


@pytest.mark.asyncio
async def test_openai_health_reports_failure() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="invalid api key")

    provider = OpenAICompatProvider(
        name="openai-prod",
        model="m",
        api_key="bad",
        client=_fake_async_client(handler),
    )
    report = await provider.health()
    assert report.ok is False
    assert report.error and "401" in report.error


@pytest.mark.asyncio
async def test_openai_chat_with_tools() -> None:
    captured: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            content=_sse(
                [
                    {
                        "choices": [
                            {"delta": {"content": "I'll call it."}, "finish_reason": None},
                        ],
                    },
                    {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "id": "t2",
                                            "function": {
                                                "name": "echo",
                                                "arguments": '{"msg":"hi"}',
                                            },
                                        },
                                    ],
                                },
                                "finish_reason": None,
                            },
                        ],
                    },
                    {
                        "choices": [
                            {"delta": {}, "finish_reason": "tool_calls"},
                        ],
                    },
                ]
            ),
        )

    provider = OpenAICompatProvider(
        name="openai-prod",
        model="gpt-4o-mini",
        api_key="sk",
        client=_fake_async_client(handler),
    )
    from backend.tom.providers.base import ToolDef

    seen_tool: list = []
    async for chunk in provider.chat(
        [Message(role="user", content="call echo")],
        tools=[ToolDef(name="echo", description="echo text", parameters={})],
    ):
        seen_tool.extend(chunk.tool_calls)
    assert captured["body"]["tools"][0]["function"]["name"] == "echo"
    assert seen_tool and seen_tool[0].arguments == {"msg": "hi"}
