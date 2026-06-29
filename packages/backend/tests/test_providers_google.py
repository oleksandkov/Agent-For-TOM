"""Tests for the Google Gemini provider via :class:`httpx.MockTransport`."""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from backend.tom.providers.base import Message, ToolDef
from backend.tom.providers.google import GoogleProvider


def _fake(handler: Callable) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _gemini_sse(events: list[dict]) -> bytes:
    body = []
    for evt in events:
        body.append(f"data: {json.dumps(evt)}")
        body.append("")
    return "\n".join(body).encode("utf-8")


@pytest.mark.asyncio
async def test_google_chat_streams_text_and_finish_reason() -> None:
    events = [
        {
            "candidates": [
                {
                    "content": {"parts": [{"text": "Hel"}, {"text": "lo"}]},
                    "finishReason": "STOP",
                }
            ]
        }
    ]

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert "streamGenerateContent" in request.url.path
        assert request.url.params["key"] == "AIza-fake"
        body = json.loads(request.content)
        assert body["contents"] == [{"role": "user", "parts": [{"text": "hi"}]}]
        assert body["systemInstruction"]["parts"][0]["text"] == "helpful"
        return httpx.Response(200, content=_gemini_sse(events))

    provider = GoogleProvider(
        name="google-prod",
        model="gemini-2.0-flash",
        api_key="AIza-fake",
        client=_fake(handler),
    )
    pieces: list[str] = []
    finish: str | None = None
    async for chunk in provider.chat(
        [
            Message(role="system", content="helpful"),
            Message(role="user", content="hi"),
        ]
    ):
        pieces.append(chunk.text_delta)
        if chunk.finish_reason:
            finish = chunk.finish_reason
    assert "".join(pieces) == "Hello"
    assert finish == "stop"


@pytest.mark.asyncio
async def test_google_chat_emits_tool_calls() -> None:
    events = [
        {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "functionCall": {
                                    "name": "echo",
                                    "args": {"msg": "hi"},
                                }
                            }
                        ],
                    },
                    "finishReason": "STOP",
                }
            ]
        }
    ]

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_gemini_sse(events))

    provider = GoogleProvider(
        name="google-prod",
        model="gemini-2.0-flash",
        api_key="k",
        client=_fake(handler),
    )
    seen: list = []
    async for chunk in provider.chat(
        [Message(role="user", content="use tool")],
        tools=[ToolDef(name="echo", description="e", parameters={})],
    ):
        seen.extend(chunk.tool_calls)
    assert seen and seen[0].name == "echo"
    assert seen[0].arguments == {"msg": "hi"}


@pytest.mark.asyncio
async def test_google_embed_calls_per_text() -> None:
    calls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(
            200,
            json={"embedding": {"values": [0.1, 0.2]}},
        )

    provider = GoogleProvider(
        name="google-prod",
        model="text-embedding-004",
        api_key="k",
        client=_fake(handler),
    )
    out = await provider.embed(["a", "b", "c"])
    assert len(calls) == 3
    assert out == [[0.1, 0.2], [0.1, 0.2], [0.1, 0.2]]


@pytest.mark.asyncio
async def test_google_health_filters_relevant_models() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "models": [
                    {
                        "name": "models/gemini-2.0-flash",
                        "supportedGenerationMethods": ["generateContent"],
                    },
                    {
                        "name": "models/text-embedding-004",
                        "supportedGenerationMethods": ["embedContent"],
                    },
                    {
                        "name": "models/some-other",
                        "supportedGenerationMethods": ["countTokens"],
                    },
                ]
            },
        )

    provider = GoogleProvider(
        name="google-prod",
        model="gemini-2.0-flash",
        api_key="k",
        client=_fake(handler),
    )
    report = await provider.health()
    assert report.ok is True
    assert "gemini-2.0-flash" in report.models
    assert "text-embedding-004" in report.models
    assert "some-other" not in report.models


@pytest.mark.asyncio
async def test_google_health_reports_failure() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="bad")

    provider = GoogleProvider(
        name="google-prod",
        model="gemini-2.0-flash",
        api_key="k",
        client=_fake(handler),
    )
    report = await provider.health()
    assert report.ok is False
    assert report.error and "400" in report.error
