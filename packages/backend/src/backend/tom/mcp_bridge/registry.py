"""MCP server registry (Section 7).

Tracks one :class:`backend.tom.mcp_bridge.client.McpClient` instance
per loaded manifest. Wraps start/stop/health/lifecycle so the rest of
TOM never touches a subprocess directly.

Lifecycle: ``start_all`` at server boot, ``stop_all`` on shutdown.
Tools exposed by every running server are aggregated into a single
catalogue that ``MCPDispatcher`` consumes per turn.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from backend.tom.db.audit import write_audit_log
from backend.tom.db.models import SkillORM, SkillStatus
from backend.tom.db.session import SessionLocal
from backend.tom.mcp_bridge.client import (
    InMemoryMcpClient,
    McpClient,
    McpClientError,
    StdioMcpClient,
)
from backend.tom.mcp_bridge.manifest import McpServerManifest

logger = logging.getLogger(__name__)


@dataclass
class ServerHealth:
    name: str
    running: bool
    last_error: str | None = None
    last_started_at: datetime | None = None
    tool_count: int = 0


@dataclass
class RunningServer:
    manifest: McpServerManifest
    client: McpClient
    health: ServerHealth = field(default_factory=lambda: ServerHealth(name="", running=False))


class ServerRegistry:
    """In-memory :class:`name → RunningServer` map."""

    def __init__(self, *, client_factory: Any = None) -> None:
        self._servers: dict[str, RunningServer] = {}
        # default factory: pick StdioMcpClient; tests can override
        self._client_factory = client_factory or (lambda manifest: StdioMcpClient(manifest))

    def __contains__(self, name: str) -> bool:
        return name in self._servers

    def names(self) -> list[str]:
        return list(self._servers.keys())

    def get(self, name: str) -> McpClient:
        if name not in self._servers:
            msg = f"server {name!r} not registered"
            raise KeyError(msg)
        return self._servers[name].client

    def get_running(self, name: str) -> RunningServer:
        if name not in self._servers:
            msg = f"server {name!r} not registered"
            raise KeyError(msg)
        return self._servers[name]

    def start(self, manifest: McpServerManifest) -> McpClient:
        """Launch one MCP server from its manifest."""
        if manifest.name in self._servers:
            return self._servers[manifest.name].client
        client = self._client_factory(manifest)
        srv = RunningServer(
            manifest=manifest,
            client=client,
            health=ServerHealth(
                name=manifest.name,
                running=False,
            ),
        )
        self._servers[manifest.name] = srv
        # v0.1: non-async entry — caller uses ``start_async`` instead
        return client

    async def start_async(self, manifest: McpServerManifest) -> McpClient:
        """Launch and await the start (suitable for app startup)."""
        if manifest.name in self._servers:
            return self._servers[manifest.name].client
        client = self._client_factory(manifest)
        srv = RunningServer(
            manifest=manifest,
            client=client,
            health=ServerHealth(name=manifest.name, running=False),
        )
        self._servers[manifest.name] = srv
        await self._launch(client, srv)
        return client

    async def start_all_async(self, manifests: list[McpServerManifest]) -> int:
        """Start every server, returning how many came up successfully."""
        ok = 0
        for manifest in manifests:
            if manifest.name in self._servers:
                continue
            client = self._client_factory(manifest)
            srv = RunningServer(
                manifest=manifest,
                client=client,
                health=ServerHealth(name=manifest.name, running=False),
            )
            self._servers[manifest.name] = srv
            try:
                await self._launch(client, srv)
                ok += 1
            except McpClientError as exc:
                logger.warning("server %s failed to start: %s", manifest.name, exc)
                srv.health.running = False
                srv.health.last_error = str(exc)
        return ok

    async def stop(self, name: str) -> None:
        if name not in self._servers:
            return
        srv = self._servers[name]
        try:
            await srv.client.stop()
        except Exception as exc:
            srv.health.last_error = str(exc)
        finally:
            srv.health.running = False
            self._servers.pop(name, None)
        _mark_skill_idle(name)

    async def stop_all(self) -> None:
        names = list(self._servers.keys())
        for name in names:
            await self.stop(name)

    def health(self, name: str) -> ServerHealth:
        if name not in self._servers:
            return ServerHealth(name=name, running=False)
        return self._servers[name].health

    def catalog(self) -> list[dict[str, Any]]:
        """Aggregate metadata across all running servers.

        The dispatcher queries ``list_tools()`` on demand for each
        server; this method returns metadata only (name, version,
        capabilities, tool count).
        """
        out: list[dict[str, Any]] = []
        for srv in self._servers.values():
            out.append(
                {
                    "server": srv.manifest.name,
                    "version": srv.manifest.version,
                    "description": srv.manifest.description,
                    "capabilities": list(srv.manifest.capabilities),
                    "running": srv.health.running,
                    "tool_count": srv.health.tool_count,
                }
            )
        return out

    async def _launch(self, client: McpClient, srv: RunningServer) -> None:
        try:
            await client.start()
        except McpClientError as exc:
            srv.health.running = False
            srv.health.last_error = str(exc)
            write_audit_log(
                action="mcp.disconnect",
                target_type="server",
                target_id=srv.manifest.name,
                payload={"phase": "start", "error": str(exc)},
            )
            raise
        srv.health.running = True
        srv.health.last_started_at = datetime.now(UTC)
        tools = await client.list_tools()
        srv.health.tool_count = len(tools)
        write_audit_log(
            action="mcp.connect",
            target_type="server",
            target_id=srv.manifest.name,
            payload={"tool_count": srv.health.tool_count},
        )


def _mark_skill_idle(skill_or_name: str) -> None:
    s = SessionLocal()
    try:
        row = s.execute(select(SkillORM).where(SkillORM.name == skill_or_name)).scalar_one_or_none()
        if row is not None and row.status is SkillStatus.ACTIVE:
            row.last_invocation_at = datetime.now(UTC)
            s.commit()
    finally:
        s.close()


def in_memory_registry_with(
    manifests: list[McpServerManifest],
    *,
    tools_by_server: dict[str, list[dict[str, Any]]] | None = None,
    handlers_by_server: dict[str, Any] | None = None,
) -> ServerRegistry:
    """Test helper: build a registry backed by :class:`InMemoryMcpClient`.

    Each manifest gets one InMemoryMcpClient. Optional maps supply
    advertised tools + a single dispatch handler per server.
    """
    tools_by_server = tools_by_server or {}
    handlers_by_server = handlers_by_server or {}

    def factory(manifest: McpServerManifest) -> McpClient:
        client = InMemoryMcpClient(
            manifest,
            tools=tools_by_server.get(manifest.name, []),
            handler=handlers_by_server.get(manifest.name),
        )
        return client

    return ServerRegistry(client_factory=factory)


__all__: list[str] = [
    "RunningServer",
    "ServerHealth",
    "ServerRegistry",
    "in_memory_registry_with",
]
