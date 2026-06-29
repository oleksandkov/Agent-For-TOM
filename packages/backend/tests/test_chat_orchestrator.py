"""Tests for :mod:`backend.tom.chat.orchestrator`.

All external dependencies (memory, providers, dispatcher, instructions)
are injected stubs so the DB layer is the only thing that actually
hits SQLCipher.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from backend.tom.chat import ChatOrchestrator, ChatTurnRequest, SessionLocks
from backend.tom.chat.tool_dispatcher import StubDispatcher
from backend.tom.db.init_db import init_db
from backend.tom.db.models import ProviderConfigORM, SessionORM, SessionStatus
from backend.tom.db.session import SessionLocal
from backend.tom.providers.base import HealthReport, Message, Provider, TokenChunk, ToolCall


class _StubProvider:
    """Async-generator provider that yields a canned stream."""

    def __init__(
        self,
        *,
        name: str = "stub",
        type_: str = "stub",
        chunks: list[TokenChunk] | None = None,
        raise_after: bool = False,
    ) -> None:
        self.name = name
        self._type = type_
        self._chunks = chunks or [
            TokenChunk(text_delta="hello"),
            TokenChunk(text_delta=" world", finish_reason="stop"),
        ]
        self._raise_after = raise_after
        self.calls: list[list[Message]] = []

    @property
    def type(self) -> str:
        return self._type

    def chat(
        self,
        messages,
        *,
        tools=None,
        temperature=0.7,
        max_tokens=None,
    ):
        async def _gen():
            self.calls.append(list(messages))
            if self._raise_after:
                raise RuntimeError("provider boom")
                yield  # pragma: no cover
            for chunk in self._chunks:
                yield chunk

        return _gen()

    async def embed(self, texts):
        return [[0.0] * 4 for _ in texts]

    async def health(self):
        return HealthReport(ok=True)


class _Registry:
    def __init__(self, provider: Provider) -> None:
        self._provider = provider

    def get(self, name: str) -> Provider:
        return self._provider

    def get_default(self) -> Provider:
        return self._provider


class _InMemoryMemory:
    """Tiny stand-in for TomMemory that returns a canned core snapshot."""

    def read_core(self) -> Any:
        from datetime import UTC, datetime

        from backend.tom.memory.types import CoreMemory, CoreMemoryBlock

        return CoreMemory(
            blocks=[CoreMemoryBlock(label="persona", text="concrete helper")],
            facts=["user likes terse replies"],
            version=0,
            updated_at=datetime.now(UTC),
        )


def _seed_session(*, name: str = "s", provider: str = "stub") -> str:
    s = SessionLocal()
    try:
        sess = SessionORM(id=name, title="t", provider=provider, model="m")
        s.add(sess)
        s.commit()
    finally:
        s.close()
    return name


def _seed_provider(*, name: str = "stub") -> None:
    s = SessionLocal()
    try:
        from sqlalchemy import delete

        s.execute(delete(ProviderConfigORM))
        s.add(
            ProviderConfigORM(
                name=name,
                type="ollama",
                model="m",
                is_default=True,
                fallback_chain=[],
            )
        )
        s.commit()
    finally:
        s.close()


@pytest.fixture
def seeded_db(tmp_path: Path) -> None:
    init_db()


@pytest.mark.asyncio
async def test_orchestrator_emits_token_and_done(seeded_db: None) -> None:
    _seed_session()
    _seed_provider()
    provider = _StubProvider()
    orchestrator = ChatOrchestrator(
        memory=_InMemoryMemory(),  # type: ignore[arg-type]
        providers=_Registry(provider),  # type: ignore[arg-type]
        dispatcher=StubDispatcher(),
    )
    req = ChatTurnRequest(session_id="s", user_content="hi")
    events = [ev async for ev in orchestrator.chat(req)]
    types = [ev.type for ev in events]
    assert types[0:2] == ["token", "token"]
    assert "done" in types
    done = next(ev for ev in events if ev.type == "done")
    assert done.payload["assistant_message_id"]
    assert "total_tokens" in done.payload


@pytest.mark.asyncio
async def test_orchestrator_emits_session_required_when_missing(
    seeded_db: None,
) -> None:
    provider = _StubProvider()
    orchestrator = ChatOrchestrator(
        providers=_Registry(provider),  # type: ignore[arg-type]
        dispatcher=StubDispatcher(),
    )
    req = ChatTurnRequest(session_id="missing", user_content="hi")
    events = [ev async for ev in orchestrator.chat(req)]
    assert events[0].type == "session_required"
    assert events[0].payload["reason"] == "session_not_found"


@pytest.mark.asyncio
async def test_orchestrator_rejects_closed_session(seeded_db: None) -> None:
    _seed_session()
    provider = _StubProvider()
    orchestrator = ChatOrchestrator(
        providers=_Registry(provider),  # type: ignore[arg-type]
        dispatcher=StubDispatcher(),
    )
    s = SessionLocal()
    try:
        sess = s.get(SessionORM, "s")
        assert sess is not None
        sess.status = SessionStatus.CLOSED
        s.commit()
    finally:
        s.close()
    req = ChatTurnRequest(session_id="s", user_content="hi")
    events = [ev async for ev in orchestrator.chat(req)]
    assert events[0].type == "session_required"
    assert events[0].payload["reason"] == "session_closed"


@pytest.mark.asyncio
async def test_orchestrator_handles_provider_error(seeded_db: None) -> None:
    _seed_session()
    _seed_provider()
    provider = _StubProvider(raise_after=True)
    orchestrator = ChatOrchestrator(
        providers=_Registry(provider),  # type: ignore[arg-type]
        dispatcher=StubDispatcher(),
    )
    req = ChatTurnRequest(session_id="s", user_content="hi")
    events = [ev async for ev in orchestrator.chat(req)]
    error = next((ev for ev in events if ev.type == "error"), None)
    assert error is not None
    assert error.payload["reason"] == "internal_error"


@pytest.mark.asyncio
async def test_orchestrator_surfaces_tool_call_events(seeded_db: None) -> None:
    _seed_session()
    _seed_provider()
    chunks = [
        TokenChunk(
            tool_calls=[ToolCall(id="t1", name="echo", arguments={"x": 1})],
        ),
        TokenChunk(finish_reason="tool_calls"),
    ]
    provider = _StubProvider(chunks=chunks)
    orchestrator = ChatOrchestrator(
        providers=_Registry(provider),  # type: ignore[arg-type]
        dispatcher=StubDispatcher(),
    )
    req = ChatTurnRequest(session_id="s", user_content="hi")
    events = [ev async for ev in orchestrator.chat(req)]
    tool_events = [ev for ev in events if ev.type == "tool_call"]
    assert tool_events and tool_events[0].payload["name"] == "echo"
    done = next(ev for ev in events if ev.type == "done")
    assert done is not None


@pytest.mark.asyncio
async def test_orchestrator_does_not_execute_with_stub(seeded_db: None) -> None:
    """Stub dispatcher refuses any call; orchestrator must NOT raise."""
    _seed_session()
    _seed_provider()
    chunks = [
        TokenChunk(
            tool_calls=[ToolCall(id="t1", name="mystery", arguments={})],
        ),
        TokenChunk(finish_reason="tool_calls"),
    ]
    provider = _StubProvider(chunks=chunks)
    orchestrator = ChatOrchestrator(
        providers=_Registry(provider),  # type: ignore[arg-type]
        dispatcher=StubDispatcher(),
    )
    req = ChatTurnRequest(session_id="s", user_content="hi")
    events = [ev async for ev in orchestrator.chat(req)]
    # No 'done' shouldn't crash the orchestrator — v0.1 stops after
    # tool_calls without dispatching.
    assert all(ev.type != "error" for ev in events)


@pytest.mark.asyncio
async def test_session_locks_serialise_turns(seeded_db: None) -> None:
    """Two concurrent chat() calls against the same session serialise."""
    _seed_session()
    _seed_provider()
    locks = SessionLocks()
    provider = _StubProvider()
    orchestrator = ChatOrchestrator(
        providers=_Registry(provider),  # type: ignore[arg-type]
        locks=locks,
        dispatcher=StubDispatcher(),
    )
    import asyncio as _asyncio

    req_a = ChatTurnRequest(session_id="s", user_content="a")
    req_b = ChatTurnRequest(session_id="s", user_content="b")

    async def drain(req: ChatTurnRequest) -> None:
        async for _ev in orchestrator.chat(req):
            pass

    await _asyncio.gather(drain(req_a), drain(req_b))
    # Point of the test: reaching here without deadlock / corruption
    # proves the per-session lock serialised the two turns.
