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

import http.client
import io
import json
import os
import threading
import urllib.error
import urllib.request
from typing import Any, Iterator, Optional
from urllib.parse import urlsplit

from zen_proxy import (anthropic_to_openai, attach_reasoning,
                       openai_to_anthropic, _oc_id)

DEFAULT_TIMEOUT = 300

#: Bounds only the TCP/TLS handshake, not the read that follows it. stdlib
#: `http.client` applies one `timeout` to both connect() and every later
#: recv(), so a route that black-holes packets (no refusal, just silence)
#: used to cost the full DEFAULT_TIMEOUT on each of the retry ladder's 4
#: attempts in core.loop — ~20 minutes for a failure a handshake should
#: reveal in seconds. Widening the socket's timeout right after connect()
#: (see _ConnectTimeoutMixin) keeps today's tolerance for a slow-but-arriving
#: reply; only the handshake itself is bounded tightly.
CONNECT_TIMEOUT = int(os.environ.get("TOMAS_CONNECT_TIMEOUT", "10"))


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


def cached_prompt_tokens(usage: Any) -> int:
    """How many prompt tokens the provider served from its prefix cache.

    Every OpenAI-wire provider that does automatic caching reports it, and each
    one spells it differently. Without this the agent shows the raw
    `prompt_tokens` and nothing else, so a turn that was 95% cache hits and a
    turn that was 0% look identical on screen — and the work that keeps the
    prefix byte-stable (sticky tool selection, the stable prompt half) has no
    visible effect to protect it from being undone.

    Returns 0 when the provider says nothing, which is not the same as a miss
    and is why this is reported separately rather than folded into the total.
    """
    if not isinstance(usage, dict):
        return 0
    details = usage.get("prompt_tokens_details")
    if isinstance(details, dict) and details.get("cached_tokens") is not None:
        return int(details.get("cached_tokens") or 0)
    for key in ("prompt_cache_hit_tokens", "cached_tokens",
                "cache_read_input_tokens"):
        if usage.get(key) is not None:
            return int(usage.get(key) or 0)
    return 0


class _Usage:
    def __init__(self, data: dict):
        self.input_tokens = data.get("input_tokens", 0)
        self.output_tokens = data.get("output_tokens", 0)
        #: The part of `input_tokens` that was a cache hit, when the provider
        #: reports it. Not subtracted from the total: this is what was sent,
        #: annotated with what it cost.
        self.cached_input_tokens = data.get("cached_input_tokens", 0)


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
        #: Tool calls whose arguments arrived as unparseable JSON and were
        #: replaced with `{}`. Set by the streaming path; the core reads it to
        #: measure how often a streamed reply would have been usable on its own.
        self.malformed_tool_args = 0

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
        # Accumulated but never yielded as an event: the chain-of-thought is
        # not the reply and must not appear on screen. It is collected because
        # a thinking model's *next* request is rejected without it — see
        # `zen_proxy.reasoning_of`. Doing this only on the non-streamed path
        # would fix nothing for the providers that actually stream, which is
        # most of them.
        reasoning_parts: list[str] = []
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

            # Spelled `reasoning_content` by DeepSeek and the Zen relay, and
            # `reasoning` by some others. Both are collected; neither is shown.
            thought = delta.get("reasoning_content") or delta.get("reasoning")
            if thought and isinstance(thought, str):
                reasoning_parts.append(thought)

            for tc in delta.get("tool_calls") or []:
                idx = tc.get("index", 0)
                slot = tool_calls.setdefault(
                    idx, {"id": tc.get("id") or _oc_id("toolu"),
                          "name": "", "arguments": "", "extra": None})
                if tc.get("id"):
                    slot["id"] = tc["id"]
                # Provider-specific baggage that has to survive the round trip
                # — Gemini 3's `thought_signature` lives here and the next
                # request is rejected without it. Kept opaque on purpose: this
                # module does not need to know what is inside, only that
                # dropping it breaks the turn after the tool call.
                if tc.get("extra_content"):
                    slot["extra"] = tc["extra_content"]
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
        # Counted, not just swallowed. This substitution is the one thing the
        # streamed assembly cannot do as well as a second non-streamed call —
        # so it is the number that decides whether that second call is still
        # worth making. Silently returning `{}` made the question unanswerable.
        malformed = 0
        for _, slot in sorted(tool_calls.items()):
            try:
                parsed = json.loads(slot["arguments"] or "{}")
            except json.JSONDecodeError:
                parsed = {}
                malformed += 1
            block = {"type": "tool_use", "id": slot["id"],
                     "name": slot["name"], "input": parsed}
            if slot.get("extra"):
                block["extra_content"] = slot["extra"]
            content.append(block)
        if not content:
            content.append({"type": "text", "text": ""})

        attach_reasoning(content, "".join(reasoning_parts))

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
                                                 len("".join(text_parts)) // 4),
                      "cached_input_tokens": cached_prompt_tokens(usage)},
        })
        self._final.malformed_tool_args = malformed
        # `stop_reason` above deliberately reports "tool_use" for a truncated
        # reply that still carried tool calls — the caller's job is to run
        # them. But truncation is also the one thing that can silently *lose*
        # a call the model meant to make, and a cut-off argument list is not
        # always cut off mid-JSON: it can stop cleanly between two calls, so
        # `malformed_tool_args` stays 0 and the loss is invisible. Recorded
        # separately so `core.loop` can decline to serve the turn from an
        # assembly it knows is incomplete.
        self._final.output_truncated = (finish_reason == "length")
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


def _drain(resp) -> None:
    """Read whatever is left on the socket so `isclosed()` becomes true.

    Only called from `_iter_sse`'s two natural-completion points, never from
    an early exit. A stream the caller abandons mid-read (interrupted turn,
    exception) delivers `GeneratorExit` to this generator at its current
    `yield`, which skips straight past both call sites — so an aborted
    connection is never drained, and `_PooledResponse.close()`'s existing
    `isclosed()` check keeps discarding it exactly as it does today. A failed
    drain (dropped connection, malformed trailer) is swallowed the same way:
    `isclosed()` just stays False and the connection is discarded, which is
    already correct.
    """
    try:
        resp.read()
    except Exception:
        pass


def _iter_sse(resp) -> Iterator[dict]:
    """Yield decoded JSON payloads from an SSE response, as they arrive.

    Draining on natural completion is what lets the connection go back to the
    pool: `[DONE]` used to `return` immediately, leaving the terminating
    chunked-encoding frame unread on the socket — `isclosed()` never became
    true, so every streamed call paid for a fresh TCP/TLS handshake on the
    *next* call even though `_ConnectionPool` exists specifically to avoid
    that. See `_PooledResponse.close()`.
    """
    if resp is None:
        return
    for raw in resp:
        line = raw.decode("utf-8", "replace").strip()
        if not line or line.startswith(":"):
            continue
        if line.startswith("data:"):
            line = line[5:].strip()
        if line == "[DONE]":
            _drain(resp)
            return
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue
    _drain(resp)  # provider closed the stream without a [DONE] sentinel


# ══════════════════════════════════════════════════════════════════════
#  Adapter
# ══════════════════════════════════════════════════════════════════════

class _PooledResponse:
    """An HTTPResponse that hands its connection back when it is closed.

    Everything the callers touch — `.read()`, `.headers`, `.status`, iteration
    by line, `with` — forwards to the real response. `close()` is the one that
    does extra work: it decides whether the connection is safe to reuse before
    closing it.
    """

    def __init__(self, pool: "_ConnectionPool", conn, resp):
        self._pool = pool
        self._conn = conn
        self._resp = resp
        self._released = False

    def __getattr__(self, name):
        return getattr(self._resp, name)

    def __iter__(self):
        return iter(self._resp)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def close(self) -> None:
        if self._released:
            return
        self._released = True
        # `isclosed()` is true only once the body has been read to the end.
        # A half-read body leaves bytes in the socket that would be parsed as
        # the *next* response's status line, so anything short of fully drained
        # is thrown away rather than pooled. Streamed calls reach `isclosed()`
        # too, via `_iter_sse`'s drain-on-completion — an *abandoned* stream
        # (interrupted mid-read) never reaches that drain, so it still falls
        # through to being discarded here, correctly.
        reusable = False
        try:
            reusable = bool(self._resp.isclosed())
        except Exception:
            reusable = False
        try:
            self._resp.close()
        except Exception:
            pass
        self._pool.release(self._conn, reusable=reusable)


class _ConnectTimeoutMixin:
    """Fail fast on a hung handshake; stay generous once connected.

    `timeout=` on an `http.client` connection covers connect() *and* every
    subsequent read with the same value. Passing the short CONNECT_TIMEOUT as
    that value and then widening the socket right after connect() gives the
    handshake its own tight bound without touching how long a slow-but-
    arriving reply is allowed to take.
    """

    def __init__(self, *a, connect_timeout: float, read_timeout: float, **kw):
        super().__init__(*a, timeout=connect_timeout, **kw)
        self._read_timeout = read_timeout

    def connect(self):
        super().connect()
        if self.sock is not None:
            self.sock.settimeout(self._read_timeout)


class _TimeoutHTTPConnection(_ConnectTimeoutMixin, http.client.HTTPConnection):
    pass


class _TimeoutHTTPSConnection(_ConnectTimeoutMixin, http.client.HTTPSConnection):
    pass


class _ConnectionPool:
    """One keep-alive connection per endpoint.

    `urllib.request.urlopen` pools nothing and sends `Connection: close`, so
    every model call paid for a fresh TCP and TLS handshake. A tool-using turn
    makes one call per step — a 29-step turn opened 58 connections to the same
    host — and all of that is dead air before the first token of the reply.

    One slot, not many: the agent issues its model calls one at a time, so a
    larger pool would only hold connections open for nothing. The lock is
    there because probes and the TUI can touch an adapter off the turn thread.
    """

    def __init__(self, endpoint: str, timeout: int):
        parts = urlsplit(endpoint)
        self._secure = parts.scheme != "http"
        self._host = parts.hostname or ""
        self._port = parts.port
        self._timeout = timeout
        self.path = parts.path or "/"
        if parts.query:
            self.path = f"{self.path}?{parts.query}"
        self._idle = None
        self._lock = threading.Lock()

    def _new(self):
        cls = _TimeoutHTTPSConnection if self._secure else _TimeoutHTTPConnection
        return cls(self._host, self._port,
                   connect_timeout=CONNECT_TIMEOUT, read_timeout=self._timeout)

    def acquire(self):
        with self._lock:
            conn, self._idle = self._idle, None
        return conn if conn is not None else self._new()

    def release(self, conn, reusable: bool) -> None:
        if reusable:
            with self._lock:
                if self._idle is None:
                    self._idle = conn
                    return
        try:
            conn.close()
        except Exception:
            pass

    def close(self) -> None:
        with self._lock:
            conn, self._idle = self._idle, None
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


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
                 timeout: int = DEFAULT_TIMEOUT,
                 preserve_tool_extras: bool = False):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key or ""
        self.extra_headers = dict(extra_headers or {})
        self.timeout = timeout
        #: Whether `extra_content` on a tool call is echoed back upstream.
        #:
        #: Off by default and on for Google, because the field is *its*
        #: baggage: Gemini 3 requires its `thought_signature` back and
        #: rejects the follow-up request without it, while another endpoint
        #: has no idea what it is. The transcript outlives a provider switch,
        #: so a conversation started on Gemini and continued on OpenAI would
        #: otherwise post a Google signature to OpenAI — stripping here is
        #: what keeps that switch working.
        self.preserve_tool_extras = preserve_tool_extras
        self.messages = _Messages(self)
        self._pool = _ConnectionPool(self._endpoint, timeout)

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
        """POST the body on a kept-alive connection.

        Raises `urllib.error.HTTPError` on a 4xx/5xx exactly as `urlopen` did,
        body attached, so both call sites keep reading `e.read()` unchanged.
        """
        payload = json.dumps(body).encode("utf-8")
        headers = self._headers()
        headers["Connection"] = "keep-alive"
        headers["Content-Length"] = str(len(payload))

        last_err: Optional[Exception] = None
        for attempt in (0, 1):
            conn = self._pool.acquire()
            try:
                conn.request("POST", self._pool.path, body=payload,
                             headers=headers)
                resp = conn.getresponse()
            except Exception as e:
                # A pooled connection the server has since dropped fails here,
                # on the write, not on connect — so one retry on a fresh
                # connection is what makes reuse safe at all. Exactly one:
                # a second would start hiding real outages behind a delay.
                self._pool.release(conn, reusable=False)
                last_err = e
                if attempt == 0:
                    continue
                raise
            if resp.status >= 400:
                detail = resp.read()
                self._pool.release(conn, reusable=True)
                raise urllib.error.HTTPError(
                    self._endpoint, resp.status, resp.reason or "",
                    resp.headers, io.BytesIO(detail))
            return _PooledResponse(self._pool, conn, resp)
        raise last_err if last_err else RuntimeError("request failed")

    @staticmethod
    def _estimate_input_tokens(kw: dict) -> int:
        blob = json.dumps({"system": kw.get("system", ""),
                           "messages": kw.get("messages", [])})
        return len(blob) // 4

    @staticmethod
    def _strip_tool_extras(body: dict) -> None:
        """Remove provider-specific `extra_content` from every tool call."""
        for message in body.get("messages") or []:
            for call in message.get("tool_calls") or []:
                call.pop("extra_content", None)

    def _build_body(self, kw: dict) -> dict:
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
        if not self.preserve_tool_extras:
            self._strip_tool_extras(body)
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
        data = openai_to_anthropic(
            raw, kw.get("model", ""), self._estimate_input_tokens(kw))
        # Annotated here rather than inside the translation: `openai_to_anthropic`
        # is shared with the standalone proxy, which speaks strict Anthropic
        # shapes and has no field to put this in.
        data.setdefault("usage", {})["cached_input_tokens"] = (
            cached_prompt_tokens(raw.get("usage")))
        return Message(data)

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
    elif provider.type == "google":
        # Same resolution the probe uses, so a measurement and a real call
        # never reach different endpoints — the bug `probe_base_url` exists
        # to prevent for zen.
        base = provider_manager.probe_base_url(provider)
        key = (key or os.environ.get("GOOGLE_API_KEY", "")
               or os.environ.get("GEMINI_API_KEY", ""))
    elif provider.type == "ollama":
        key = key or "ollama"      # the shim rejects an empty bearer

    if not base or ":6446" in base:
        return None                # nothing sane to point at
    return OpenAICompatAdapter(base, key, headers,
                               preserve_tool_extras=provider.type == "google")
