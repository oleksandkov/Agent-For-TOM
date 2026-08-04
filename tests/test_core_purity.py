#!/usr/bin/env python3
"""
Enforcement for Phase 2: the core must stay renderable by a GUI.

Without this test the split decays back within a month. When someone needs to
print from core/, the failure tells them to emit an event instead — which is
exactly the conversation worth having.

Run: python -m unittest discover -s tests -p "test_*.py"
"""
import io
import os
import sys
import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent.resolve()
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

CORE_FILES = sorted((PROJECT_DIR / "core").rglob("*.py"))

BANNED = ("print(", "input(", "msvcrt", "\033[")


class TestCoreHasNoTerminalCoupling(unittest.TestCase):
    def test_core_package_is_not_empty(self):
        """Guard against the test silently passing because core/ moved."""
        self.assertTrue(CORE_FILES, "no files found under core/")

    def test_core_has_no_terminal_coupling(self):
        offences = []
        for path in CORE_FILES:
            for lineno, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            ):
                for banned in BANNED:
                    if banned in line:
                        offences.append(
                            f"{path.name}:{lineno} contains {banned!r} — "
                            f"emit an event instead"
                        )
        self.assertEqual(offences, [], "\n" + "\n".join(offences))


class TestCoreRunsHeadless(unittest.TestCase):
    """Acceptance criterion: a full turn with tool calls completes with
    stdout redirected to devnull."""

    def test_turn_completes_with_stdout_closed(self):
        from unittest.mock import MagicMock

        from adapters.test import TestAdapter
        from core.loop import run_turn
        from core.state import AgentState
        from core.events import ToolFinished

        from test_core_loop import FakeResponse, text_block, tool_block

        client = MagicMock()
        client.messages.create.side_effect = [
            FakeResponse([tool_block()], "tool_use"),
            FakeResponse([text_block("done")], "end_turn"),
        ]

        adapter = TestAdapter()
        state = AgentState(
            system_prompt="sys",
            messages=[],
            get_client=lambda: client,
            get_model=lambda: "test-model",
            responder=adapter,
            execute_tool=lambda n, a: "ok",
            streaming_enabled=False,
        )

        orig_stdout = sys.stdout
        sys.stdout = open(os.devnull, "w")
        try:
            reply = adapter.run(state, "list the files")
        finally:
            sys.stdout.close()
            sys.stdout = orig_stdout

        self.assertEqual(reply, "done")
        self.assertTrue(adapter.of(ToolFinished))


if __name__ == "__main__":
    unittest.main()
