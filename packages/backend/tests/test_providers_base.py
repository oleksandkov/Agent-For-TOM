"""Tests for :mod:`backend.tom.providers.base`."""

from __future__ import annotations

import pytest

from backend.tom.providers.base import (
    HealthReport,
    Message,
    TokenChunk,
    ToolCall,
    ToolDef,
    collect_text,
    filter_supported_kwargs,
)


def test_message_defaults() -> None:
    m = Message()
    assert m.role == "user"
    assert m.content == ""
    assert m.tool_calls == []
    assert m.tool_call_id is None


def test_token_chunk_defaults() -> None:
    c = TokenChunk()
    assert c.text_delta == ""
    assert c.tool_calls == []
    assert c.finish_reason is None


def test_tool_def_dict_factory() -> None:
    td = ToolDef(name="x", description="y")
    assert td.parameters == {}


def test_health_report_serialization() -> None:
    r = HealthReport(
        ok=True,
        latency_ms=42.5,
        models=["a", "b"],
        error=None,
    )
    assert r.to_dict() == {
        "ok": True,
        "latency_ms": 42.5,
        "models": ["a", "b"],
        "error": None,
    }


def test_filter_supported_kwargs_openai() -> None:
    out = filter_supported_kwargs(
        "openai",
        temperature=0.5,
        top_p=0.9,
        seed=42,  # not in allowlist for openai/compat
        stop=["\n"],
        unknown_kw="x",
    )
    assert out == {"temperature": 0.5, "top_p": 0.9, "stop": ["\n"]}


def test_filter_supported_kwargs_ollama_passes_through() -> None:
    out = filter_supported_kwargs("ollama", temperature=0.5, foo="bar")
    assert out == {"temperature": 0.5, "foo": "bar"}


@pytest.mark.asyncio
async def test_collect_text_joins_text() -> None:
    async def gen():
        yield TokenChunk(text_delta="hello ")
        yield TokenChunk(text_delta="world", finish_reason="stop")

    text, tool_calls, finish = await collect_text(gen())
    assert text == "hello world"
    assert tool_calls == []
    assert finish == "stop"


@pytest.mark.asyncio
async def test_collect_text_collects_tool_calls() -> None:
    async def gen():
        yield TokenChunk(
            tool_calls=[
                ToolCall(id="t1", name="echo", arguments={"x": 1}),
            ],
        )
        yield TokenChunk(finish_reason="tool_calls")

    text, tool_calls, finish = await collect_text(gen())
    assert text == ""
    assert len(tool_calls) == 1
    assert tool_calls[0].name == "echo"
    assert finish == "tool_calls"
