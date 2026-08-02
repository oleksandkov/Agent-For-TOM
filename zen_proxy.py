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

# Models available via the free Zen tier
# (as of July 2026 — fetched from upstream API, may change)
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


def anthropic_to_openai(ant_body: dict) -> dict:
    """Convert an Anthropic-format request body to OpenAI format."""
    messages = []

    # System message
    if ant_body.get("system"):
        sys_text = ant_body["system"]
        if isinstance(sys_text, list):
            sys_text = " ".join(b.get("text", "") for b in sys_text)
        if sys_text:
            messages.append({"role": "system", "content": sys_text})

    # Conversation messages
    for msg in ant_body.get("messages", []):
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
                msg_entry["tool_calls"] = [
                    {
                        "id": t["id"],
                        "type": "function",
                        "function": {
                            "name": t["name"],
                            "arguments": json.dumps(t.get("input", {})),
                        },
                    }
                    for t in tool_uses
                ]
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
                messages.append({"role": role, "content": text})

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
        content.append({
            "type": "tool_use",
            "id": tc.get("id", _oc_id("toolu")),
            "name": tc["function"]["name"],
            "input": inp,
        })

    if not content:
        content.append({"type": "text", "text": ""})

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

    def _ensure_session(self):
        now = time.time()
        if now - self.__class__._session_ts > 1800:  # 30 min
            self.__class__._session_id = _oc_id("ses")
            self.__class__._session_ts = now

    def do_GET(self):
        if self.path == "/v1/models":
            data = {
                "object": "list",
                "data": [
                    {
                        "id": m,
                        "object": "model",
                        "created": 1779000000,
                        "owned_by": "opencode-zen",
                        "context_window": MODEL_CONTEXT_WINDOWS.get(m, 128_000),
                    }
                    for m in ZEN_MODELS
                ],
            }
            self._send_json(200, data)
        elif self.path == "/health":
            self._send_json(200, {
                "status": "ok",
                "type": "opencode-zen-proxy",
                "models": ZEN_MODELS,
                "model_context_windows": MODEL_CONTEXT_WINDOWS,
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
        oai_body["stream"] = stream
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
        model_list = ', '.join(f"{m} ({MODEL_CONTEXT_WINDOWS.get(m, '?')} ctx)" for m in ZEN_MODELS)
        print(f"  Models: {model_list}")
        print()
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\n  Shutting down...")
            server.shutdown()


def check_status(port: int = DEFAULT_PORT) -> bool:
    """Check if the proxy is running. Returns True if reachable."""
    try:
        req = Request(f"http://127.0.0.1:{port}/health")
        with urlopen(req, timeout=2) as resp:
            data = json.loads(resp.read())
            return data.get("status") == "ok"
    except Exception:
        return False


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
