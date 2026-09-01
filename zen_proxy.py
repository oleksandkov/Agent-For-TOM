#!/usr/bin/env python3
"""
Lightweight Python proxy for OpenCode Zen API.

Listens on localhost, accepts Anthropic-format requests (POST /v1/messages),
converts them to OpenAI format, forwards to opencode.ai/zen/v1/ with the
required x-opencode-* headers, and translates the response back.

Usage:
    python zen_proxy.py              # start on default port 6446
    python zen_proxy.py --port 9999  # custom port
    python zen_proxy.py --status     # check if proxy is running
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

ZEN_API_HOST = "opencode.ai"
ZEN_API_PATH = "/zen/v1/chat/completions"
DEFAULT_PORT = 6446
OC_VERSION = "1.15.0"

# ANSI color constants (used when printing to terminal)
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
BOLD = '\033[1m'
DIM = '\033[2m'
RESET = '\033[0m'

def _live_catalog():
    """The current Zen catalogue, falling back to the tables below.

    Imported lazily because `zen_catalog` reaches back into this module for
    exactly those tables when it cannot reach the network.
    """
    try:
        import zen_catalog
        return zen_catalog.catalog()
    except Exception:                       # offline, or import trouble
        from types import SimpleNamespace
        models = [SimpleNamespace(id=m, context=MODEL_CONTEXT_WINDOWS.get(m, 128_000),
                                  free=m.endswith("-free"))
                  for m in ZEN_MODELS]
        return SimpleNamespace(models=models, source="static",
                               free=lambda: [m for m in models if m.free])


# The offline fallback, and nothing more. `zen_catalog` fetches the real list
# from `opencode.ai/zen/v1/models` on every path that shows models to a user;
# this is what is left when there is no network and no cache.
#
# It is kept rather than deleted because a first run with no connectivity still
# has to offer *something*, but it is wrong the moment upstream changes and
# there is no way to notice from in here — checked on 2026-08-13, three of
# these had been withdrawn and four served models were absent. Do not add to
# it by hand expecting the picker to show the addition; the picker asks
# upstream.
ZEN_MODELS = [
    # Claude
    "claude-fable-5",
    "claude-opus-5",
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-opus-4-5",
    "claude-opus-4-1",
    "claude-sonnet-5",
    "claude-sonnet-4-6",
    "claude-sonnet-4-5",
    "claude-sonnet-4",
    "claude-haiku-4-5",
    # Gemini
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.5-flash",
    "gemini-3.1-pro",
    "gemini-3-flash",
    # GPT / OpenAI
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "gpt-5.5",
    "gpt-5.5-pro",
    "gpt-5.4",
    "gpt-5.4-pro",
    "gpt-5.4-mini",
    "gpt-5.4-nano",
    "gpt-5.3-codex-spark",
    "gpt-5.3-codex",
    "gpt-5.2",
    "gpt-5.2-codex",
    "gpt-5.1",
    "gpt-5.1-codex-max",
    "gpt-5.1-codex",
    "gpt-5.1-codex-mini",
    "gpt-5",
    "gpt-5-codex",
    "gpt-5-nano",
    # Grok
    "grok-build-0.1",
    "grok-4.5",
    # DeepSeek
    "deepseek-v4-pro",
    "deepseek-v4-flash",
    "deepseek-v4-flash-free",
    # GLM / Zhipu
    "glm-5.2",
    "glm-5.1",
    "glm-5",
    # MiniMax
    "minimax-m3",
    "minimax-m2.7",
    "minimax-m2.5",
    # Kimi / Moonshot
    "kimi-k2.7-code",
    "kimi-k2.6",
    "kimi-k2.5",
    "kimi-k3",
    # Qwen / Alibaba
    "qwen3.6-plus",
    "qwen3.5-plus",
    # OpenCode Zen free tier
    "big-pickle",
    "mimo-v2.5-free",
    "ling-3.0-flash-free",
    "nemotron-3-ultra-free",
    "north-mini-code-free",
    "laguna-s-2.1-free",
]

# Context windows for each Zen model (tokens)
# Models not listed here default to 128_000
MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    # Claude — most have 200K context
    "claude-sonnet-4-5": 200_000,
    "claude-sonnet-4-6": 200_000,
    "claude-sonnet-4": 200_000,
    "claude-sonnet-5": 200_000,
    "claude-opus-4-1": 200_000,
    "claude-opus-4-5": 200_000,
    "claude-opus-4-6": 200_000,
    "claude-opus-4-7": 200_000,
    "claude-opus-4-8": 200_000,
    "claude-opus-5": 200_000,
    "claude-fable-5": 200_000,
    "claude-haiku-4-5": 200_000,
    # Gemini — 1M context
    "gemini-3.6-flash": 1_000_000,
    "gemini-3.5-flash": 1_000_000,
    "gemini-3.5-flash-lite": 1_000_000,
    "gemini-3.1-pro": 1_000_000,
    "gemini-3-flash": 1_000_000,
    # DeepSeek — 1M context
    "deepseek-v4-pro": 1_000_000,
    "deepseek-v4-flash": 1_000_000,
    "deepseek-v4-flash-free": 1_000_000,
    # GPT-5.6 — 1M context
    "gpt-5.6-sol": 1_000_000,
    "gpt-5.6-terra": 1_000_000,
    "gpt-5.6-luna": 1_000_000,
    # Other GPT — 128K context
    "gpt-5.5": 128_000,
    "gpt-5.5-pro": 128_000,
    "gpt-5.4": 128_000,
    "gpt-5.4-pro": 128_000,
    "gpt-5.4-mini": 128_000,
    "gpt-5.4-nano": 128_000,
    "gpt-5.3-codex-spark": 128_000,
    "gpt-5.3-codex": 128_000,
    "gpt-5.2": 128_000,
    "gpt-5.2-codex": 128_000,
    "gpt-5.1": 128_000,
    "gpt-5.1-codex-max": 128_000,
    "gpt-5.1-codex": 128_000,
    "gpt-5.1-codex-mini": 128_000,
    "gpt-5": 128_000,
    "gpt-5-codex": 128_000,
    "gpt-5-nano": 128_000,
    # Grok — 128K context
    "grok-build-0.1": 128_000,
    "grok-4.5": 128_000,
    # GLM — 128K context
    "glm-5.2": 128_000,
    "glm-5.1": 128_000,
    "glm-5": 128_000,
    # MiniMax — 128K context
    "minimax-m3": 128_000,
    "minimax-m2.7": 128_000,
    "minimax-m2.5": 128_000,
    # Kimi — 128K context
    "kimi-k2.7-code": 128_000,
    "kimi-k2.6": 128_000,
    "kimi-k2.5": 128_000,
    "kimi-k3": 128_000,
    # Qwen — 128K context
    "qwen3.6-plus": 128_000,
    "qwen3.5-plus": 128_000,
    # OpenCode Zen free tier
    "big-pickle": 128_000,
    "mimo-v2.5-free": 128_000,
    "ling-3.0-flash-free": 128_000,
    "nemotron-3-ultra-free": 128_000,
    "north-mini-code-free": 128_000,
    "laguna-s-2.1-free": 128_000,
}


def _zen_headers(session_id: str, request_id: str) -> dict:
    """Build the required x-opencode-* headers for Zen API."""
    token = os.environ.get("OPENCODE_API_KEY") or os.environ.get("ZEN_API_KEY") or "public"
    if token.startswith("Bearer "):
        token = token[7:]
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
        "User-Agent": f"opencode/{OC_VERSION} ai-sdk/provider-utils/4.0.23 runtime/python/3.10",
        "x-opencode-client": "cli",
        "x-opencode-project": "global",
        "x-opencode-request": request_id,
        "x-opencode-session": session_id,
    }


def _oc_id(prefix: str) -> str:
    """Generate an OpenCode-style ID (msg_xxx / ses_xxx / toolu_xxx)."""
    ts = hex(int(time.time() * 1000))[2:]
    rnd = uuid.uuid4().hex[:16]
    return f"{prefix}_{ts}{rnd}"


def _upstream_request(zen_req: Request, max_retries: int = 2) -> bytes:
    """
    Forward a request to the upstream Zen API with retry on 5xx / timeout.
    Raises URLError (or HTTPError subclass) on failure after exhausting retries.
    """
    import socket
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            with urlopen(zen_req, timeout=120) as zen_resp:
                return zen_resp.read()
        except HTTPError as e:
            # 4xx client errors → don't retry, re-raise immediately
            if 400 <= e.code < 500:
                raise
            last_error = e
            if attempt < max_retries:
                wait = 1.0 * (attempt + 1)
                sys.stderr.write(
                    f"{YELLOW}[ZEN PROXY] Upstream {e.code} (attempt {attempt+1}/{max_retries+1}), "
                    f"retrying in {wait:.0f}s...{RESET}\n"
                )
                time.sleep(wait)
        except (URLError, socket.timeout, OSError) as e:
            last_error = e
            if attempt < max_retries:
                wait = 1.0 * (attempt + 1)
                sys.stderr.write(
                    f"{YELLOW}[ZEN PROXY] Upstream error: {e} (attempt {attempt+1}/{max_retries+1}), "
                    f"retrying in {wait:.0f}s...{RESET}\n"
                )
                time.sleep(wait)
    # All retries exhausted
    raise last_error  # type: ignore[misc]


def reasoning_of(blocks) -> str:
    """The `reasoning_content` carried on an assistant turn's blocks, if any.

    A thinking model's chain-of-thought is part of the assistant turn, and some
    upstreams *require* it back on the next request rather than merely
    accepting it:

        400 invalid_request_error — "The `reasoning_content` in the thinking
        mode must be passed back"

    Measured on `deepseek-v4-flash-free` through Zen: turn 1 and 2 answered,
    and turn 3 — the first request replaying an assistant turn that had
    reasoning — failed, then every turn after it. A ten-turn session delivered
    two answers. Neither this module nor `openai_adapter` mentioned
    `reasoning_content` anywhere, so it was being dropped on the floor for
    every reasoning model; `zen_catalog` reports `reasoning: True` for all
    eight of Zen's free models.

    Stored on the content blocks rather than beside them because
    `core.loop` appends `response.content` straight into `messages` and nothing
    else survives that hop — the same reason Gemini 3's `thought_signature`
    rides on the tool_use block. Read from any block, written to the first.
    """
    if not isinstance(blocks, list):
        return ""
    for block in blocks:
        if isinstance(block, dict) and block.get("reasoning_content"):
            return str(block["reasoning_content"])
    return ""


def attach_reasoning(blocks: list, reasoning: str) -> list:
    """Put `reasoning` on the first block, so it round-trips. No-op if empty."""
    if reasoning and blocks and isinstance(blocks[0], dict):
        blocks[0]["reasoning_content"] = reasoning
    return blocks


#: Replay `reasoning_content` for assistant turns this far back from the end,
#: and drop it from everything older. `0` would send none and `None` all.
#:
#: The requirement `reasoning_of` documents is real but narrow: the provider
#: validates the reasoning attached to the assistant turn it is being asked to
#: continue. Replaying *every* historical turn's chain-of-thought satisfies
#: that and then keeps paying for it — measured on one `hy3-free` session,
#: 146,000 characters (~36k tokens) of superseded reasoning re-sent on all 51
#: requests, growing with every step. Prompt caching hides most of the bill
#: and none of the context-window cost.
#:
#: Four is a margin, not a measurement: what the provider needs is the last
#: one, and the three before it are there because "the active chain" is not
#: something this function can see. Raise it, or set it to None, if an
#: upstream ever rejects a request for reasoning it was not given —
#: `TOMAS_REPLAY_REASONING` does that without a code change.
def _reasoning_window() -> "int | None":
    raw = os.environ.get("TOMAS_REPLAY_REASONING", "").strip().lower()
    if raw in ("all", "-1"):
        return None
    if raw.isdigit():
        return int(raw)
    return 4


def anthropic_to_openai(ant_body: dict, replay_reasoning: bool = True) -> dict:
    """Convert an Anthropic-format request body to OpenAI format.

    `replay_reasoning=False` sends no `reasoning_content` at all. Not every
    upstream that *emits* reasoning will *accept* it: Groq answers with a
    `reasoning` field and rejects a request carrying one back
    (`property 'reasoning_content' is unsupported`), which is the mirror image
    of the DeepSeek requirement `reasoning_of` documents. Declared per
    endpoint via `ProviderSpec.quirks`, not guessed from the model name.
    """
    messages = []

    # Which assistant turns still carry their reasoning. Counted from the end
    # over the *source* list, before any splitting into tool/assistant
    # entries, so the window means the same thing whatever a turn contained.
    window = _reasoning_window()
    source = ant_body.get("messages", [])
    # Past the end of the list, so `_reasoning_at` is empty for every index —
    # one branch instead of a second condition at both call sites.
    send_none = len(source) + 1
    if not replay_reasoning or window == 0:
        # `window == 0` is `TOMAS_REPLAY_REASONING=0`, documented as "send
        # none". It used to do the exact opposite: `assistant_positions[-0]`
        # is `[0]`, so the escape hatch for an upstream that chokes on
        # reasoning widened the window to every turn in the conversation.
        keep_reasoning_from = send_none
    elif window is None:
        keep_reasoning_from = 0
    else:
        assistant_positions = [i for i, m in enumerate(source)
                               if m.get("role") == "assistant"]
        keep_reasoning_from = (assistant_positions[-window]
                               if len(assistant_positions) > window else 0)

    def _reasoning_at(index: int, content) -> str:
        return reasoning_of(content) if index >= keep_reasoning_from else ""

    # System message
    if ant_body.get("system"):
        sys_text = ant_body["system"]
        if isinstance(sys_text, list):
            sys_text = " ".join(b.get("text", "") for b in sys_text)
        if sys_text:
            messages.append({"role": "system", "content": sys_text})

    # Conversation messages
    for index, msg in enumerate(source):
        role = msg["role"]
        content = msg.get("content", "")

        if isinstance(content, str):
            if role == "assistant":
                messages.append({"role": "assistant", "content": content})
            else:
                messages.append({"role": role, "content": content})
        elif isinstance(content, list):
            # Extract text content
            text_parts = [b["text"] for b in content if b.get("type") == "text"]
            text = "\n".join(text_parts)

            # Tool use blocks → tool_calls
            tool_uses = [b for b in content if b.get("type") == "tool_use"]
            if tool_uses and role == "assistant":
                msg_entry: dict = {"role": "assistant", "content": text or None}
                # `extra_content` is echoed back when the block carries it.
                # Gemini 3 attaches a `thought_signature` to every tool call
                # and *requires* it on the way back in:
                #
                #   400 INVALID_ARGUMENT — "Function call is missing a
                #   thought_signature in functionCall parts. This is required
                #   for tools to work correctly."
                #
                # Rebuilding the call from id/name/input alone therefore made
                # every Gemini 3 model run its first tool and then fail the
                # follow-up request — the tool worked and the turn did not.
                # Verified against gemini-3.6-flash, -3.5-flash and
                # -3.1-flash-lite; 2.5 and gemma do not send one and are
                # unaffected, because the key is only emitted when present.
                calls = []
                for t in tool_uses:
                    call = {
                        "id": t["id"],
                        "type": "function",
                        "function": {
                            "name": t["name"],
                            "arguments": json.dumps(t.get("input", {})),
                        },
                    }
                    if t.get("extra_content"):
                        call["extra_content"] = t["extra_content"]
                    calls.append(call)
                msg_entry["tool_calls"] = calls
                # Thinking models reject the next request without it — for the
                # turn being continued. See `_reasoning_window`.
                thought = _reasoning_at(index, content)
                if thought:
                    msg_entry["reasoning_content"] = thought
                messages.append(msg_entry)
            elif any(b.get("type") == "tool_result" for b in content):
                for b in content:
                    if b.get("type") == "tool_result":
                        result_content = b.get("content", "")
                        if isinstance(result_content, list):
                            result_content = " ".join(
                                c.get("text", "") for c in result_content
                            )
                        messages.append({
                            "role": "tool",
                            "tool_call_id": b["tool_use_id"],
                            "content": str(result_content),
                        })
            else:
                entry = {"role": role, "content": text}
                # An assistant turn that reasoned and then answered in prose,
                # with no tool call. Same requirement, different branch — and
                # the branch a short session is most likely to hit first.
                thought = _reasoning_at(index, content) if role == "assistant" else ""
                if thought:
                    entry["reasoning_content"] = thought
                messages.append(entry)

    # Tools
    tools = []
    for t in ant_body.get("tools", []):
        schema = t.get("input_schema") or {"type": "object", "properties": {}}
        if not isinstance(schema, dict) or "type" not in schema:
            schema = {"type": "object", "properties": {}}
        desc = t.get("description") or f"Tool {t.get('name', 'mcp')}"
        tools.append({
            "type": "function",
            "function": {
                "name": t["name"],
                "description": str(desc),
                "parameters": schema,
            },
        })

    result: dict = {"messages": messages}
    if tools:
        result["tools"] = tools
    return result


def openai_to_anthropic(oai_resp: dict, model: str, input_tokens: int) -> dict:
    """Convert an OpenAI-format response to Anthropic format."""
    choice = (oai_resp.get("choices") or [None])[0]
    if not choice:
        return {
            "id": _oc_id("msg"),
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": ""}],
            "model": model,
            "stop_reason": "end_turn",
            "usage": {"input_tokens": input_tokens, "output_tokens": 0},
        }

    content: list[dict] = []
    if choice.get("message", {}).get("content"):
        content.append({"type": "text", "text": choice["message"]["content"]})
    for tc in (choice.get("message", {}).get("tool_calls") or []):
        try:
            inp = json.loads(tc["function"]["arguments"])
        except (json.JSONDecodeError, KeyError):
            inp = {}
        block = {
            "type": "tool_use",
            "id": tc.get("id", _oc_id("toolu")),
            "name": tc["function"]["name"],
            "input": inp,
        }
        # Carried, not dropped. See `anthropic_to_openai` for why.
        if tc.get("extra_content"):
            block["extra_content"] = tc["extra_content"]
        content.append(block)

    if not content:
        content.append({"type": "text", "text": ""})

    # Carried onto the blocks so it survives into `messages` and comes back on
    # the next request. See `reasoning_of`.
    attach_reasoning(content, (choice.get("message") or {}).get("reasoning_content") or "")

    finish = choice.get("finish_reason", "stop")
    stop_reason_map = {
        "tool_calls": "tool_use",
        "length": "max_tokens",
        "stop": "end_turn",
    }
    stop_reason = stop_reason_map.get(finish, "end_turn")

    usage = oai_resp.get("usage", {})
    return {
        "id": _oc_id("msg"),
        "type": "message",
        "role": "assistant",
        "content": content,
        "model": model,
        "stop_reason": stop_reason,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", input_tokens),
            "output_tokens": usage.get("completion_tokens", 0),
        },
    }


# ── HTTP Handler ──────────────────────────────────────────────────────

class ZenProxyHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the Zen proxy."""

    # Shared session state (rotates every 30 min)
    _session_id: str = _oc_id("ses")
    _session_ts: float = time.time()

    def _send_json(self, status: int, data: dict):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def _send_sse_event(self, event: str, data: dict) -> None:
        """Write one Server-Sent Event frame."""
        self.wfile.write(f"event: {event}\n".encode())
        self.wfile.write(f"data: {json.dumps(data)}\n\n".encode())
        self.wfile.flush()

    def _send_anthropic_sse(self, ant_resp: dict) -> None:
        """Replay a completed Anthropic response as an SSE stream.

        The upstream call has already finished, so this is not true token
        streaming — but it emits exactly the frame sequence the Anthropic SDK's
        .stream() expects, so streaming clients work instead of failing with a
        502. True incremental streaming belongs in the provider adapter.
        """
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        # Deliberately NOT keep-alive: this server speaks HTTP/1.0 and sends no
        # Content-Length, so end-of-stream is signalled by closing the socket.
        # Advertising keep-alive would leave clients waiting for more data.
        self.send_header("Connection", "close")
        self.end_headers()

        blocks = ant_resp.get("content", []) or []
        usage = ant_resp.get("usage", {}) or {}

        # message_start carries the message shell with an empty content list.
        self._send_sse_event("message_start", {
            "type": "message_start",
            "message": {**{k: v for k, v in ant_resp.items() if k != "content"},
                        "content": []},
        })

        for i, block in enumerate(blocks):
            btype = block.get("type")
            if btype == "text":
                start_block = {"type": "text", "text": ""}
            else:
                # tool_use: input is streamed separately as input_json_delta
                start_block = {**block, "input": {}}
            self._send_sse_event("content_block_start", {
                "type": "content_block_start",
                "index": i,
                "content_block": start_block,
            })

            if btype == "text":
                self._send_sse_event("content_block_delta", {
                    "type": "content_block_delta",
                    "index": i,
                    "delta": {"type": "text_delta", "text": block.get("text", "")},
                })
            elif btype == "tool_use":
                self._send_sse_event("content_block_delta", {
                    "type": "content_block_delta",
                    "index": i,
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": json.dumps(block.get("input", {})),
                    },
                })

            self._send_sse_event("content_block_stop", {
                "type": "content_block_stop", "index": i,
            })

        self._send_sse_event("message_delta", {
            "type": "message_delta",
            "delta": {
                "stop_reason": ant_resp.get("stop_reason"),
                "stop_sequence": ant_resp.get("stop_sequence"),
            },
            "usage": {"output_tokens": usage.get("output_tokens", 0)},
        })
        self._send_sse_event("message_stop", {"type": "message_stop"})

    def _ensure_session(self):
        now = time.time()
        if now - self.__class__._session_ts > 1800:  # 30 min
            self.__class__._session_id = _oc_id("ses")
            self.__class__._session_ts = now

    def do_GET(self):
        if self.path == "/v1/models":
            # Served from the live catalogue, like everything else that lists
            # Zen models. The whole point of running this daemon is to point
            # *another* tool at Zen, and that tool's model picker was being
            # handed a list compiled into this file — three models it offered
            # had been withdrawn upstream.
            catalog = _live_catalog()
            data = {
                "object": "list",
                "data": [
                    {
                        "id": m.id,
                        "object": "model",
                        "created": 1779000000,
                        "owned_by": "opencode-zen",
                        "context_window": m.context,
                        "free": m.free,
                    }
                    for m in catalog.models
                ],
            }
            self._send_json(200, data)
        elif self.path == "/health":
            catalog = _live_catalog()
            self._send_json(200, {
                "status": "ok",
                "type": "opencode-zen-proxy",
                "catalog_source": catalog.source,
                "models": [m.id for m in catalog.models],
                "free_models": [m.id for m in catalog.free()],
                "model_context_windows": {m.id: m.context for m in catalog.models},
            })
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self):
        body_size = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(body_size) if body_size else b"{}"

        if self.path == "/v1/messages":
            self._handle_anthropic(raw_body)
        elif self.path == "/v1/chat/completions":
            self._handle_openai(raw_body)
        else:
            self._send_json(404, {"error": "not found"})

    def _handle_anthropic(self, raw_body: bytes):
        """Handle Anthropic-format POST /v1/messages."""
        try:
            ant_body = json.loads(raw_body)
        except json.JSONDecodeError:
            self._send_json(400, {"type": "error", "error": {"type": "invalid_request_error", "message": "Invalid JSON"}})
            return

        model = ant_body.get("model", ZEN_MODELS[0])

        stream = ant_body.get("stream", False)
        self._ensure_session()
        session_id = self.__class__._session_id
        request_id = _oc_id("msg")

        # Convert Anthropic → OpenAI
        oai_body = anthropic_to_openai(ant_body)
        oai_body["model"] = model
        # Always fetch a COMPLETE response upstream. _upstream_request reads the
        # whole body and json.loads() it below; an SSE body is not valid JSON,
        # so forwarding stream=true made every streamed request fail with
        # "Invalid upstream response". If the client wants streaming we
        # synthesise the SSE frames from the finished response instead.
        oai_body["stream"] = False
        input_tokens = len(json.dumps(oai_body["messages"])) // 4

        # Forward to Zen API (with retry on transient errors)
        zen_data = json.dumps(oai_body).encode()
        zen_req = Request(
            f"https://{ZEN_API_HOST}{ZEN_API_PATH}",
            data=zen_data,
            headers=_zen_headers(session_id, request_id),
            method="POST",
        )

        try:
            zen_raw = _upstream_request(zen_req)
        except URLError as e:
            # Log a SHORT one-line error (no request body dump to avoid terminal flooding)
            err_body = ""
            if isinstance(e, HTTPError):
                try:
                    err_body = e.read().decode('utf-8', errors='replace')[:200]
                except Exception:
                    pass
            status = getattr(e, 'code', 0)
            req_size = len(zen_data) if zen_data else 0
            sys.stderr.write(
                f"{YELLOW}[ZEN PROXY] Upstream error: {status} {e.reason}"
                f" (request: {req_size:,} bytes){RESET}\n"
            )
            self._send_json(502, {
                "type": "error",
                "error": {
                    "type": "upstream_error",
                    "message": str(e.reason),
                    "upstream_status": status,
                    "upstream_body": err_body[:300] if err_body else "",
                },
            })
            return

        try:
            zen_json = json.loads(zen_raw)
        except json.JSONDecodeError:
            self._send_json(502, {
                "type": "error",
                "error": {"type": "upstream_error", "message": "Invalid upstream response"},
            })
            return

        # Check for rate limit / error
        if zen_json.get("error"):
            err_msg = zen_json["error"].get("message", "Rate limit exceeded")
            self._send_json(429, {
                "type": "error",
                "error": {"type": "rate_limit_error", "message": err_msg + " (free model rate limit)"},
            })
            return

        # Convert OpenAI → Anthropic
        ant_resp = openai_to_anthropic(zen_json, model, input_tokens)
        if stream:
            self._send_anthropic_sse(ant_resp)
        else:
            self._send_json(200, ant_resp)

    def _handle_openai(self, raw_body: bytes):
        """Handle OpenAI-format POST /v1/chat/completions (passthrough)."""
        try:
            body = json.loads(raw_body)
        except json.JSONDecodeError:
            self._send_json(400, {"error": {"message": "Invalid JSON"}})
            return

        model = body.get("model", ZEN_MODELS[0])

        self._ensure_session()
        session_id = self.__class__._session_id
        request_id = _oc_id("msg")

        zen_data = json.dumps(body).encode()
        zen_req = Request(
            f"https://{ZEN_API_HOST}{ZEN_API_PATH}",
            data=zen_data,
            headers=_zen_headers(session_id, request_id),
            method="POST",
        )

        try:
            zen_raw = _upstream_request(zen_req)
        except URLError as e:
            # Log a SHORT one-line error (no request body dump)
            err_body = ""
            if isinstance(e, HTTPError):
                try:
                    err_body = e.read().decode('utf-8', errors='replace')[:200]
                except Exception:
                    pass
            status = getattr(e, 'code', 0)
            req_size = len(zen_data) if zen_data else 0
            sys.stderr.write(
                f"{YELLOW}[ZEN PROXY] Upstream error: {status} {e.reason}"
                f" (request: {req_size:,} bytes){RESET}\n"
            )
            self._send_json(502, {
                "error": {
                    "message": str(e.reason),
                    "upstream_status": status,
                    "upstream_body": err_body[:300] if err_body else "",
                }
            })
            return

        try:
            zen_json = json.loads(zen_raw)
        except json.JSONDecodeError:
            self._send_json(502, {"error": {"message": "Invalid upstream response"}})
            return

        if zen_json.get("error"):
            err_msg = zen_json["error"].get("message", "Rate limit exceeded")
            self._send_json(429, {"error": {"message": err_msg + " (free model rate limit)"}})
            return

        self._send_json(200, zen_json)

    def log_message(self, format, *args):
        """Silent logger — proxy noise is suppressed from user output.
        
        Upstream errors are logged separately in the request handler.
        """
        pass


def start_proxy(port: int = DEFAULT_PORT, daemon: bool = False) -> HTTPServer:
    """Start the Zen proxy server. If daemon=True, run in a background thread."""
    server = HTTPServer(("127.0.0.1", port), ZenProxyHandler)
    if daemon:
        import threading
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        print(f"  {GREEN}✓{RESET} Zen proxy started on http://127.0.0.1:{port}")
        return server
    else:
        print(f"  Zen proxy listening on http://127.0.0.1:{port}")
        print(f"  Anthropic API: POST /v1/messages")
        print(f"  OpenAI API:    POST /v1/chat/completions")
        print(f"  Models:        GET  /v1/models")
        print(f"  Health:        GET  /health")
        catalog = _live_catalog()
        free = catalog.free()
        print(f"  Catalogue:     {len(catalog.models)} models ({catalog.source}), "
              f"{len(free)} free")
        print(f"  Free:          {', '.join(m.id for m in free) or 'none'}")
        print()
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\n  Shutting down...")
            server.shutdown()


def check_status(port: int = DEFAULT_PORT, use_cache: bool = True) -> bool:
    """Check if the proxy is running. Returns True if reachable.

    Since Phase 4 the proxy is opt-in, so on a normal install nothing is
    listening — and this function was the single most expensive thing in the
    menus because of it. A connect to the closed port is silently dropped
    rather than refused, so the `timeout=2` was paid *in full*, every time,
    on three different pages (measured: 2,099 ms).

    Asking a cheap question first — is anything listening at all? — bounds the
    negative answer to ~200 ms, and the short TTL means revisiting a page is
    free. A running proxy still answers on the real HTTP path as before.
    """
    from net_probe import cached, port_open

    def measure() -> bool:
        if not port_open("127.0.0.1", port):
            return False
        try:
            req = Request(f"http://127.0.0.1:{port}/health")
            with urlopen(req, timeout=2) as resp:
                return json.loads(resp.read()).get("status") == "ok"
        except Exception:
            return False

    if not use_cache:
        return measure()
    return cached(f"zen_status:{port}", ttl=5.0, produce=measure)


# ── CLI entry point ──────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="OpenCode Zen Proxy")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Port to listen on (default: {DEFAULT_PORT})")
    parser.add_argument("--status", action="store_true", help="Check if proxy is running")
    args = parser.parse_args()

    if args.status:
        running = check_status(args.port)
        if running:
            print(f"  {GREEN}✓{RESET} Zen proxy is running on http://127.0.0.1:{args.port}")
        else:
            print(f"  {RED}✗{RESET} Zen proxy is NOT running on http://127.0.0.1:{args.port}")
        sys.exit(0 if running else 1)

    start_proxy(args.port)
