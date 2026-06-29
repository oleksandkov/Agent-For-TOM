"""Google Gemini (Generative Language) provider.

Direct HTTP via ``httpx`` — no Google SDK required for v0.1. Auth is the
``key`` query parameter; ``generateContent`` is called with SSE-style
``alt=sse`` for streaming.

Embeddings route to ``models/{model}:embedContent`` (single text per
call; batched by TOM into sequential requests — most relevant models
expose this endpoint).
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx

from backend.tom.providers.base import (
    HealthReport,
    Message,
    Provider,
    TokenChunk,
    ToolCall,
    ToolDef,
)

DEFAULT_GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"


class GoogleProvider:
    def __init__(
        self,
        *,
        name: str,
        model: str,
        api_key: str | None = None,
        base_url: str = DEFAULT_GEMINI_BASE,
        client: httpx.AsyncClient | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.name = name
        self.model = model
        self._api_key = api_key or ""
        self._base_url = base_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout)

    @property
    def type(self) -> str:
        return "google"

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _query(self) -> str:
        return f"key={self._api_key}" if self._api_key else ""

    def _headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json"}

    async def chat(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolDef] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[TokenChunk]:
        system_text, user_messages = _split_system(messages)
        payload: dict[str, Any] = {
            "contents": [_to_gemini_content(m) for m in user_messages if m.content.strip()],
            "generationConfig": {"temperature": temperature},
        }
        if system_text:
            payload["systemInstruction"] = {"role": "system", "parts": [{"text": system_text}]}
        if max_tokens is not None:
            payload["generationConfig"]["maxOutputTokens"] = max_tokens
        if tools:
            payload["tools"] = [{"functionDeclarations": [_tool_to_gemini(t) for t in tools]}]

        url = f"{self._base_url}/models/{self.model}:streamGenerateContent"
        params = {"alt": "sse"}
        if self._api_key:
            params["key"] = self._api_key

        async with self._client.stream(
            "POST", url, json=payload, params=params, headers=self._headers()
        ) as response:
            response.raise_for_status()
            async for raw in response.aiter_text():
                for line in raw.splitlines():
                    line = line.strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[len("data:") :].strip()
                    if not data:
                        continue
                    try:
                        event = json.loads(data)
                    except ValueError:
                        continue
                    candidates = event.get("candidates") or []
                    if not candidates:
                        continue
                    parts = (candidates[0].get("content") or {}).get("parts") or []
                    for part in parts:
                        if "text" in part:
                            yield TokenChunk(text_delta=part["text"])
                        if "functionCall" in part:
                            fc = part["functionCall"]
                            yield TokenChunk(
                                tool_calls=[
                                    ToolCall(
                                        id=f"gemini-{fc.get('name', '')}",
                                        name=fc.get("name", ""),
                                        arguments=dict(fc.get("args") or {}),
                                    )
                                ]
                            )
                    finish = candidates[0].get("finishReason")
                    if finish:
                        yield TokenChunk(finish_reason=_map_finish_reason(finish) or "stop")

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for text in texts:
            url = f"{self._base_url}/models/{self.model}:embedContent"
            params = {"key": self._api_key} if self._api_key else {}
            response = await self._client.post(
                url,
                params=params,
                json={"content": {"parts": [{"text": text}]}},
                headers=self._headers(),
            )
            response.raise_for_status()
            data = response.json()
            embedding = ((data.get("embedding") or {}).get("values")) or []
            out.append([float(v) for v in embedding])
        return out

    async def health(self) -> HealthReport:
        start = time.monotonic()
        try:
            url = f"{self._base_url}/models"
            params = {"key": self._api_key} if self._api_key else {}
            response = await self._client.get(url, params=params, headers=self._headers())
            response.raise_for_status()
            data = response.json()
            models = [
                m.get("name", "").split("/")[-1]
                for m in data.get("models", [])
                if "embedContent" in (m.get("supportedGenerationMethods") or [])
                or "generateContent" in (m.get("supportedGenerationMethods") or [])
            ]
            return HealthReport(
                ok=True,
                latency_ms=(time.monotonic() - start) * 1000.0,
                models=models,
            )
        except Exception as exc:
            return HealthReport(
                ok=False,
                latency_ms=(time.monotonic() - start) * 1000.0,
                error=f"{type(exc).__name__}: {exc}",
            )


def _split_system(messages: Sequence[Message]) -> tuple[str, list[Message]]:
    system_texts: list[str] = []
    rest: list[Message] = []
    for m in messages:
        if m.role == "system":
            if m.content:
                system_texts.append(m.content)
        else:
            rest.append(m)
    return "\n\n".join(system_texts), rest


def _to_gemini_content(m: Message) -> dict[str, Any]:
    role = "model" if m.role == "assistant" else m.role
    return {"role": role, "parts": [{"text": m.content}]}


def _tool_to_gemini(t: ToolDef) -> dict[str, Any]:
    return {
        "name": t.name,
        "description": t.description,
        "parameters": t.parameters or {"type": "object", "properties": {}},
    }


def _map_finish_reason(reason: str) -> str | None:
    return {
        "STOP": "stop",
        "MAX_TOKENS": "length",
        "SAFETY": "stop",
        "RECITATION": "stop",
        "OTHER": "stop",
    }.get(reason)


__all__: list[str] = ["DEFAULT_GEMINI_BASE", "GoogleProvider"]


def _provider_is_compatible(p: Provider) -> bool:
    return isinstance(p, GoogleProvider)
