"""Tests for :mod:`backend.tom.chat.tool_dispatcher`."""

from __future__ import annotations

import pytest

from backend.tom.chat.tool_dispatcher import StubDispatcher, ToolDispatchError
from backend.tom.providers.base import ToolCall


def test_stub_dispatcher_lists_known_tools() -> None:
    import asyncio

    stub = StubDispatcher(
        known_tools=[{"name": "echo", "description": "echo back", "parameters": {}}]
    )

    async def _go() -> list[dict]:
        return await stub.tools()

    tools = asyncio.run(_go())
    assert tools == [{"name": "echo", "description": "echo back", "parameters": {}}]


@pytest.mark.asyncio
async def test_stub_dispatcher_dispatch_raises() -> None:
    stub = StubDispatcher()
    with pytest.raises(ToolDispatchError):
        await stub.dispatch(ToolCall(id="t1", name="missing", arguments={"x": 1}))


@pytest.mark.asyncio
async def test_stub_dispatcher_ignores_extra_keys_in_tools() -> None:
    stub = StubDispatcher(known_tools=[{"name": "x"}, {"garbage": True}])
    tools = await stub.tools()
    assert tools[0]["name"] == "x"
    assert tools[1] == {"garbage": True}
