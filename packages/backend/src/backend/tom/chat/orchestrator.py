"""Chat orchestrator — composes memory, instructions, providers, and tool
dispatch into a single async generator of ``ChatEvent`` rows.

The orchestrator is the only thing in TOM that knows how a chat turn
flows. The FastAPI layer (§6) and the playground REPL (§8) both talk
to it; nothing else inside TOM depends on it directly.

Public surface:

- :class:`ChatTurnRequest` — what the API sends
- :class:`ChatEvent` — what the API streams to the client
- :class:`ChatOrchestrator` — the runner itself

Side effects (all per-turn):

- INSERT into ``messages`` (user + assistant)
- UPDATE ``sessions`` (``total_tokens``)
- INSERT into ``audit_log`` (start / done / error)
- INSERT into ``memory_records`` (core snapshot + recall push)

Concurrency:

- :class:`SessionLocks` serialises turns per-session; turns on
  different sessions are independent.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from backend.tom.chat.locks import SessionLocks
from backend.tom.chat.tokens import estimate_tokens
from backend.tom.chat.tool_dispatcher import StubDispatcher, ToolDispatcher
from backend.tom.db.audit import write_audit_log
from backend.tom.db.models import MessageORM, SessionORM, SessionStatus
from backend.tom.db.session import SessionLocal
from backend.tom.instructions.loader import render_prompt
from backend.tom.memory.tom_memory import TomMemory
from backend.tom.providers.base import Message, Provider, ToolCall, ToolDef
from backend.tom.providers.registry import (
    ProviderRegistry,
)

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 5
HISTORY_WINDOW = 40  # last N DB messages to attach as context


@dataclass
class ChatTurnRequest:
    session_id: str
    user_content: str
    attachments: list[dict[str, object]] = field(default_factory=list)


@dataclass
class ChatEvent:
    """A single SSE event yielded by the orchestrator.

    ``type`` is one of:

    - ``session_required`` — session id missing/unknown, no events follow
    - ``tool_call`` — model-emitted tool call before dispatch
    - ``tool_result`` — dispatcher response paired with a prior tool_call
    - ``token`` — text delta (continuous stream)
    - ``done`` — turn ended cleanly; payload has ``message_id`` + tokens
    - ``error`` — turn aborted; payload has ``reason`` + optional detail
    """

    type: str
    payload: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {"type": self.type, "payload": dict(self.payload)}


@dataclass
class _TurnContext:
    session: SessionORM
    user_message_id: str
    assistant_message_id: str


class ChatOrchestrator:
    """Run a single chat turn end-to-end."""

    def __init__(
        self,
        *,
        memory: TomMemory | None = None,
        providers: ProviderRegistry | None = None,
        dispatcher: ToolDispatcher | None = None,
        instructions_loader: Callable[[], str] | None = None,
        locks: SessionLocks | None = None,
        max_tool_rounds: int = MAX_TOOL_ROUNDS,
        history_window: int = HISTORY_WINDOW,
    ) -> None:
        self._memory = memory or TomMemory()
        self._providers = providers or ProviderRegistry()
        self._dispatcher: ToolDispatcher = dispatcher or StubDispatcher()
        self._instructions_loader = instructions_loader or (
            lambda: render_prompt(user_override=None)
        )
        self._locks = locks or SessionLocks()
        self._max_tool_rounds = max_tool_rounds
        self._history_window = history_window

    @property
    def locks(self) -> SessionLocks:
        return self._locks

    async def chat(self, req: ChatTurnRequest) -> AsyncIterator[ChatEvent]:
        """Yield ``ChatEvent`` rows for one turn. Never raises."""
        async with self._locks.acquire(req.session_id):
            ctx = await self._begin_turn(req)
            if isinstance(ctx, ChatEvent):
                # propagated error
                yield ctx
                return
            try:
                async for ev in self._run_turn(req, ctx):
                    yield ev
            except Exception as exc:
                logger.exception("chat turn failed")
                write_audit_log(
                    action="chat.error",
                    target_type="session",
                    target_id=req.session_id,
                    payload={"error": f"{type(exc).__name__}: {exc}"},
                )
                yield ChatEvent(
                    type="error",
                    payload={"reason": "internal_error", "detail": str(exc)},
                )

    async def _begin_turn(self, req: ChatTurnRequest) -> _TurnContext | ChatEvent:
        """Load session + write the user message; return context or error event."""
        s = SessionLocal()
        try:
            sess = s.execute(
                select(SessionORM).where(SessionORM.id == req.session_id)
            ).scalar_one_or_none()
            if sess is None:
                return ChatEvent(
                    type="session_required",
                    payload={"reason": "session_not_found", "session_id": req.session_id},
                )
            if sess.status is SessionStatus.CLOSED:
                return ChatEvent(
                    type="session_required",
                    payload={"reason": "session_closed", "session_id": req.session_id},
                )
            user_msg = MessageORM(
                session_id=req.session_id,
                role="user",
                content=req.user_content,
                tool_calls={"attachments": req.attachments} if req.attachments else None,
            )
            s.add(user_msg)
            s.commit()
            s.refresh(user_msg)
            assistant_msg = MessageORM(
                session_id=req.session_id,
                role="assistant",
                content="",
            )
            s.add(assistant_msg)
            s.commit()
            s.refresh(assistant_msg)
            write_audit_log(
                action="chat.start",
                target_type="session",
                target_id=req.session_id,
                payload={"user_message_id": user_msg.id},
            )
            return _TurnContext(
                session=sess,
                user_message_id=user_msg.id,
                assistant_message_id=assistant_msg.id,
            )
        finally:
            s.close()

    async def _run_turn(self, req: ChatTurnRequest, ctx: _TurnContext) -> AsyncIterator[ChatEvent]:
        """The streaming loop. Yields ChatEvent rows."""
        provider = self._resolve_provider(ctx.session)
        if isinstance(provider, ChatEvent):
            yield provider
            return
        tool_defs = await self._tool_defs()
        history = self._load_history(req.session_id)
        core_memory_text = self._format_core_memory()

        messages: list[Message] = [
            Message(role="system", content=f"{self._instructions_loader()}\n\n{core_memory_text}")
        ]
        messages.extend(history)
        messages.append(Message(role="user", content=req.user_content))

        tool_rounds = 0
        text_buffer: list[str] = []
        streamed_text = ""
        while True:
            streamed_text = ""
            tool_calls_in_turn: list[ToolCall] = []
            finish: str | None = None
            async for chunk in provider.chat(
                messages,
                tools=tool_defs or None,
                temperature=0.7,
            ):
                if chunk.text_delta:
                    streamed_text += chunk.text_delta
                    yield ChatEvent(
                        type="token",
                        payload={"text_delta": chunk.text_delta},
                    )
                if chunk.tool_calls:
                    tool_calls_in_turn.extend(chunk.tool_calls)
                    for tc in chunk.tool_calls:
                        yield ChatEvent(
                            type="tool_call",
                            payload={
                                "id": tc.id,
                                "name": tc.name,
                                "arguments": tc.arguments,
                            },
                        )
                if chunk.finish_reason is not None:
                    finish = chunk.finish_reason
            text_buffer.append(streamed_text)

            if not tool_calls_in_turn:
                if finish is not None:
                    pass  # final turn — break below
                break

            # Pause here: §7 will wire the dispatcher. v0.1 stops after
            # surfacing tool_calls and labels the turn finished with
            # finish_reason="tool_calls" but no execution.
            if not tool_defs:
                break

            tool_rounds += 1
            if tool_rounds > self._max_tool_rounds:
                yield ChatEvent(
                    type="error",
                    payload={"reason": "tool_loop_exceeded", "max_rounds": self._max_tool_rounds},
                )
                break

            tool_messages: list[Message] = []
            for call in tool_calls_in_turn:
                try:
                    result = await self._dispatcher.dispatch(call)
                except Exception as exc:
                    yield ChatEvent(
                        type="tool_result",
                        payload={
                            "id": call.id,
                            "name": call.name,
                            "error": f"{type(exc).__name__}: {exc}",
                        },
                    )
                    tool_messages.append(
                        Message(
                            role="tool",
                            content=f"ERROR: {exc}",
                            tool_call_id=call.id,
                        )
                    )
                    continue
                yield ChatEvent(
                    type="tool_result",
                    payload={"id": call.id, "name": call.name, "result": result},
                )
                import json as _json

                tool_messages.append(
                    Message(
                        role="tool",
                        content=_json.dumps(result, ensure_ascii=False),
                        tool_call_id=call.id,
                    )
                )
            messages.append(
                Message(
                    role="assistant",
                    content=streamed_text,
                    tool_calls=tool_calls_in_turn,
                )
            )
            messages.extend(tool_messages)

        # Persist assistant message + bump total_tokens + audit
        self._finalize(
            req.session_id,
            ctx.assistant_message_id,
            "".join(text_buffer),
        )
        yield ChatEvent(
            type="done",
            payload={
                "user_message_id": ctx.user_message_id,
                "assistant_message_id": ctx.assistant_message_id,
                "total_tokens": self._session_total_tokens(req.session_id),
            },
        )

    def _resolve_provider(self, sess: SessionORM) -> Provider | ChatEvent:
        if sess.provider:
            try:
                return self._providers.get(sess.provider)
            except (LookupError, PermissionError) as exc:
                return ChatEvent(
                    type="error",
                    payload={"reason": "provider_unavailable", "detail": str(exc)},
                )
        try:
            return self._providers.get_default()
        except LookupError:
            return ChatEvent(
                type="error",
                payload={"reason": "no_default_provider"},
            )

    async def _tool_defs(self) -> list[ToolDef]:
        """Advertise tools to the model if a dispatcher has any."""
        try:
            advertised: list[dict[str, Any]] = await self._dispatcher.tools()
        except Exception:
            return []
        out: list[ToolDef] = []
        for entry in advertised:
            try:
                out.append(
                    ToolDef(
                        name=str(entry.get("name", "")),
                        description=str(entry.get("description", "")),
                        parameters=dict(entry.get("parameters") or {}),
                    )
                )
            except Exception:
                continue
        return out

    def _load_history(self, session_id: str) -> list[Message]:
        s = SessionLocal()
        try:
            rows = (
                s.execute(
                    select(MessageORM)
                    .where(MessageORM.session_id == session_id)
                    .order_by(MessageORM.created_at.desc())
                    .limit(self._history_window)
                )
                .scalars()
                .all()
            )
        finally:
            s.close()
        rows = list(reversed(rows))  # oldest-first
        out: list[Message] = []
        for r in rows:
            if r.role == "user" and not r.content:
                continue
            out.append(Message(role=r.role, content=r.content))  # type: ignore[arg-type]
        return out

    def _format_core_memory(self) -> str:
        try:
            core = self._memory.read_core()
        except Exception:
            return "## core memory\n(unavailable)"
        blocks_text = "\n".join(f"- {b.label}: {b.text}" for b in core.blocks) or "(no blocks)"
        facts_text = "\n".join(f"- {f}" for f in core.facts) or "(no facts)"
        return (
            f"## core memory (version={core.version})\n"
            f"### blocks\n{blocks_text}\n"
            f"### facts\n{facts_text}\n"
        )

    def _finalize(self, session_id: str, assistant_message_id: str, content: str) -> None:
        s = SessionLocal()
        try:
            row = s.get(MessageORM, assistant_message_id)
            if row is not None:
                row.content = content
                row.tokens = estimate_tokens(content)
            sess = s.get(SessionORM, session_id)
            if sess is not None:
                sess.total_tokens = (sess.total_tokens or 0) + estimate_tokens(content)
                sess.updated_at = datetime.now(UTC)
            # Audit trail + recall push happen in a follow-up step; for
            # v0.1 we only need to commit the assistant row + token update.
            s.commit()
            write_audit_log(
                action="chat.done",
                target_type="session",
                target_id=session_id,
                payload={
                    "assistant_message_id": assistant_message_id,
                    "tokens": estimate_tokens(content),
                },
            )
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    def _session_total_tokens(self, session_id: str) -> int:
        s = SessionLocal()
        try:
            sess = s.get(SessionORM, session_id)
            return int(sess.total_tokens) if sess else 0
        finally:
            s.close()


__all__: list[str] = [
    "ChatEvent",
    "ChatOrchestrator",
    "ChatTurnRequest",
]


def session_total_tokens_for(session_id: str) -> int:
    """Read-only helper for the GET /v1/sessions/{id} response."""
    s = SessionLocal()
    try:
        sess = s.get(SessionORM, session_id)
        return int(sess.total_tokens) if sess else 0
    finally:
        s.close()
