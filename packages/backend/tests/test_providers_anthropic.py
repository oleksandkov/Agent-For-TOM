"""Tests for the Anthropic Messages provider via :class:`httpx.MockTransport`."""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from backend.tom.providers.anthropic import AnthropicProvider
from backend.tom.providers.base import HealthReport, Message, ToolDef


def _fake(handler: Callable) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _anthropic_sse(events: list[dict]) -> bytes:
    body = []
    for evt in events:
        body.append(f"event: {evt.get('type', 'message')}")
        body.append(f"data: {json.dumps(evt)}")
        body.append("")
    return "\n".join(body).encode("utf-8")


@pytest.mark.asyncio
async def test_anthropic_chat_streams_text() -> None:
    events = [
        {"type": "message_start", "message": {}},
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": ""},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "Hel"},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "lo"},
        },
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
        },
        {"type": "message_stop"},
    ]

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/messages"
        body = json.loads(request.content)
        assert body["model"] == "claude-3-5-sonnet"
        assert body["messages"] == [{"role": "user", "content": "hi"}]
        assert body["system"] == "You are TOM."
        assert request.headers["x-api-key"] == "sk-ant-fake"
        return httpx.Response(200, content=_anthropic_sse(events))

    provider = AnthropicProvider(
        name="anthropic-prod",
        model="claude-3-5-sonnet",
        api_key="sk-ant-fake",
        client=_fake(handler),
    )
    pieces: list[str] = []
    finish: str | None = None
    async for chunk in provider.chat(
        [
            Message(role="system", content="You are TOM."),
            Message(role="user", content="hi"),
        ]
    ):
        pieces.append(chunk.text_delta)
        if chunk.finish_reason:
            finish = chunk.finish_reason
    assert "".join(pieces) == "Hello"
    assert finish == "stop"


@pytest.mark.asyncio
async def test_anthropic_chat_tool_use() -> None:
    events = [
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {
                "type": "tool_use",
                "id": "toolu_1",
                "name": "echo",
                "input": {},
            },
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": '{"msg":'},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": '"hi"}'},
        },
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "tool_use"},
        },
    ]

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert "tools" in body
        return httpx.Response(200, content=_anthropic_sse(events))

    provider = AnthropicProvider(
        name="anthropic-prod",
        model="claude",
        api_key="k",
        client=_fake(handler),
    )
    seen: list = []
    async for chunk in provider.chat(
        [Message(role="user", content="use the tool")],
        tools=[ToolDef(name="echo", description="echo", parameters={})],
    ):
        seen.extend(chunk.tool_calls)
    assert seen and seen[0].name == "echo"
    assert seen[0].arguments == {"msg": "hi"}


@pytest.mark.asyncio
async def test_anthropic_health() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/models"
        return httpx.Response(
            200,
            json={"data": [{"id": "claude-3-5-sonnet-latest"}]},
        )

    provider = AnthropicProvider(
        name="anthropic-prod",
        model="claude-3-5-sonnet",
        api_key="k",
        client=_fake(handler),
    )
    report = await provider.health()
    assert isinstance(report, HealthReport)
    assert report.ok is True
    assert "claude-3-5-sonnet-latest" in report.models


@pytest.mark.asyncio
async def test_anthropic_embed_unsupported() -> None:
    provider = AnthropicProvider(name="anthropic-prod", model="claude-3-5-sonnet", api_key="k")
    with pytest.raises(NotImplementedError):
        await provider.embed(["hi"])


@pytest.mark.asyncio
async def test_anthropic_message_maps_tool_role_to_user_with_tool_result() -> None:
    captured: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            content=_anthropic_sse(
                [
                    {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "ok"}},
                    {"type": "message_delta", "delta": {"stop_reason": "end_turn"}},
                ]
            ),
        )

    provider = AnthropicProvider(
        name="anthropic-prod",
        model="claude",
        api_key="k",
        client=_fake(handler),
    )
    async for _ in provider.chat(
        [
            Message(role="user", content="ask"),
            Message(role="tool", tool_call_id="toolu_1", content='{"ok":true}'),
        ]
    ):
        pass

    body = captured["body"]
    assert body["messages"][1]["role"] == "user"
    assert body["messages"][1]["content"][0]["type"] == "tool_result"
    assert body["messages"][1]["content"][0]["tool_use_id"] == "toolu_1"
