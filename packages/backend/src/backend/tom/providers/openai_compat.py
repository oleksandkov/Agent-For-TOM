"""OpenAI-compatible provider — works for OpenAI and any custom endpoint
that mirrors the ``/v1/chat/completions``, ``/v1/embeddings`` and
``/v1/models`` shape (Together, Groq, OpenRouter, LM Studio, vLLM, etc.).

Streaming is done via Server-Sent Events as per the OpenAI spec.
Auth header is ``Authorization: Bearer <key>`` — the key is fetched
from the OS keyring by the registry, never stored in the DB.
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


class OpenAICompatProvider:
    def __init__(
        self,
        *,
        name: str,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.name = name
        self.model = model
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key or ""
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout)

    @property
    def type(self) -> str:
        return "openai"

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    async def chat(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolDef] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[TokenChunk]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [_to_oai_message(m) for m in messages],
            "stream": True,
            "temperature": temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if tools:
            payload["tools"] = [_tool_to_oai(t) for t in tools]
            payload["tool_choice"] = "auto"

        async with self._client.stream(
            "POST",
            f"{self._base_url}/chat/completions",
            json=payload,
            headers=self._headers(),
        ) as response:
            response.raise_for_status()
            async for raw in response.aiter_text():
                for line in raw.splitlines():
                    line = line.strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[len("data:") :].strip()
                    if data == "[DONE]":
                        yield TokenChunk(finish_reason="stop")
                        return
                    try:
                        event = json.loads(data)
                    except ValueError:
                        continue
                    choice = (event.get("choices") or [{}])[0]
                    delta = choice.get("delta", {})
                    text = delta.get("content") or ""
                    raw_tool_calls = delta.get("tool_calls") or []
                    finish = choice.get("finish_reason")
                    if text or raw_tool_calls or finish:
                        yield TokenChunk(
                            text_delta=text,
                            tool_calls=[
                                _tool_call_from_oai(tc, idx)
                                for idx, tc in enumerate(raw_tool_calls)
                            ],
                            finish_reason=finish,
                        )

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        body: dict[str, Any] = await self._post_json(
            "/embeddings",
            {"model": self.model, "input": list(texts)},
        )
        return [list(map(float, item["embedding"])) for item in body["data"]]

    async def health(self) -> HealthReport:
        start = time.monotonic()
        try:
            response = await self._client.get(f"{self._base_url}/models", headers=self._headers())
            response.raise_for_status()
            data = response.json()
            models = [m["id"] for m in data.get("data", [])]
            if not models and "models" in data:
                models = [m.get("id") or m.get("name", "") for m in data["models"]]
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

    async def _post_json(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        response = await self._client.post(
            f"{self._base_url}{path}",
            json=body,
            headers=self._headers(),
        )
        response.raise_for_status()
        result: dict[str, Any] = response.json()
        return result


def _to_oai_message(m: Message) -> dict[str, Any]:
    out: dict[str, Any] = {"role": m.role, "content": m.content}
    if m.name:
        out["name"] = m.name
    if m.tool_call_id:
        out["tool_call_id"] = m.tool_call_id
    if m.tool_calls:
        out["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
            }
            for tc in m.tool_calls
        ]
    return out


def _tool_to_oai(t: ToolDef) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": t.name,
            "description": t.description,
            "parameters": t.parameters or {"type": "object", "properties": {}},
        },
    }


def _tool_call_from_oai(tc: dict[str, Any], idx: int) -> ToolCall:
    fn = tc.get("function", {})
    raw_args = fn.get("arguments") or "{}"
    if isinstance(raw_args, dict):
        return ToolCall(
            id=str(tc.get("id") or f"oai-{idx}"), name=fn.get("name", ""), arguments=dict(raw_args)
        )
    try:
        parsed = json.loads(raw_args)
    except ValueError:
        parsed = {"_raw": raw_args}
    return ToolCall(
        id=str(tc.get("id") or f"oai-{idx}"),
        name=fn.get("name", ""),
        arguments=dict(parsed),
    )


__all__: list[str] = ["OpenAICompatProvider"]


def _provider_is_compatible(p: Provider) -> bool:
    return isinstance(p, OpenAICompatProvider)
