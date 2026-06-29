"""Chat subsystem (orchestrator + tokens + locks + dispatcher).

Composition of the v0.1 turn loop:

1. Resolve session + lock (per-:class:`SessionLocks`).
2. Read core memory from the DB; render into the system prompt.
3. Drain any pending recall buffer into the prompt's recent context.
4. Append ``[user message]`` and call ``provider.chat(...)``.
5. Stream ``ChatEvent`` rows to the API layer:
   - ``token`` — text delta (and optional ``tool_call`` chunks)
   - ``tool_call`` — model-emitted call before/after dispatch
   - ``done`` — turn complete; carries assistant message id + tokens
   - ``error`` — failure; carries a short reason
6. After the turn, write ``messages`` rows, bump ``total_tokens``,
   write ``audit_log`` rows, push the user message into recall.

§7 will plug a real :class:`ToolDispatcher` in here. v0.1 surfaces
tool_calls as events without executing them.
"""

from __future__ import annotations

from backend.tom.chat.locks import SessionLocks
from backend.tom.chat.orchestrator import (
    ChatEvent,
    ChatOrchestrator,
    ChatTurnRequest,
)
from backend.tom.chat.tokens import estimate_messages, estimate_tokens
from backend.tom.chat.tool_dispatcher import (
    StubDispatcher,
    ToolDispatcher,
    ToolDispatchError,
)

__all__: list[str] = [
    "ChatEvent",
    "ChatOrchestrator",
    "ChatTurnRequest",
    "SessionLocks",
    "StubDispatcher",
    "ToolDispatchError",
    "ToolDispatcher",
    "estimate_messages",
    "estimate_tokens",
]
