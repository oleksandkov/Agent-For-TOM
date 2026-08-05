"""
In-process OpenAI-compatible adapter.

`zen_proxy.py` does this translation correctly, but does it by running a
separate HTTP daemon on port 6446: a background process with its own
lifecycle, its own port conflicts, its own failure modes, and — measured in
the QA pass — the largest single source of production failures. The
translation is worth keeping. The HTTP hop is not.

This module presents the same surface the agent already programs against
(`client.messages.create(...)` / `client.messages.stream(...)`, Anthropic
shapes in and out) and speaks OpenAI wire format underneath, with no daemon.

It also delivers **real** streaming. The proxy synthesised SSE frames from an
already-complete response — a deliberate stopgap that made the UI look
streamed while the user waited for the whole reply. Here upstream SSE is read
line by line and re-emitted as it arrives.

The standalone proxy remains available (`TOMAS_ZEN_PROXY=1`) because pointing
*other* tools at Zen is a genuine use for it. The agent no longer depends on
it.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Iterator, Optional

from zen_proxy import anthropic_to_openai, openai_to_anthropic, _oc_id

DEFAULT_TIMEOUT = 300


# ══════════════════════════════════════════════════════════════════════
#  Anthropic-shaped response objects
# ══════════════════════════════════════════════════════════════════════

class _Block(dict):
    """One content block, readable as an object *and* as a dict.

    The loop reads attributes (`b.type`, `b.text`, `b.id`, `b.name`,
    `b.input`) the way the Anthropic SDK presents them. It then appends
    `response.content` straight into `messages`, where the next request's
    translation reads the same blocks with `b.get("type")` and `b["text"]`.
    Supporting only attributes made the second request die with
    "'_Block' object has no attribute 'get'" — and only on the turn *after*
    a tool call, which is why the stub round-trips never hit it.
    """

    def __init__(self, data: dict):
        super().__init__(data)
        self.type = data.get("type", "text")
        self.text = data.get("text", "")
        self.id = data.get("id", "")
        self.name = data.get("name", "")
        self.input = data.get("input", {})

    def model_dump(self) -> dict:
        return dict(self)

    def __repr__(self) -> str:
        return f"<Block {self.type} {self.name or self.text[:24]!r}>"


class _Usage:
    def __init__(self, data: dict):
        self.input_tokens = data.get("input_tokens", 0)
        self.output_tokens = data.get("output_tokens", 0)


class Message:
    """Anthropic-shaped response."""

    def __init__(self, data: dict):
        self._data = data
        self.id = data.get("id", "")
        self.type = data.get("type", "message")
        self.role = data.get("role", "assistant")
        self.model = data.get("model", "")
        self.stop_reason = data.get("stop_reason", "end_turn")
        self.content = [_Block(b) for b in data.get("content", [])]
        self.usage = _Usage(data.get("usage", {}))

    def model_dump(self) -> dict:
        return dict(self._data)


# ── Streaming events (the subset core/loop.py consumes) ──

class _Delta:
    def __init__(self, type_: str, text: str = ""):
        self.type = type_
        self.text = text


class _ContentBlockRef:
    def __init__(self, type_: str):
        self.type = type_


class _Event:
    def __init__(self, type_: str, delta: Optional[_Delta] = None,
                 content_block: Optional[_ContentBlockRef] = None):
        self.type = type_
        self.delta = delta
        self.content_block = content_block


class _Stream:
    """Context manager yielding Anthropic-shaped stream events."""

    def __init__(self, adapter: "OpenAICompatAdapter", kwargs: dict):
        self._adapter = adapter
        self._kwargs = kwargs
        self._final: Optional[Message] = None
        self._resp = None

    def __enter__(self) -> "_Stream":
        self._resp = self._adapter._open_stream(self._kwargs)
        return self

    def __exit__(self, *exc) -> bool:
        try:
            if self._resp is not None:
                self._resp.close()
        except Exception:
            pass
        return False

    def __iter__(self) -> Iterator[_Event]:
        text_parts: list[str] = []
        tool_calls: dict[int, dict] = {}
        finish_reason = None
        usage: dict = {}
        announced_tool = False

        for chunk in _iter_sse(self._resp):
            choices = chunk.get("choices") or []
            if chunk.get("usage"):
                usage = chunk["usage"]
            if not choices:
                continue
            choice = choices[0]
            delta = choice.get("delta") or {}

            piece = delta.get("content")
            if piece:
                text_parts.append(piece)
                yield _Event("content_block_delta", delta=_Delta("text_delta", piece))

            for tc in delta.get("tool_calls") or []:
                idx = tc.get("index", 0)
                slot = tool_calls.setdefault(
                    idx, {"id": tc.get("id") or _oc_id("toolu"),
                          "name": "", "arguments": ""})
                if tc.get("id"):
                    slot["id"] = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    slot["name"] = fn["name"]
                if fn.get("arguments"):
                    slot["arguments"] += fn["arguments"]
                if not announced_tool:
                    announced_tool = True
                    yield _Event("content_block_start",
                                 content_block=_ContentBlockRef("tool_use"))

            if choice.get("finish_reason"):
                finish_reason = choice["finish_reason"]

        content: list[dict] = []
        if text_parts:
            content.append({"type": "text", "text": "".join(text_parts)})
        for _, slot in sorted(tool_calls.items()):
            try:
                parsed = json.loads(slot["arguments"] or "{}")
            except json.JSONDecodeError:
                parsed = {}
            content.append({"type": "tool_use", "id": slot["id"],
                            "name": slot["name"], "input": parsed})
        if not content:
            content.append({"type": "text", "text": ""})

        stop_reason = {"tool_calls": "tool_use", "length": "max_tokens",
                       "stop": "end_turn"}.get(finish_reason or "stop", "end_turn")
        if tool_calls:
            stop_reason = "tool_use"

        self._final = Message({
            "id": _oc_id("msg"), "type": "message", "role": "assistant",
            "content": content, "model": self._kwargs.get("model", ""),
            "stop_reason": stop_reason,
            "usage": {"input_tokens": usage.get("prompt_tokens", 0),
                      "output_tokens": usage.get("completion_tokens",
                                                 len("".join(text_parts)) // 4)},
        })
        yield _Event("message_stop")

    def get_final_message(self) -> Message:
        if self._final is None:
            self._final = Message({
                "id": _oc_id("msg"), "type": "message", "role": "assistant",
                "content": [{"type": "text", "text": ""}],
                "model": self._kwargs.get("model", ""),
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 0, "output_tokens": 0},
            })
        return self._final


def _iter_sse(resp) -> Iterator[dict]:
    """Yield decoded JSON payloads from an SSE response, as they arrive."""
    if resp is None:
        return
    for raw in resp:
        line = raw.decode("utf-8", "replace").strip()
        if not line or line.startswith(":"):
            continue
        if line.startswith("data:"):
            line = line[5:].strip()
        if line == "[DONE]":
            return
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


# ══════════════════════════════════════════════════════════════════════
#  Adapter
# ══════════════════════════════════════════════════════════════════════

class ProviderStreamError(RuntimeError):
    """The endpoint would not stream. Recoverable — fall back to blocking."""


class _Messages:
    def __init__(self, adapter: "OpenAICompatAdapter"):
        self._adapter = adapter

    def create(self, **kw) -> Message:
        return self._adapter.create(**kw)

    def stream(self, **kw) -> _Stream:
        return self._adapter.stream(**kw)


class OpenAICompatAdapter:
    """Speaks the agent's Anthropic-shaped interface, talks OpenAI wire format."""

    def __init__(self, base_url: str, api_key: str = "",
                 extra_headers: Optional[dict] = None,
                 timeout: int = DEFAULT_TIMEOUT):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key or ""
        self.extra_headers = dict(extra_headers or {})
        self.timeout = timeout
        self.messages = _Messages(self)

    # ── plumbing ──

    @property
    def _endpoint(self) -> str:
        base = self.base_url
        if base.endswith("/v1"):
            return f"{base}/chat/completions"
        return f"{base}/v1/chat/completions"

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        headers.update(self.extra_headers)
        return headers

    def _request(self, body: dict):
        req = urllib.request.Request(
            self._endpoint, data=json.dumps(body).encode("utf-8"), method="POST")
        for k, v in self._headers().items():
            req.add_header(k, v)
        return urllib.request.urlopen(req, timeout=self.timeout)

    @staticmethod
    def _estimate_input_tokens(kw: dict) -> int:
        blob = json.dumps({"system": kw.get("system", ""),
                           "messages": kw.get("messages", [])})
        return len(blob) // 4

    @staticmethod
    def _build_body(kw: dict) -> dict:
        """Translate the request, then supply what the translation omits.

        `anthropic_to_openai` returns only `messages` (and `tools` when there
        are any) — the proxy always set `model` and the rest itself. Relying
        on the translation to carry them sent a body with no model, which the
        upstream rejects with a template placeholder in the message
        ("Model {{model}} is not supported"). A stub that ignores the model
        field cannot catch this; a real endpoint does immediately.
        """
        body = anthropic_to_openai(kw)
        body["model"] = kw.get("model", "")
        max_tokens = kw.get("max_tokens")
        if max_tokens:
            body["max_tokens"] = int(max_tokens)
        for passthrough in ("temperature", "top_p", "stop_sequences"):
            if kw.get(passthrough) is not None:
                key = "stop" if passthrough == "stop_sequences" else passthrough
                body[key] = kw[passthrough]
        body.pop("stream", None)
        return body

    # ── public surface ──

    def create(self, **kw) -> Message:
        body = self._build_body(kw)
        try:
            with self._request(body) as resp:
                raw = json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:400]
            raise RuntimeError(f"HTTP {e.code} from {self._endpoint}: {detail}") from e
        return Message(openai_to_anthropic(
            raw, kw.get("model", ""), self._estimate_input_tokens(kw)))

    def stream(self, **kw) -> _Stream:
        return _Stream(self, kw)

    def _open_stream(self, kw: dict):
        body = self._build_body(kw)
        body["stream"] = True
        body.setdefault("stream_options", {"include_usage": True})
        try:
            resp = self._request(body)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            raise ProviderStreamError(f"HTTP {e.code}: {detail}") from e
        except Exception as e:
            raise ProviderStreamError(str(e)) from e
        ctype = resp.headers.get("Content-Type", "")
        if "event-stream" not in ctype and "text/plain" not in ctype:
            resp.close()
            raise ProviderStreamError(f"endpoint did not stream (Content-Type: {ctype})")
        return resp


def should_use_adapter(provider_type: str = "", base_url: str = "") -> bool:
    """True when the active endpoint speaks OpenAI wire format.

    Set TOMAS_ZEN_PROXY=1 to force the old daemon path instead.
    """
    if os.environ.get("TOMAS_ZEN_PROXY", "") == "1":
        return False
    if os.environ.get("TOMAS_NO_OPENAI_ADAPTER", "") == "1":
        return False
    try:
        import provider_manager
        if provider_type:
            return provider_type in provider_manager.OPENAI_WIRE_TYPES
        provider = provider_manager.get_active()
        return bool(provider and provider.speaks_openai_wire)
    except Exception:
        return False


def build_from_active():
    """Construct an adapter for the active provider, or None."""
    import provider_manager
    provider = provider_manager.get_active()
    if provider is None or not provider.speaks_openai_wire:
        return None
    base = provider.base_url or os.environ.get("ANTHROPIC_BASE_URL", "")
    headers = dict(provider.extra_headers or {})
    # Ask the provider for its key rather than reaching for ANTHROPIC_API_KEY.
    # `api_key_env` is a configurable field, and hardcoding the Anthropic name
    # here meant any provider that set it — OPENROUTER_API_KEY, say — probed
    # fine (probe uses provider.api_key) and then failed every real call with
    # "Missing Authentication header".
    key = provider.api_key or os.environ.get("ANTHROPIC_API_KEY", "")

    if provider.type == "zen":
        # A zen provider points at the local proxy port, which only ever meant
        # "reach Zen". Go there directly and carry the headers the daemon used
        # to add on the way through.
        from zen_proxy import ZEN_API_HOST, _oc_id as _mkid, _zen_headers
        base = os.environ.get("ZEN_UPSTREAM_URL", f"https://{ZEN_API_HOST}/zen/v1")
        zen = _zen_headers(_mkid("ses"), _mkid("req"))
        key = zen.pop("Authorization", "").replace("Bearer ", "") or key
        zen.pop("Content-Type", None)
        headers.update(zen)
    elif provider.type == "ollama":
        key = key or "ollama"      # the shim rejects an empty bearer

    if not base or ":6446" in base:
        return None                # nothing sane to point at
    return OpenAICompatAdapter(base, key, headers)
