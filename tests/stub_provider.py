"""A minimal OpenAI-compatible server, for exercising the provider layer.

Real provider bugs are what the QA pass found and no mock caught, so the
stubs here deliberately reproduce *misbehaviour* — an endpoint that refuses
to stream, one with no tool support — rather than only the happy path.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


class BaseStub(BaseHTTPRequestHandler):
    context_length = 31337

    def log_message(self, *a):
        pass

    def _json(self, payload: dict, status: int = 200):
        raw = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path.endswith("/models"):
            self._json({"data": [{"id": "stub-model",
                                  "context_length": self.context_length}]})
            return
        self.send_response(404)
        self.end_headers()

    def _read(self) -> dict:
        n = int(self.headers.get("Content-Length", 0))
        try:
            req = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            req = {}
        self.server.last_request = req
        return req

    def _reject_if_incomplete(self, req: dict) -> bool:
        """Reject a request missing fields a real endpoint requires.

        A stub that quietly accepts a body with no `model` is how the adapter
        shipped a 401 against live Zen while every test passed.
        """
        if not req.get("model"):
            self._json({"error": {"message": "Model {{model}} is not supported"}},
                       status=401)
            return True
        return False


class FullStub(BaseStub):
    """Streams, calls tools, accepts a system prompt."""

    def do_POST(self):
        req = self._read()
        if self._reject_if_incomplete(req):
            return
        if req.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for frame in (
                {"choices": [{"delta": {"content": "Hel"}, "index": 0}]},
                {"choices": [{"delta": {"content": "lo"}, "index": 0}]},
                {"choices": [{"delta": {}, "finish_reason": "stop", "index": 0}],
                 "usage": {"prompt_tokens": 11, "completion_tokens": 2}},
            ):
                try:
                    self.wfile.write(b"data: " + json.dumps(frame).encode() + b"\n\n")
                    self.wfile.flush()
                except OSError:
                    return          # client stopped reading (the probe does)
            try:
                self.wfile.write(b"data: [DONE]\n\n")
            except OSError:
                pass
            return

        if req.get("tools"):
            self._json({"choices": [{"message": {"content": None, "tool_calls": [{
                "id": "call_1", "type": "function",
                "function": {"name": "list_files", "arguments": '{"path": "."}'}}]},
                "finish_reason": "tool_calls", "index": 0}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 5}})
            return
        self._json({"choices": [{"message": {"content": "blocking reply"},
                                 "finish_reason": "stop", "index": 0}],
                    "usage": {"prompt_tokens": 7, "completion_tokens": 2}})


class NoStreamStub(BaseStub):
    """Answers stream=True with a plain JSON body — the Phase 0 failure."""

    def do_POST(self):
        req = self._read()
        if self._reject_if_incomplete(req):
            return
        self._json({"choices": [{"message": {"content": "not a stream"},
                                 "finish_reason": "stop"}]})


class NoToolsStub(BaseStub):
    """400s any request carrying tools, like a small local model."""

    def do_POST(self):
        req = self._read()
        if self._reject_if_incomplete(req):
            return
        if req.get("tools"):
            self._json({"error": {"message": "tools are not supported"}}, status=400)
            return
        self._json({"choices": [{"message": {"content": "text only"},
                                 "finish_reason": "stop"}]})


class StreamingToolStub(BaseStub):
    """Streams a tool call whose arguments arrive in fragments."""

    def do_POST(self):
        req = self._read()
        if self._reject_if_incomplete(req):
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for frame in (
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": "call_9", "type": "function",
                 "function": {"name": "read_file", "arguments": '{"file_'}}]}}]},
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "function": {"arguments": 'path": "a.py"}'}}]}}]},
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        ):
            try:
                self.wfile.write(b"data: " + json.dumps(frame).encode() + b"\n\n")
                self.wfile.flush()
            except OSError:
                return
        try:
            self.wfile.write(b"data: [DONE]\n\n")
        except OSError:
            pass


class KeepAliveStub(BaseStub):
    """Speaks HTTP/1.1 and counts the TCP connections it was handed.

    The counter is the whole point: `urllib.request.urlopen` opened one
    connection per request and said `Connection: close`, so a turn's worth of
    model calls paid for a TLS handshake each. Nothing in a mocked client can
    show that — only a server counting sockets can.

    It also reports cached prompt tokens, which every OpenAI-wire provider that
    caches does and the adapter used to discard.
    """

    protocol_version = "HTTP/1.1"
    timeout = 3          # so a kept-alive socket cannot wedge a test run
    connections = 0
    requests = 0
    _counter_lock = threading.Lock()

    @classmethod
    def reset(cls):
        with cls._counter_lock:
            cls.connections = 0
            cls.requests = 0

    def setup(self):
        with KeepAliveStub._counter_lock:
            KeepAliveStub.connections += 1
        super().setup()

    def do_POST(self):
        req = self._read()
        if self._reject_if_incomplete(req):
            return
        with KeepAliveStub._counter_lock:
            KeepAliveStub.requests += 1
        if req.get("stream"):
            # Real chunked transfer encoding — a streamed response has no
            # Content-Length to declare, and HTTP/1.1 keep-alive means the
            # client cannot fall back to "read until the connection closes"
            # the way it can (and does) against FullStub's HTTP/1.0 response.
            # This framing is exactly what exposes whether the client drains
            # the terminating 0-length chunk before releasing the connection:
            # skip it and the socket still has bytes on it when the *next*
            # request tries to parse a status line from them.
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()

            def _chunk(data: bytes) -> bytes:
                return f"{len(data):x}\r\n".encode() + data + b"\r\n"

            for frame in (
                {"choices": [{"delta": {"content": "ok"}, "index": 0}]},
                {"choices": [{"delta": {}, "finish_reason": "stop", "index": 0}],
                 "usage": {"prompt_tokens": 100, "completion_tokens": 2,
                          "prompt_tokens_details": {"cached_tokens": 90}}},
            ):
                try:
                    self.wfile.write(_chunk(
                        b"data: " + json.dumps(frame).encode() + b"\n\n"))
                    self.wfile.flush()
                except OSError:
                    return
            try:
                self.wfile.write(_chunk(b"data: [DONE]\n\n"))
                self.wfile.write(b"0\r\n\r\n")   # terminating chunk
            except OSError:
                pass
            return
        self._json({"choices": [{"message": {"content": "ok"},
                                 "finish_reason": "stop", "index": 0}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 2,
                              "prompt_tokens_details": {"cached_tokens": 90}}})


class _Server(HTTPServer):
    """HTTPServer whose shutdown() also releases the socket, so a test run
    does not fill the log with ResourceWarnings. Records the last request
    body so tests can assert on what was actually sent upstream."""

    last_request: dict = {}

    def shutdown(self):
        super().shutdown()
        try:
            self.server_close()
        except Exception:
            pass


def serve(handler=FullStub) -> tuple[HTTPServer, str]:
    """Start a stub on an ephemeral port. Returns (server, base_url)."""
    srv = _Server(("127.0.0.1", 0), handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}/v1"
