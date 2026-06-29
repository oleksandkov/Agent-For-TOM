"""Test fixture MCP server.

Launched as a subprocess by ``test_mcp_e2e.py``. Exposes two tools
(``echo`` and ``ping``) over the official MCP stdio transport.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("tom-fixture")


@mcp.tool()
def echo(message: str) -> str:
    """Return the message verbatim."""
    return message


@mcp.tool()
def ping() -> str:
    """Liveness probe."""
    return "pong"


if __name__ == "__main__":
    mcp.run(transport="stdio")
