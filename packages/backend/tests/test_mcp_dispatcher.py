"""Tests for :mod:`backend.tom.mcp_bridge.dispatcher` using an in-memory
:class:`InMemoryMcpClient` registry — no real subprocess.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from backend.tom.db.init_db import init_db
from backend.tom.db.models import AuditLogORM
from backend.tom.db.session import SessionLocal
from backend.tom.mcp_bridge.dispatcher import MCPDispatcher
from backend.tom.mcp_bridge.manifest import EntrypointModel, McpServerManifest
from backend.tom.mcp_bridge.registry import in_memory_registry_with
from backend.tom.providers.base import ToolCall


def _manifest(name: str = "echo", version: str = "0.1.0") -> McpServerManifest:
    return McpServerManifest(
        name=name,
        version=version,
        description=f"{name} server",
        entrypoint=EntrypointModel(command="echo", args=[name]),
        capabilities=["tools"],
        tool_timeout_seconds=2.0,
    )


def test_dispatcher_lists_tools(tmp_path: Path) -> None:
    init_db()
    manifest = _manifest()
    registry = in_memory_registry_with(
        [manifest],
        tools_by_server={
            manifest.name: [{"name": "echo", "description": "echo back", "parameters": {}}]
        },
        handlers_by_server={manifest.name: None},
    )
    dispatcher = MCPDispatcher(registry)

    async def go() -> None:
        # Need to start the in-memory client manually
        await registry.start_async(manifest)
        tools = await dispatcher.tools()
        names = [t["name"] for t in tools]
        assert names == ["echo"]

    import asyncio

    asyncio.run(go())


def test_dispatcher_invokes_and_audits(tmp_path: Path) -> None:
    init_db()
    manifest = _manifest("echo", "0.1.0")
    handler_called: list[tuple[str, dict[str, Any]]] = []

    async def handler(name: str, args: dict[str, Any]) -> dict[str, Any]:
        handler_called.append((name, args))
        return {"content": args}

    registry = in_memory_registry_with(
        [manifest],
        tools_by_server={
            manifest.name: [{"name": "echo", "description": "echo back", "parameters": {}}]
        },
        handlers_by_server={manifest.name: handler},
    )
    dispatcher = MCPDispatcher(registry)

    async def go() -> None:
        await registry.start_async(manifest)
        result = await dispatcher.dispatch(ToolCall(id="t1", name="echo", arguments={"msg": "hi"}))
        assert result["content"] == {"msg": "hi"}
        assert handler_called == [("echo", {"msg": "hi"})]

    import asyncio

    asyncio.run(go())

    s = SessionLocal()
    try:
        audit = (
            s.execute(
                select(AuditLogORM)
                .where(AuditLogORM.action == "tool.invoke")
                .order_by(AuditLogORM.id.desc())
            )
            .scalars()
            .first()
        )
        assert audit is not None
        assert audit.target_id == "echo/echo"
        payload = audit.payload
        assert payload["tool"] == "echo"
        assert payload["server"] == "echo"
        assert payload["success"] is True
    finally:
        s.close()


def test_dispatcher_unknown_tool_raises(tmp_path: Path) -> None:
    init_db()
    manifest = _manifest()
    registry = in_memory_registry_with([manifest], handlers_by_server={manifest.name: None})
    dispatcher = MCPDispatcher(registry)

    async def go() -> None:
        await registry.start_async(manifest)
        from backend.tom.chat.tool_dispatcher import ToolDispatchError

        with pytest.raises(ToolDispatchError):
            await dispatcher.dispatch(ToolCall(id="x", name="no-such-tool", arguments={}))

    import asyncio

    asyncio.run(go())
