"""TOM MCP (Model Context Protocol) bridge — client + server lifecycle.

Section 7 surfaces:

- :mod:`backend.tom.mcp_bridge.manifest` — schema, Pydantic model, loader
- :mod:`backend.tom.mcp_bridge.client` — stdio + in-memory McpClient
- :mod:`backend.tom.mcp_bridge.loader` — scans ``data_dir/skills/{builtin,generated}``
- :mod:`backend.tom.mcp_bridge.registry` — process-wide name→server map
- :mod:`backend.tom.mcp_bridge.dispatcher` — :class:`ToolDispatcher` impl

Public re-exports below.
"""

from __future__ import annotations

from backend.tom.mcp_bridge.client import (
    InMemoryMcpClient,
    McpClient,
    McpClientError,
    StdioMcpClient,
)
from backend.tom.mcp_bridge.dispatcher import MCPDispatcher
from backend.tom.mcp_bridge.loader import (
    discover_builtin,
    discover_generated,
    load_all,
)
from backend.tom.mcp_bridge.manifest import (
    DEFAULT_TOOL_TIMEOUT_SECONDS,
    ManifestError,
    McpServerManifest,
    manifest_json_schema,
)
from backend.tom.mcp_bridge.registry import (
    RunningServer,
    ServerHealth,
    ServerRegistry,
    in_memory_registry_with,
)

__all__: list[str] = [
    "DEFAULT_TOOL_TIMEOUT_SECONDS",
    "InMemoryMcpClient",
    "MCPDispatcher",
    "ManifestError",
    "McpClient",
    "McpClientError",
    "McpServerManifest",
    "RunningServer",
    "ServerHealth",
    "ServerRegistry",
    "StdioMcpClient",
    "discover_builtin",
    "discover_generated",
    "in_memory_registry_with",
    "load_all",
    "manifest_json_schema",
]
