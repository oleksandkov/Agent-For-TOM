"""TOM HTTP API surface (chat, sessions, messages, memory, providers).

Section 6 lands ``POST /v1/chat`` + ``/v1/sessions*`` + ``/v1/sessions/{id}/messages``.
Memory (§4) and providers (§5) routers are mounted alongside.
"""

from __future__ import annotations

from backend.tom.api import chat as chat_module
from backend.tom.api import messages as messages_module
from backend.tom.api import sessions as sessions_module

__all__: list[str] = [
    "chat_module",
    "messages_module",
    "sessions_module",
]
