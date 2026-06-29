"""End-to-end test: spawn a real FastMCP server subprocess and verify the
JSON-RPC/stdio wire protocol via :class:`StdioMcpClient`.

This test exercises the production code path:
- ``mcp.client.stdio.stdio_client``
- ``mcp.client.session.ClientSession.initialize`` / ``list_tools`` /
  ``call_tool``
- The TOM ``StdioMcpClient`` wrapper

Gated behind ``TOM_RUN_MCP_E2E=1`` so CI without a Python interpreter
(rare) or with strict process-isolation issues can skip it.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

RUN_E2E = os.environ.get("TOM_RUN_MCP_E2E") == "1"


@pytest.mark.skipif(not RUN_E2E, reason="set TOM_RUN_MCP_E2E=1 to run")
@pytest.mark.asyncio
async def test_stdio_client_lifecycle(tmp_path: Path) -> None:
    from backend.tom.mcp_bridge.client import McpClientError, StdioMcpClient
    from backend.tom.mcp_bridge.manifest import (
        EntrypointModel,
        McpServerManifest,
    )

    manifest = McpServerManifest(
        name="tom-fixture",
        version="0.1.0",
        description="in-tree test server",
        entrypoint=EntrypointModel(
            command=sys.executable,
            args=["-m", "tests._mcp_fixture_server"],
        ),
        capabilities=["tools"],
        tool_timeout_seconds=5.0,
    )
    client = StdioMcpClient(manifest)
    try:
        await client.start()
        tools = await client.list_tools()
        names = sorted(t["name"] for t in tools)
        assert names == ["echo", "ping"]

        result = await client.call_tool("echo", {"message": "hello"})
        assert "hello" in str(result["content"])

        try:
            await client.call_tool("does_not_exist", {})
        except McpClientError:
            pass
        else:
            msg = "expected McpClientError on unknown tool"
            raise AssertionError(msg)
    finally:
        await client.stop()


@pytest.mark.skipif(not RUN_E2E, reason="set TOM_RUN_MCP_E2E=1 to run")
@pytest.mark.asyncio
async def test_stdio_client_timeout(tmp_path: Path) -> None:
    """Fixture server has no slow tool, so we test timeout by configuring
    the manifest with a zero-second timeout to force the path."""

    from backend.tom.mcp_bridge.client import McpClientError, StdioMcpClient
    from backend.tom.mcp_bridge.manifest import (
        EntrypointModel,
        McpServerManifest,
    )

    manifest = McpServerManifest(
        name="tom-fixture",
        version="0.1.0",
        description="in-tree test server",
        entrypoint=EntrypointModel(
            command=sys.executable,
            args=["-m", "tests._mcp_fixture_server"],
        ),
        capabilities=["tools"],
        tool_timeout_seconds=0.0001,
    )
    client = StdioMcpClient(manifest)
    try:
        await client.start()
        with pytest.raises(McpClientError):
            await client.call_tool("echo", {"message": "x"})
    finally:
        await client.stop()
