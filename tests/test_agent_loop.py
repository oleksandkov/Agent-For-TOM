#!/usr/bin/env python3
"""
Terminal-facing regression tests (Phase 0).

The agent-loop tests that used to live here were rewritten against the core's
event stream in test_core_loop.py when the loop moved to core/loop.py in the
core/UI split (see docs/HISTORY.md). What remains here is the part that is
genuinely about the terminal front end and has no core equivalent.

Run: python -m unittest discover -s tests -p "test_*.py"
"""
import io
import sys
import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent.resolve()
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import agent


class TestConsoleEncoding(unittest.TestCase):
    """P0-3: the UI prints glyphs that crash a non-UTF-8 Windows console."""

    def test_ui_glyphs_survive_a_legacy_codepage(self):
        buf = io.TextIOWrapper(io.BytesIO(), encoding="cp1251", errors="replace")
        glyphs = f"{agent.MAGENTA}{agent.BOLD}▌ TOMAS{agent.RESET} ✦ ⚙ ⚡"
        try:
            buf.write(glyphs)   # must not raise UnicodeEncodeError
        finally:
            buf.close()

    @unittest.skipUnless(sys.platform == "win32", "Windows-only behaviour")
    def test_stdout_is_utf8_after_import(self):
        self.assertEqual((sys.stdout.encoding or "").lower().replace("-", ""),
                         "utf8")


class TestAgentLoopShim(unittest.TestCase):
    """agent.agent_loop is now a shim over core.loop.run_turn. It must keep
    the signature its existing callers rely on."""

    def test_shim_drives_the_core_and_returns_the_reply(self):
        from unittest.mock import MagicMock
        sys.path.insert(0, str(PROJECT_DIR / "tests"))
        from test_core_loop import FakeResponse, text_block, tool_block

        client = MagicMock()
        client.messages.create.side_effect = [
            FakeResponse([tool_block()], "tool_use"),
            FakeResponse([text_block("done")], "end_turn"),
        ]

        orig_client, orig_stream = agent._get_client, agent._streaming_disabled
        orig_yolo, orig_stdout = agent.YOLO_MODE, sys.stdout
        agent._get_client = lambda: client
        agent._streaming_disabled = True
        agent.YOLO_MODE = True
        sys.stdout = io.StringIO()
        try:
            messages = [{"role": "user", "content": "list the files"}]
            reply = agent.agent_loop("sys", messages)
        finally:
            sys.stdout = orig_stdout
            agent._get_client = orig_client
            agent._streaming_disabled = orig_stream
            agent.YOLO_MODE = orig_yolo

        self.assertEqual(reply, "done")
        self.assertEqual([m["role"] for m in messages],
                         ["user", "assistant", "user", "assistant"])


if __name__ == "__main__":
    unittest.main()
