#!/usr/bin/env python3
"""
Chat experience (Phase 7, Part B).

Run: python -m unittest tests.test_chat_ux -v
"""
import sys
import time
import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent.resolve()
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import agent
import mcp_manager
import text_display as td
from core.events import PermissionNeeded
from core.state import AgentState


class _Denier:
    """A responder that always says no, and counts how often it is asked."""

    def __init__(self):
        self.asks = 0

    def ask(self, event) -> str:
        self.asks += 1
        return "deny"

    def ask_continue(self, event) -> bool:
        return False


# ══════════════════════════════════════════════════════════════════════
#  P7-7 — a denial must stop the retry loop
# ══════════════════════════════════════════════════════════════════════

class TestDenialSemantics(unittest.TestCase):

    def test_denial_says_a_retry_will_fail(self):
        """Regression: 'user denied this tool call' read as transient, so the
        model reissued the same command six times in one observed turn."""
        source = (PROJECT_DIR / "core" / "loop.py").read_text(encoding="utf-8")
        self.assertIn("will be denied", source)
        self.assertIn("Do not re-issue", source)

    def test_second_denial_escalates(self):
        source = (PROJECT_DIR / "core" / "loop.py").read_text(encoding="utf-8")
        self.assertIn("denials >= 2", source)

    def test_text_protocol_denial_also_says_so(self):
        source = (PROJECT_DIR / "agent.py").read_text(encoding="utf-8")
        self.assertIn("denied again — do not re-issue", source)

    def test_non_interactive_adapter_denies_without_prompting(self):
        from adapters.terminal import TerminalAdapter
        adapter = TerminalAdapter(interactive=False)
        event = PermissionNeeded("id", "run_command", {"command": "del x"}, "high")
        self.assertEqual(adapter.ask(event), "deny")

    def test_non_interactive_notice_is_shown_once(self):
        import io
        from adapters.terminal import TerminalAdapter
        adapter = TerminalAdapter(interactive=False)
        event = PermissionNeeded("id", "run_command", {"command": "del x"}, "high")
        saved, buf = sys.stdout, io.StringIO()
        sys.stdout = buf
        try:
            for _ in range(5):
                adapter.ask(event)
        finally:
            sys.stdout = saved
        self.assertEqual(buf.getvalue().count("non-interactive"), 1)


# ══════════════════════════════════════════════════════════════════════
#  P7-7 — the model is told which shell it has
# ══════════════════════════════════════════════════════════════════════

class TestEnvironmentAwareness(unittest.TestCase):

    def test_prompt_names_the_shell(self):
        """It reached for rm / ls / test -f on cmd.exe and burned tool calls
        discovering they do not exist."""
        prompt = agent.build_system_prompt("anything")
        self.assertIn("# Environment", prompt)
        if sys.platform == "win32":
            self.assertIn("cmd.exe", prompt)
            self.assertIn("findstr", prompt)

    def test_prompt_names_the_interpreter(self):
        self.assertIn(sys.executable, agent.build_system_prompt(""))

    def test_prompt_asks_for_the_user_s_language(self):
        self.assertIn("language the user wrote in",
                      agent.build_system_prompt(""))


# ══════════════════════════════════════════════════════════════════════
#  P7-3 — startup
# ══════════════════════════════════════════════════════════════════════

class _SlowServer:
    """A stub that takes `delay` seconds to connect."""

    def __init__(self, name, delay=0.4, ok=True, tools=None):
        self.name = name
        self.delay = delay
        self._ok = ok
        self.tools = tools if tools is not None else [
            {"name": f"{name}_tool", "inputSchema": {"type": "object"}}]
        self.resources, self.prompts = [], []
        self._last_error = None if ok else "stub failure"

    def connect(self):
        time.sleep(self.delay)
        return self._ok


class TestParallelConnect(unittest.TestCase):

    def _run(self, servers, parallel):
        by_name = {s.name: s for s in servers}
        original = mcp_manager.MCPServer
        mcp_manager.MCPServer = lambda name, cfg: by_name[name]
        try:
            mgr = mcp_manager.MCPManager()
            t0 = time.perf_counter()
            mgr.discover_and_connect(config={s.name: {} for s in servers},
                                     parallel=parallel)
            return mgr, time.perf_counter() - t0
        finally:
            mcp_manager.MCPServer = original

    def test_parallel_is_faster_than_serial(self):
        servers = [_SlowServer(f"s{i}", delay=0.3) for i in range(8)]
        _, t_par = self._run(servers, parallel=True)
        _, t_ser = self._run(servers, parallel=False)
        self.assertLess(t_par, t_ser / 2,
                        f"parallel {t_par:.2f}s vs serial {t_ser:.2f}s")

    def test_exposed_names_are_identical_either_way(self):
        """Determinism: which server wins an uncontested name must not depend
        on which thread finished first."""
        def servers():
            return [_SlowServer("a", 0.30, tools=[{"name": "dup", "inputSchema": {}}]),
                    _SlowServer("b", 0.05, tools=[{"name": "dup", "inputSchema": {}}]),
                    _SlowServer("c", 0.01, tools=[{"name": "dup", "inputSchema": {}}])]
        par, _ = self._run(servers(), parallel=True)
        ser, _ = self._run(servers(), parallel=False)
        self.assertEqual([t["name"] for t in par.tools],
                         [t["name"] for t in ser.tools])
        # The slowest server is first in config order, so it keeps the name.
        self.assertEqual(par.tools[0]["name"], "dup")
        self.assertEqual(par.get_server_for_tool("dup"), "a")

    def test_failures_are_recorded_not_raised(self):
        mgr, _ = self._run([_SlowServer("ok", 0.01),
                            _SlowServer("bad", 0.01, ok=False)], parallel=True)
        self.assertIn("bad", mgr.failed_servers)
        self.assertIn("ok", mgr.servers)

    def test_a_raising_server_does_not_kill_startup(self):
        class Exploding(_SlowServer):
            def connect(self):
                raise RuntimeError("boom")
        mgr, _ = self._run([_SlowServer("fine", 0.01), Exploding("boom", 0.01)],
                           parallel=True)
        self.assertIn("fine", mgr.servers)
        self.assertIn("boom", mgr.failed_servers)

    def test_no_duplicate_names_under_concurrency(self):
        servers = [_SlowServer(f"s{i}", 0.05,
                               tools=[{"name": "shared", "inputSchema": {}}])
                   for i in range(6)]
        mgr, _ = self._run(servers, parallel=True)
        names = [t["name"] for t in mgr.tools]
        self.assertEqual(len(names), len(set(names)))


# ══════════════════════════════════════════════════════════════════════
#  P7-8 — startup noise
# ══════════════════════════════════════════════════════════════════════

class TestFailureClassification(unittest.TestCase):

    def test_auth_failures_are_not_errors(self):
        auth, broken = agent._classify_mcp_failures({
            "github": "HTTP 401: Unauthorized",
            "supabase": "HTTP 403: Forbidden",
            "vercel": "authentication required",
            "toolbox": "initialize failed: None",
            "linear": "Expecting value: line 1 column 1",
        })
        self.assertEqual(sorted(auth), ["github", "supabase", "vercel"])
        self.assertEqual(sorted(broken), ["linear", "toolbox"])

    def test_empty_input(self):
        self.assertEqual(agent._classify_mcp_failures({}), ([], []))


# ══════════════════════════════════════════════════════════════════════
#  P7-6 — width awareness in the renderer
# ══════════════════════════════════════════════════════════════════════

class TestRendererWidth(unittest.TestCase):

    def test_rule_matches_the_terminal(self):
        self.assertEqual(td.display_width(td.rule(width=50, indent=2)), 48)

    def test_term_width_is_clamped(self):
        w = td.term_width()
        self.assertGreaterEqual(w, td.MIN_WIDTH)
        self.assertLessEqual(w, td.MAX_WIDTH)

    def test_renderer_imports_the_display_helpers(self):
        """One implementation of width, shared by the REPL and the renderer."""
        source = (PROJECT_DIR / "adapters" / "terminal.py").read_text(encoding="utf-8")
        self.assertIn("from text_display import", source)

    def test_assistant_text_is_wrapped(self):
        source = (PROJECT_DIR / "adapters" / "terminal.py").read_text(encoding="utf-8")
        self.assertIn("print(wrap(event.text))", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
