"""`POST /v1/chat` — Server-Sent Events streaming turn (Section 6).

Request body::

    {
      "session_id": "...uuid...",
      "content": "user message",
      "attachments": [...]      // optional, v0.1: ignored but recorded
    }

Response: ``text/event-stream`` of:

    event: session_required
    data: {"reason": "...", ...}

    event: token
    data: {"text_delta": "..."}

    event: tool_call
    data: {"id": "...", "name": "...", "arguments": {...}}

    event: tool_result
    data: {"id": "...", "name": "...", "result": {...} | "error": "..."}

    event: done
    data: {"user_message_id": "...", "assistant_message_id": "...", "total_tokens": N}

    event: error
    data: {"reason": "...", "detail": "..."}
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from backend.tom.api.deps import get_orchestrator
from backend.tom.chat import ChatEvent, ChatOrchestrator, ChatTurnRequest

router = APIRouter(prefix="/v1", tags=["chat"])


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(..., min_length=1)
    content: str = Field(..., min_length=1)
    attachments: list[dict[str, object]] = Field(default_factory=list)


def _format_event(ev: ChatEvent) -> str:
    out = ev.to_dict()
    payload = out["payload"]
    return f"event: {ev.type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _stream(orchestrator: ChatOrchestrator, req: ChatTurnRequest) -> AsyncIterator[str]:
    async for ev in orchestrator.chat(req):
        yield _format_event(ev)


@router.post("/chat")
async def post_chat(
    body: ChatRequest,
    orchestrator: Annotated[ChatOrchestrator, Depends(get_orchestrator)],
) -> StreamingResponse:
    req = ChatTurnRequest(
        session_id=body.session_id,
        user_content=body.content,
        attachments=list(body.attachments),
    )
    return StreamingResponse(
        _stream(orchestrator, req),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


__all__: list[str] = ["router"]
