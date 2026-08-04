#!/usr/bin/env python3
"""
Regression tests for zen_proxy (Phase 0).

zen_proxy had zero tests despite being the single largest source of production
failures found in QA: every streamed request returned 502 because the proxy
forwarded stream=true upstream and then tried to json.loads() an SSE body.

Run: python -m unittest discover -s tests -p "test_*.py"
"""
import json
import sys
import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent.resolve()
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import zen_proxy


class FakeWFile:
    """Captures what the handler writes to the socket."""

    def __init__(self):
        self.chunks = []

    def write(self, data):
        self.chunks.append(data)

    def flush(self):
        pass

    @property
    def text(self):
        return b"".join(self.chunks).decode("utf-8")


class SSEHandler(zen_proxy.ZenProxyHandler):
    """The real handler with the socket plumbing stubbed out.

    BaseHTTPRequestHandler.__init__ is what binds a connection, so we simply
    don't call it — everything else on the class stays real.
    """

    def __init__(self):  # noqa: D107 - deliberately does not call super()
        self.wfile = FakeWFile()
        self.headers_sent = {}
        self.status = None

    def send_response(self, code):
        self.status = code

    def send_header(self, k, v):
        self.headers_sent[k] = v

    def end_headers(self):
        pass


def parse_sse(raw: str):
    """Return [(event_name, data_dict), ...] from an SSE payload."""
    out = []
    for frame in raw.strip().split("\n\n"):
        if not frame.strip():
            continue
        name, data = None, None
        for line in frame.splitlines():
            if line.startswith("event: "):
                name = line[len("event: "):]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: "):])
        out.append((name, data))
    return out


TEXT_RESPONSE = {
    "id": "msg_1",
    "type": "message",
    "role": "assistant",
    "model": "test-model",
    "content": [{"type": "text", "text": "hello world"}],
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "usage": {"input_tokens": 10, "output_tokens": 3},
}

TOOL_RESPONSE = {
    "id": "msg_2",
    "type": "message",
    "role": "assistant",
    "model": "test-model",
    "content": [{"type": "tool_use", "id": "tu_1", "name": "read_file",
                 "input": {"file_path": "agent.py"}}],
    "stop_reason": "tool_use",
    "stop_sequence": None,
    "usage": {"input_tokens": 10, "output_tokens": 7},
}


class TestAnthropicSSE(unittest.TestCase):
    """The frame sequence the Anthropic SDK's .stream() requires."""

    REQUIRED_ORDER = [
        "message_start", "content_block_start", "content_block_delta",
        "content_block_stop", "message_delta", "message_stop",
    ]

    def render(self, ant_resp):
        h = SSEHandler()
        zen_proxy.ZenProxyHandler._send_anthropic_sse(h, ant_resp)
        return h, parse_sse(h.wfile.text)

    def test_text_response_frame_sequence(self):
        h, frames = self.render(TEXT_RESPONSE)
        self.assertEqual([n for n, _ in frames], self.REQUIRED_ORDER)
        self.assertEqual(h.status, 200)
        self.assertEqual(h.headers_sent.get("Content-Type"), "text/event-stream")

    def test_text_is_delivered_in_the_delta(self):
        _, frames = self.render(TEXT_RESPONSE)
        delta = dict(frames)["content_block_delta"]
        self.assertEqual(delta["delta"]["type"], "text_delta")
        self.assertEqual(delta["delta"]["text"], "hello world")

    def test_message_start_has_empty_content(self):
        """The SDK accumulates content from the block events; a populated
        content list in message_start would double it."""
        _, frames = self.render(TEXT_RESPONSE)
        msg = dict(frames)["message_start"]["message"]
        self.assertEqual(msg["content"], [])
        self.assertEqual(msg["id"], "msg_1")

    def test_tool_use_is_streamed_as_input_json_delta(self):
        _, frames = self.render(TOOL_RESPONSE)
        by_name = dict(frames)
        start = by_name["content_block_start"]["content_block"]
        self.assertEqual(start["type"], "tool_use")
        self.assertEqual(start["name"], "read_file")
        self.assertEqual(start["input"], {}, "input arrives via the delta")

        delta = by_name["content_block_delta"]["delta"]
        self.assertEqual(delta["type"], "input_json_delta")
        self.assertEqual(json.loads(delta["partial_json"]),
                         {"file_path": "agent.py"})

    def test_stop_reason_and_usage_are_reported(self):
        _, frames = self.render(TOOL_RESPONSE)
        md = dict(frames)["message_delta"]
        self.assertEqual(md["delta"]["stop_reason"], "tool_use")
        self.assertEqual(md["usage"]["output_tokens"], 7)

    def test_multiple_blocks_are_indexed(self):
        resp = dict(TEXT_RESPONSE)
        resp["content"] = [{"type": "text", "text": "a"},
                           {"type": "text", "text": "b"}]
        _, frames = self.render(resp)
        starts = [d["index"] for n, d in frames if n == "content_block_start"]
        self.assertEqual(starts, [0, 1])

    def test_empty_content_still_produces_a_valid_stream(self):
        resp = dict(TEXT_RESPONSE)
        resp["content"] = []
        _, frames = self.render(resp)
        self.assertEqual([n for n, _ in frames],
                         ["message_start", "message_delta", "message_stop"])


class TestUpstreamNeverStreams(unittest.TestCase):
    """The proxy must always request a complete response upstream —
    _upstream_request/json.loads cannot consume an SSE body."""

    def test_handler_source_forces_stream_false(self):
        src = Path(PROJECT_DIR / "zen_proxy.py").read_text(encoding="utf-8")
        self.assertIn('oai_body["stream"] = False', src)
        self.assertNotIn('oai_body["stream"] = stream', src)


if __name__ == "__main__":
    unittest.main()
