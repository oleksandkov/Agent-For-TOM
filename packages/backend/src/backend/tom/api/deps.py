"""FastAPI dependency overrides for chat, sessions, and messages.

Tests inject fakes via ``app.dependency_overrides[get_orchestrator] = ...``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from backend.tom.chat import ChatOrchestrator

if TYPE_CHECKING:
    pass


_orchestrator: ChatOrchestrator = ChatOrchestrator()


def get_orchestrator() -> ChatOrchestrator:
    return _orchestrator


def set_orchestrator(instance: ChatOrchestrator) -> None:
    global _orchestrator
    _orchestrator = instance


__all__: list[str] = ["get_orchestrator", "set_orchestrator"]
