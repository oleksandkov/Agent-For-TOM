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
import re
import sys
import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent.resolve()
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

CORE_FILES = sorted((PROJECT_DIR / "core").rglob("*.py"))

#: Matched on word boundaries, not as substrings. `print(`/`input(` are the
#: builtins, and a plain `in` test flagged `_cached_input(usage)` — a helper
#: that reads a token count and touches no terminal. A purity rule that fires
#: on any identifier merely *ending* in a banned name teaches people to rename
#: around the check rather than to respect it.
BANNED = (r"\bprint\(", r"\binput\(", r"\bmsvcrt\b", r"\033\[")


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
                    if re.search(banned, line):
                        offences.append(
                            f"{path.name}:{lineno} matches {banned!r} — "
                            f"emit an event instead"
                        )
        self.assertEqual(offences, [], "\n" + "\n".join(offences))

    def test_the_rule_still_catches_what_it_is_for(self):
        """The loosening above must not turn the check into a no-op."""
        for line in ("    print('hi')", "    x = input('name? ')",
                     "    import msvcrt", "    sys.stdout.write('\033[2J')"):
            self.assertTrue(any(re.search(b, line) for b in BANNED),
                            f"purity check no longer catches: {line!r}")

    def test_the_rule_allows_an_identifier_that_merely_ends_in_one(self):
        for line in ("    cached = _cached_input(usage)",
                     "    return _pretty_print(value)"):
            self.assertFalse(any(re.search(b, line) for b in BANNED),
                             f"false positive on: {line!r}")


class TestCoreRunsHeadless(unittest.TestCase):
    """Acceptance criterion: a full turn with tool calls completes with
    stdout redirected to devnull."""

    def test_turn_completes_with_stdout_closed(self):
        from unittest.mock import MagicMock

        from adapters.test import TestAdapter
        from core.loop import run_turn
        from core.state import AgentState
        from core.events import ToolFinished

        # Importable both ways: under `discover -s tests` the tests dir is on
        # sys.path already, but the dotted-module invocation documented in
        # CLAUDE.md (`python -m unittest tests.test_core_purity...`) starts
        # from the project root and would raise "No module named
        # 'test_core_loop'". Put the tests dir on sys.path regardless.
        sys.path.insert(0, str(Path(__file__).parent))
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
