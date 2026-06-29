"""Tool dispatcher abstraction.

§6 orchestrates a tool-call loop on top of the provider's streaming
output, but the actual call execution lives in the MCP bridge (§7).
This module defines the seam:

- :class:`ToolDispatcher` — Protocol with ``dispatch(tool_call) -> result``
- :class:`StubDispatcher` — used by §6 tests and by the standalone
  orchestrator when no MCP servers are registered (§7 wires the real
  one in via the same Protocol)

Keeping the two interchangeable means §6 can land first without §7,
and tests don't need a running MCP server.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Protocol, runtime_checkable

from backend.tom.providers.base import ToolCall


@runtime_checkable
class ToolDispatcher(Protocol):
    """Look up a tool by name and execute it.

    Returns a JSON-serialisable result payload, or raises
    :class:`ToolDispatchError`. Empty ``tools`` means no dispatcher is
    registered and tool calls should not be issued.
    """

    async def tools(self) -> list[dict[str, Any]]:
        """Return the advertised tool catalogue (for the system prompt)."""
        ...

    async def dispatch(self, call: ToolCall) -> dict[str, Any]:
        """Execute a single tool call. Returns the result payload.

        Implementations are responsible for permissions and timeouts.
        """
        ...


class ToolDispatchError(RuntimeError):
    """Raised when a tool is not registered, times out, or fails."""


class StubDispatcher:
    """No-op dispatcher; useful for §6 tests until §7 lands."""

    def __init__(self, known_tools: Iterable[dict[str, Any]] | None = None) -> None:
        self._tools = list(known_tools or ())

    async def tools(self) -> list[dict[str, Any]]:
        return list(self._tools)

    async def dispatch(self, call: ToolCall) -> dict[str, Any]:
        raise ToolDispatchError(f"no tool dispatcher wired (stub): cannot execute {call.name!r}")


__all__: list[str] = ["StubDispatcher", "ToolDispatchError", "ToolDispatcher"]
