"""MCP stdio client (Section 7).

Wraps the official ``mcp`` Python SDK (vendored as a dependency) to:

- Spawn a server subprocess from a manifest entrypoint
- Open a :class:`mcp.client.session.ClientSession` over stdio
- List advertised tools
- Invoke a tool with a wall-clock timeout
- Tear down on stop

Why an explicit wrapper instead of using the SDK directly in the
dispatcher? Three reasons:

1. Timeouts need to be *per-call*, not configured globally; this
   wrapper layers :func:`asyncio.wait_for` on top of the SDK.
2. The manifest's ``entrypoint`` shape needs translation into the SDK's
   :class:`StdioServerParameters` (env defaults, cwd, etc.).
3. Tests substitute a fake :class:`McpClient` to drive the
   dispatcher without a real subprocess.
"""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from typing import Any, Protocol

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from backend.tom.mcp_bridge.manifest import (
    DEFAULT_TOOL_TIMEOUT_SECONDS,
    McpServerManifest,
)


class McpClient(Protocol):
    """Surface the dispatcher actually depends on.

    The default implementation :class:`StdioMcpClient` runs the manifest
    entrypoint as a subprocess. Tests inject a stub that fakes
    ``list_tools`` and ``call_tool``.
    """

    name: str

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def list_tools(self) -> list[dict[str, Any]]: ...

    async def call_tool(
        self, name: str, arguments: dict[str, Any], *, timeout: float | None = None
    ) -> dict[str, Any]: ...

    def is_running(self) -> bool: ...


class McpClientError(RuntimeError):
    """Raised when a tool call times out or returns an error payload."""


class StdioMcpClient:
    """Spawn-and-talk implementation that uses the official ``mcp`` SDK."""

    def __init__(self, manifest: McpServerManifest) -> None:
        self._manifest = manifest
        self.name = manifest.name
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None
        self._running = False

    def _params(self) -> StdioServerParameters:
        ep = self._manifest.entrypoint
        return StdioServerParameters(
            command=ep.command,
            args=list(ep.args),
            env=ep.env or None,
            cwd=ep.cwd,
        )

    def is_running(self) -> bool:
        return self._running and self._session is not None

    async def start(self) -> None:
        if self.is_running():
            return
        stack = AsyncExitStack()
        try:
            read, write = await stack.enter_async_context(stdio_client(self._params()))
            session = ClientSession(read, write)
            await stack.enter_async_context(session)
            await session.initialize()
        except Exception:
            await stack.aclose()
            raise
        self._stack = stack
        self._session = session
        self._running = True

    async def stop(self) -> None:
        if self._stack is None:
            return
        try:
            await self._stack.aclose()
        finally:
            self._stack = None
            self._session = None
            self._running = False

    async def list_tools(self) -> list[dict[str, Any]]:
        self._ensure_ready()
        assert self._session is not None
        result = await self._session.list_tools()
        out: list[dict[str, Any]] = []
        for tool in result.tools:
            out.append(
                {
                    "name": tool.name,
                    "description": tool.description or "",
                    "parameters": tool.inputSchema or {},
                }
            )
        return out

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        self._ensure_ready()
        assert self._session is not None
        effective_timeout = timeout if timeout is not None else self._manifest.tool_timeout_seconds
        if effective_timeout <= 0:
            effective_timeout = DEFAULT_TOOL_TIMEOUT_SECONDS
        try:
            result = await asyncio.wait_for(
                self._session.call_tool(name, arguments or {}),
                timeout=effective_timeout,
            )
        except TimeoutError as exc:
            msg = f"tool {name!r} on {self.name!r} timed out after {effective_timeout}s"
            raise McpClientError(msg) from exc
        if getattr(result, "isError", False):
            content = getattr(result, "content", None)
            msg = f"tool {name!r} on {self.name!r} returned error: {content!r}"
            raise McpClientError(msg)
        content = getattr(result, "content", None)
        return {"content": _content_to_jsonable(content), "structured": None}

    def _ensure_ready(self) -> None:
        if not self.is_running():
            msg = f"MCP server {self.name!r} is not running"
            raise McpClientError(msg)


def _content_to_jsonable(content: object) -> object:
    """Best-effort cast for the structured content MCP servers return."""
    if content is None:
        return None
    if isinstance(content, list):
        out: list[object] = []
        for item in content:
            out.append(_content_to_jsonable(item))
        return out
    if isinstance(content, (str, int, float, bool)):
        return content
    text = getattr(content, "text", None)
    if isinstance(text, str):
        return text
    data = getattr(content, "data", None)
    if data is not None:
        return data
    return str(content)


class InMemoryMcpClient:
    """Test-only McpClient that simulates a server without a subprocess.

    Either pre-populates tool list + handlers, or fails closed.
    """

    def __init__(
        self,
        manifest: McpServerManifest,
        tools: list[dict[str, Any]] | None = None,
        handler: Any = None,
    ) -> None:
        self._manifest = manifest
        self._tools = tools or []
        self._handler = handler
        self.name = manifest.name
        self._running = False
        self.calls: list[tuple[str, dict[str, Any], float | None]] = []

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    def is_running(self) -> bool:
        return self._running

    async def list_tools(self) -> list[dict[str, Any]]:
        if not self.is_running():
            msg = f"client {self.name!r} not started"
            raise McpClientError(msg)
        return list(self._tools)

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        self.calls.append((name, arguments, timeout))
        if self._handler is None:
            msg = f"no handler registered for {name!r}"
            raise McpClientError(msg)
        result: dict[str, Any] = await self._handler(name, arguments)
        return result


__all__: list[str] = [
    "InMemoryMcpClient",
    "McpClient",
    "McpClientError",
    "StdioMcpClient",
]
