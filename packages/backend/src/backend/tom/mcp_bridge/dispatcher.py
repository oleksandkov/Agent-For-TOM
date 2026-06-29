"""MCP tool dispatcher (Section 7).

Implements the :class:`backend.tom.chat.tool_dispatcher.ToolDispatcher`
Protocol so :class:`ChatOrchestrator` can route ``tool_calls`` emitted
by the LLM into the right MCP server.

Every invocation is recorded via :func:`write_audit_log` with action
``mcp.invoke`` and a payload containing the tool name + arguments
(sanitised in §11 hardening) + result status.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from backend.tom.chat.tool_dispatcher import ToolDispatchError
from backend.tom.db.audit import write_audit_log
from backend.tom.mcp_bridge.client import McpClient, McpClientError
from backend.tom.mcp_bridge.registry import ServerRegistry
from backend.tom.providers.base import ToolCall


class MCPDispatcher:
    """Routes :class:`ToolCall` rows into the correct :class:`McpClient`."""

    def __init__(self, registry: ServerRegistry) -> None:
        self._registry = registry

    async def _all_tools(self) -> dict[tuple[str, str], dict[str, Any]]:
        """Return ``{(server_name, tool_name): tool_dict}``."""
        catalogue: dict[tuple[str, str], dict[str, Any]] = {}
        for name in self._registry.names():
            try:
                client = self._registry.get(name)
                health = self._registry.health(name)
                if not health.running:
                    continue
                tools = await client.list_tools()
            except McpClientError:
                continue
            for tool in tools:
                catalogue[(name, tool["name"])] = tool
        return catalogue

    async def tools(self) -> list[dict[str, Any]]:
        """Tools the model can pick from, each tagged with its server."""
        catalogue = await self._all_tools()
        items: list[dict[str, Any]] = []
        for (server_name, _tool_name), tool in sorted(catalogue.items()):
            items.append({**tool, "server": server_name})
        return items

    async def dispatch(self, call: ToolCall) -> dict[str, Any]:
        """Invoke ``call.name`` on the server that advertised it."""
        target = await self._resolve_target(call.name)
        if target is None:
            msg = f"no MCP tool registered with name={call.name!r}"
            self._audit(call, success=False, error=msg)
            raise ToolDispatchError(msg)
        server_name, client = target
        start = datetime.now(UTC)
        try:
            result = await client.call_tool(call.name, dict(call.arguments))
        except McpClientError as exc:
            elapsed_ms = (datetime.now(UTC) - start).total_seconds() * 1000.0
            self._audit(
                call,
                success=False,
                error=str(exc),
                server=server_name,
                elapsed_ms=elapsed_ms,
            )
            raise ToolDispatchError(str(exc)) from exc
        elapsed_ms = (datetime.now(UTC) - start).total_seconds() * 1000.0
        self._audit(
            call,
            success=True,
            server=server_name,
            elapsed_ms=elapsed_ms,
        )
        return result

    async def _resolve_target(self, tool_name: str) -> tuple[str, McpClient] | None:
        catalogue = await self._all_tools()
        for server_name, t_name in catalogue:
            if t_name == tool_name:
                try:
                    return server_name, self._registry.get(server_name)
                except KeyError:
                    return None
        return None

    def _audit(
        self,
        call: ToolCall,
        *,
        success: bool,
        server: str | None = None,
        error: str | None = None,
        elapsed_ms: float | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "tool": call.name,
            "arguments": dict(call.arguments),
            "id": call.id,
            "success": success,
        }
        if server is not None:
            payload["server"] = server
        if error is not None:
            payload["error"] = error
        if elapsed_ms is not None:
            payload["elapsed_ms"] = round(elapsed_ms, 2)
        write_audit_log(
            action="tool.invoke" if success else "tool.invoke.error",
            actor="orchestrator",
            target_type="tool",
            target_id=f"{server or '?'}/{call.name}",
            payload=payload,
        )


__all__: list[str] = ["MCPDispatcher"]
