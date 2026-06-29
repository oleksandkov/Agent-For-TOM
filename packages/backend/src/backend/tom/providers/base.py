"""Provider abstraction (Section 5).

Public surface used everywhere else in TOM:

- :class:`Provider` — async streaming interface, implemented per backend.
- :class:`Message`, :class:`ToolDef`, :class:`ToolCall` — transport-agnostic
  data classes. Each provider module translates to/from its native JSON.
- :class:`TokenChunk` — what streams out of :meth:`Provider.chat`.
- :class:`HealthReport` — what :meth:`Provider.health` returns.

The :class:`Provider` is a ``Protocol`` — concrete classes live in
``ollama.py``, ``openai_compat.py``, ``anthropic.py``, ``google.py``.
The :mod:`backend.tom.providers.registry` module wires configs to
implementations and applies the configured fallback chain.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable


@dataclass
class Message:
    """One message in a chat prompt. Role is the OpenAI-style string."""

    role: Literal["system", "user", "assistant", "tool"] = "user"
    content: str = ""
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)


@dataclass
class ToolDef:
    """Function-calling tool declaration (JSON schema form)."""

    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolCall:
    """A model-emitted tool invocation."""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class TokenChunk:
    """A single chunk of a streaming chat response."""

    text_delta: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None  # "stop" | "tool_calls" | "length" | "error"


@dataclass
class HealthReport:
    """Result of :meth:`Provider.health`.

    ``to_dict`` matches the CLI/JSON shape required by the plan.
    """

    ok: bool
    latency_ms: float | None = None
    models: list[str] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "latency_ms": self.latency_ms,
            "models": list(self.models),
            "error": self.error,
        }


@runtime_checkable
class Provider(Protocol):
    """Protocol every concrete provider implements.

    ``chat`` returns an async iterator directly because concrete providers
    are async-generator functions (``async def`` + ``yield``). Declaring
    the Protocol this way keeps structural matching with the concrete
    classes without forcing callers to ``await`` before iterating.

    ``embed`` / ``health`` return awaitables (the typical ``async def``
    with ``return`` shape).
    """

    name: str
    type: str
    model: str

    def chat(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolDef] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[TokenChunk]: ...

    def embed(self, texts: Sequence[str]) -> Awaitable[list[list[float]]]: ...

    def health(self) -> Awaitable[HealthReport]: ...


def filter_supported_kwargs(provider_type: str, **kw: Any) -> dict[str, Any]:
    """Strip unsupported kwargs before forwarding to a provider.

    Cloud providers have different accept headers; this helper lets
    callers pass generic kwargs (``top_p=...``, ``seed=...``) without
    crashing on per-provider validation.
    """
    if provider_type == "ollama":
        return kw  # Ollama accepts almost anything via JSON passthrough
    return {k: v for k, v in kw.items() if k in {"temperature", "max_tokens", "top_p", "stop"}}


async def collect_text(
    stream: AsyncIterator[TokenChunk],
) -> tuple[str, list[ToolCall], str | None]:
    """Drain an async iterator and return ``(text, tool_calls, finish_reason)``.

    Use from another async function via ``await``; this helper is async
    because a streaming provider's ``chat()`` yields chunks on the
    running loop.
    """
    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    finish_reason: str | None = None
    async for chunk in stream:
        if chunk.text_delta:
            text_parts.append(chunk.text_delta)
        tool_calls.extend(chunk.tool_calls)
        if chunk.finish_reason is not None:
            finish_reason = chunk.finish_reason
    return "".join(text_parts), tool_calls, finish_reason


def _iter_messages(messages: Sequence[Message]) -> Iterator[Message]:
    """Defensive iterator for code that needs a stable Sequence view."""
    return iter(tuple(messages))


__all__: list[str] = [
    "HealthReport",
    "Message",
    "Provider",
    "TokenChunk",
    "ToolCall",
    "ToolDef",
    "collect_text",
    "filter_supported_kwargs",
]
