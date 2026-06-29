"""Anthropic Messages API provider.

Hits ``POST /v1/messages`` directly over HTTP via ``httpx`` — the official
SDK isn't pulled into the dep tree to keep the surface small and tests
fast. SSE streaming is the same format the SDK would surface.

Auth header is ``x-api-key``; the optional ``anthropic-version`` header
is fixed at the latest stable version TOM supports (configurable via
``extra_headers`` if needed).
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

DEFAULT_ANTHROPIC_VERSION = "2023-06-01"


class AnthropicProvider:
    def __init__(
        self,
        *,
        name: str,
        model: str,
        api_key: str | None = None,
        base_url: str = "https://api.anthropic.com",
        anthropic_version: str = DEFAULT_ANTHROPIC_VERSION,
        client: httpx.AsyncClient | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.name = name
        self.model = model
        self._api_key = api_key or ""
        self._base_url = base_url.rstrip("/")
        self._anthropic_version = anthropic_version
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout)

    @property
    def type(self) -> str:
        return "anthropic"

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "anthropic-version": self._anthropic_version,
            **({"x-api-key": self._api_key} if self._api_key else {}),
        }

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
            "model": self.model,
            "messages": [_to_anthropic_message(m) for m in user_messages],
            "temperature": temperature,
            "stream": True,
            "max_tokens": max_tokens or 1024,
        }
        if system_text:
            payload["system"] = system_text
        if tools:
            payload["tools"] = [_tool_to_anthropic(t) for t in tools]

        async with self._client.stream(
            "POST",
            f"{self._base_url}/v1/messages",
            json=payload,
            headers=self._headers(),
        ) as response:
            response.raise_for_status()
            current_tool: dict[str, Any] | None = None
            async for raw in response.aiter_text():
                for line in raw.splitlines():
                    line = line.strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[len("data:") :].strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        event = json.loads(data)
                    except ValueError:
                        continue
                    evt_type = event.get("type")
                    if evt_type == "content_block_start":
                        block = event.get("content_block") or {}
                        if block.get("type") == "tool_use":
                            current_tool = {
                                "id": block.get("id", ""),
                                "name": block.get("name", ""),
                                "arguments_str": "",
                            }
                    elif evt_type == "content_block_delta":
                        delta = event.get("delta") or {}
                        if delta.get("type") == "text_delta":
                            yield TokenChunk(text_delta=delta.get("text", ""))
                        elif delta.get("type") == "input_json_delta" and current_tool:
                            current_tool["arguments_str"] += delta.get("partial_json", "")
                    elif evt_type == "content_block_stop":
                        if current_tool:
                            try:
                                arguments = json.loads(current_tool["arguments_str"] or "{}")
                            except ValueError:
                                arguments = {"_raw": current_tool["arguments_str"]}
                            yield TokenChunk(
                                tool_calls=[
                                    ToolCall(
                                        id=current_tool["id"],
                                        name=current_tool["name"],
                                        arguments=dict(arguments),
                                    )
                                ]
                            )
                            current_tool = None
                    elif evt_type == "message_delta":
                        stop_reason = (event.get("delta") or {}).get("stop_reason")
                        if stop_reason:
                            yield TokenChunk(finish_reason=_map_stop_reason(stop_reason))
                    elif evt_type == "message_stop":
                        yield TokenChunk(finish_reason="stop")

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        raise NotImplementedError(
            "Anthropic does not provide embeddings; route to Ollama or OpenAI instead."
        )

    async def health(self) -> HealthReport:
        start = time.monotonic()
        try:
            response = await self._client.get(
                f"{self._base_url}/v1/models",
                headers=self._headers(),
            )
            response.raise_for_status()
            data = response.json()
            models = [m["id"] for m in data.get("data", [])]
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


def _to_anthropic_message(m: Message) -> dict[str, Any]:
    out: dict[str, Any] = {"role": m.role, "content": m.content}
    if m.tool_calls:
        out["content"] = [
            {
                "type": "tool_use",
                "id": tc.id,
                "name": tc.name,
                "input": tc.arguments,
            }
            for tc in m.tool_calls
        ]
    if m.role == "tool" and m.tool_call_id:
        out = {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": m.tool_call_id,
                    "content": m.content,
                }
            ],
        }
    return out


def _tool_to_anthropic(t: ToolDef) -> dict[str, Any]:
    return {
        "name": t.name,
        "description": t.description,
        "input_schema": t.parameters or {"type": "object", "properties": {}},
    }


def _map_stop_reason(reason: str) -> str:
    return {
        "end_turn": "stop",
        "max_tokens": "length",
        "stop_sequence": "stop",
        "tool_use": "tool_calls",
    }.get(reason, "stop")


__all__: list[str] = ["DEFAULT_ANTHROPIC_VERSION", "AnthropicProvider"]


def _provider_is_compatible(p: Provider) -> bool:
    return isinstance(p, AnthropicProvider)
