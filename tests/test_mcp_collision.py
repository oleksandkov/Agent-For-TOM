#!/usr/bin/env python3
"""
Unit tests for MCP tool name collision resolution, prefixing, and routing.

Covers:
  - resolve_mcp_tool_conflicts: mcp_ prefixing, reverse name map, descriptions
  - apply_tool_cap: 128-tool / 32-tool free-tier truncation, built-ins first
  - tool_ceiling: capability-driven, not sniffed from URL or model name
  - execute_tool: rename resolution (mcp_X -> X), built-in precedence, unknown tool
  - MCPManager.call_tool: routing to the owning server, cross-server collisions
  - _to_anthropic_tool: inputSchema -> input_schema conversion

Run: python -m unittest tests.test_mcp_collision -v
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent.resolve()
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import agent
import provider_manager as pm
from mcp_manager import MCPManager

BUILTIN_NAMES = {t["name"] for t in agent.TOOLS}


def mcp_tool(name, desc="mcp tool", schema=None):
    """Build an MCP-style tool descriptor (inputSchema camelCase)."""
    return {
        "name": name,
        "description": desc,
        "inputSchema": schema or {
            "type": "object",
            "properties": {"x": {"type": "string"}},
        },
    }


class FakeMCPServer:
    """Minimal stand-in for mcp_manager.MCPServer."""
    def __init__(self, name, tools):
        self.name = name
        self.tools = tools
        self.calls = []
        self._last_error = None

    def connect(self):
        return True

    def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return f"ok:{name}"


def connect_manager(*servers):
    """Build an MCPManager wired to fake servers through the real
    discover_and_connect path, which is what assigns exposed tool names."""
    import mcp_manager as mm
    by_name = {s.name: s for s in servers}
    original = mm.MCPServer
    mm.MCPServer = lambda name, cfg: by_name[name]
    try:
        mgr = MCPManager()
        mgr.discover_and_connect(config={s.name: {} for s in servers})
    finally:
        mm.MCPServer = original
    return mgr


class FakeMCPManager:
    """Stand-in manager recording what tool name reaches the server."""
    def __init__(self, server):
        self.server = server

    def call_tool(self, name, arguments):
        # Emulate real MCPManager.call_tool: only route if a server owns the tool
        if self.get_server_for_tool(name) is None:
            return f"Error: MCP tool '{name}' not found on any connected server."
        return self.server.call_tool(name, arguments)

    def get_server_for_tool(self, name):
        return self.server.name if any(t["name"] == name for t in self.server.tools) else None


class TestConflictResolution(unittest.TestCase):
    """resolve_mcp_tool_conflicts — prefixing and reverse map."""

    def test_colliding_tool_gets_mcp_prefix(self):
        tool = mcp_tool("read_file", "reads stuff")  # read_file is built-in
        resolved, name_map, renames = agent.resolve_mcp_tool_conflicts([tool])
        self.assertEqual(renames, 1)
        self.assertEqual(resolved[0]["name"], "mcp_read_file")
        self.assertEqual(resolved[0]["description"], "[MCP: read_file] reads stuff")
        self.assertEqual(name_map, {"mcp_read_file": "read_file"})
        # Original descriptor is not mutated
        self.assertEqual(tool["name"], "read_file")

    def test_unique_tool_keeps_name(self):
        tool = mcp_tool("git_status", "repo state")
        resolved, name_map, renames = agent.resolve_mcp_tool_conflicts([tool])
        self.assertEqual(renames, 0)
        self.assertEqual(resolved[0]["name"], "git_status")
        self.assertEqual(name_map, {})

    def test_mixed_collisions_and_uniques(self):
        tools = [
            mcp_tool("read_file"),          # built-in -> mcp_read_file
            mcp_tool("run_command"),        # built-in -> mcp_run_command
            mcp_tool("custom_tool"),        # unique
            mcp_tool("search_web"),         # built-in -> mcp_search_web
        ]
        resolved, name_map, renames = agent.resolve_mcp_tool_conflicts(tools)
        self.assertEqual(renames, 3)
        self.assertEqual(
            [t["name"] for t in resolved],
            ["mcp_read_file", "mcp_run_command", "custom_tool", "mcp_search_web"],
        )
        self.assertEqual(name_map["mcp_read_file"], "read_file")
        self.assertEqual(name_map["mcp_run_command"], "run_command")
        self.assertEqual(name_map["mcp_search_web"], "search_web")
        self.assertNotIn("custom_tool", name_map)

    def test_custom_builtin_names(self):
        tool = mcp_tool("foo")
        resolved, _, renames = agent.resolve_mcp_tool_conflicts([tool], builtin_names={"foo"})
        self.assertEqual(renames, 1)
        self.assertEqual(resolved[0]["name"], "mcp_foo")

    def test_empty_input(self):
        resolved, name_map, renames = agent.resolve_mcp_tool_conflicts([])
        self.assertEqual(resolved, [])
        self.assertEqual(name_map, {})
        self.assertEqual(renames, 0)


class TestToolCap(unittest.TestCase):
    """apply_tool_cap — built-ins preserved, excess MCP tools dropped."""

    def test_under_cap_keeps_everything(self):
        tools = [mcp_tool(f"t{i}") for i in range(5)]
        combined, dropped = agent.apply_tool_cap(tools, max_allowed=128)
        self.assertEqual(dropped, 0)
        self.assertEqual(len(combined), len(agent.TOOLS) + 5)

    def test_over_cap_truncates_mcp_only(self):
        tools = [mcp_tool(f"t{i}") for i in range(200)]
        combined, dropped = agent.apply_tool_cap(tools, max_allowed=128)
        self.assertEqual(len(combined), 128)
        self.assertEqual(dropped, 200 - (128 - len(agent.TOOLS)))
        # Built-ins come first, untouched
        self.assertEqual(combined[:len(agent.TOOLS)], agent.TOOLS)
        # Dropped ones are the tail
        self.assertEqual(combined[-1]["name"], f"t{127 - len(agent.TOOLS)}")

    def test_free_tier_cap_32(self):
        tools = [mcp_tool(f"t{i}") for i in range(50)]
        combined, dropped = agent.apply_tool_cap(tools, max_allowed=32)
        self.assertEqual(len(combined), 32)
        self.assertEqual(dropped, 50 - (32 - len(agent.TOOLS)))

    def test_cap_smaller_than_builtins_keeps_builtins(self):
        tools = [mcp_tool(f"t{i}") for i in range(3)]
        combined, dropped = agent.apply_tool_cap(tools, max_allowed=len(agent.TOOLS))
        self.assertEqual(combined, agent.TOOLS)
        self.assertEqual(dropped, 3)


class TestToolCeilingIsNotSniffed(unittest.TestCase):
    """The tool ceiling comes from probed capabilities (P4-2).

    It used to be `is_free_tier_model()`, which returned True when the model
    name contained "free" or the URL contained "openrouter" / "127.0.0.1".
    That function is deleted; these tests guard against it — or an equivalent
    heuristic — coming back.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._saved = pm.PROVIDERS_CONFIG_PATH
        pm.PROVIDERS_CONFIG_PATH = Path(self._tmp.name) / "providers.json"

    def tearDown(self):
        pm.PROVIDERS_CONFIG_PATH = self._saved
        self._tmp.cleanup()
        os.environ.pop("ANTHROPIC_BASE_URL", None)

    def test_function_is_gone(self):
        self.assertFalse(hasattr(agent, "is_free_tier_model"))

    def test_model_named_free_keeps_its_full_budget(self):
        """The bug the deletion fixes: a model called `my-free-model` lost
        75% of its tool budget for its name."""
        p = pm.Provider(name="P", type="anthropic", model="my-free-model")
        p.capabilities.max_tools = 128
        p.capabilities.probed_at = 1.0
        pm.save(p)
        self.assertEqual(agent.tool_ceiling(), 128)

    def test_base_url_does_not_change_the_ceiling(self):
        p = pm.Provider(name="P", type="anthropic", model="m")
        p.capabilities.max_tools = 96
        p.capabilities.probed_at = 1.0
        pm.save(p)
        for url in ("https://openrouter.ai/api/v1", "http://127.0.0.1:6446",
                    "https://api.anthropic.com"):
            os.environ["ANTHROPIC_BASE_URL"] = url
            self.assertEqual(agent.tool_ceiling(), 96, url)

    def test_probed_ceiling_is_used(self):
        p = pm.Provider(name="P", type="custom", model="m")
        p.capabilities.max_tools = 32
        p.capabilities.probed_at = 1.0
        pm.save(p)
        self.assertEqual(agent.tool_ceiling(), 32)

    def test_no_substring_heuristics_left_in_the_source(self):
        source = (PROJECT_DIR / "agent.py").read_text(encoding="utf-8")
        self.assertNotIn('"free" in model_name', source)
        self.assertNotIn('is_free_tier_model', source)


class TestExecuteToolRouting(unittest.TestCase):
    """execute_tool — built-in precedence, rename resolution, unknown tool."""

    def setUp(self):
        self._orig_manager = agent.mcp_manager
        self._orig_map = agent.MCP_TOOL_NAME_MAP

    def tearDown(self):
        agent.mcp_manager = self._orig_manager
        agent.MCP_TOOL_NAME_MAP = self._orig_map

    def test_builtin_wins_over_mcp(self):
        server = FakeMCPServer("srv", [mcp_tool("read_file", "mcp read")])
        agent.mcp_manager = FakeMCPManager(server)
        agent.MCP_TOOL_NAME_MAP = {"mcp_read_file": "read_file"}
        # Built-in handler intercepts; MCP server must NOT be called
        result = agent.execute_tool("read_file", {"file_path": "AGENTS.md"})
        self.assertIn("Agent-For-TOM", result)
        self.assertEqual(server.calls, [])

    def test_renamed_tool_routes_to_original_name(self):
        server = FakeMCPServer("srv", [mcp_tool("read_file")])
        agent.mcp_manager = FakeMCPManager(server)
        agent.MCP_TOOL_NAME_MAP = {"mcp_read_file": "read_file"}
        result = agent.execute_tool("mcp_read_file", {"file_path": "x"})
        self.assertEqual(server.calls, [("read_file", {"file_path": "x"})])
        self.assertEqual(result, "ok:read_file")

    def test_unrenamed_mcp_tool_routes_as_is(self):
        server = FakeMCPServer("srv", [mcp_tool("git_status")])
        agent.mcp_manager = FakeMCPManager(server)
        agent.MCP_TOOL_NAME_MAP = {}
        result = agent.execute_tool("git_status", {"path": "."})
        self.assertEqual(server.calls, [("git_status", {"path": "."})])
        self.assertEqual(result, "ok:git_status")

    def test_no_manager_unknown_tool(self):
        agent.mcp_manager = None
        result = agent.execute_tool("nonexistent_tool", {})
        self.assertIn("unknown tool", result)

    def test_manager_unknown_tool(self):
        server = FakeMCPServer("srv", [mcp_tool("git_status")])
        agent.mcp_manager = FakeMCPManager(server)
        agent.MCP_TOOL_NAME_MAP = {}
        result = agent.execute_tool("does_not_exist", {})
        self.assertIn("not found", result)

    def test_tool_origin_provenance(self):
        server = FakeMCPServer("srv", [mcp_tool("git_status")])
        agent.mcp_manager = FakeMCPManager(server)
        agent.MCP_TOOL_NAME_MAP = {"mcp_read_file": "read_file"}
        self.assertEqual(agent._tool_origin("read_file"), "built-in")
        self.assertEqual(agent._tool_origin("git_status"), "MCP: srv")
        # Renamed tool resolves back to original before server lookup
        self.assertEqual(agent._tool_origin("mcp_read_file"), "built-in")


class TestManagerRouting(unittest.TestCase):
    """MCPManager.call_tool — routing by owning server (P6-7)."""

    def test_routes_to_correct_server(self):
        server_a = FakeMCPServer("a", [mcp_tool("foo")])
        server_b = FakeMCPServer("b", [mcp_tool("bar")])
        mgr = connect_manager(server_a, server_b)
        mgr.call_tool("bar", {"k": 1})
        self.assertEqual(server_b.calls, [("bar", {"k": 1})])
        self.assertEqual(server_a.calls, [])

    def test_cross_server_tool_collision(self):
        """Both servers stay reachable when they expose the same tool name.

        Regression for bug 5: routing took the first match in insertion
        order, so the second server's tool was permanently unreachable.
        """
        server_a = FakeMCPServer("chrome", [mcp_tool("take_screenshot")])
        server_b = FakeMCPServer("playwright", [mcp_tool("take_screenshot")])
        mgr = connect_manager(server_a, server_b)

        names = [t["name"] for t in mgr.tools]
        self.assertEqual(names, ["take_screenshot", "mcp_playwright_take_screenshot"])

        mgr.call_tool("take_screenshot", {})
        mgr.call_tool("mcp_playwright_take_screenshot", {"k": 2})
        # Each server is reached, and each receives its ORIGINAL tool name.
        self.assertEqual(server_a.calls, [("take_screenshot", {})])
        self.assertEqual(server_b.calls, [("take_screenshot", {"k": 2})])

    def test_no_duplicate_names_in_tool_list(self):
        """Duplicate names in the payload are a protocol error, independent
        of routing."""
        mgr = connect_manager(
            FakeMCPServer("a", [mcp_tool("dup"), mcp_tool("solo_a")]),
            FakeMCPServer("b", [mcp_tool("dup"), mcp_tool("solo_b")]),
            FakeMCPServer("c", [mcp_tool("dup")]),
        )
        names = [t["name"] for t in mgr.tools]
        self.assertEqual(len(names), len(set(names)), f"duplicates in {names}")
        self.assertEqual(len(names), 5)

    def test_uncontested_name_is_not_prefixed(self):
        """One server owning a name keeps that name — only the loser moves."""
        mgr = connect_manager(FakeMCPServer("a", [mcp_tool("solo")]))
        self.assertEqual([t["name"] for t in mgr.tools], ["solo"])

    def test_unknown_tool(self):
        mgr = connect_manager(FakeMCPServer("a", [mcp_tool("foo")]))
        result = mgr.call_tool("nope", {})
        self.assertIn("not found", result)

    def test_get_server_for_tool(self):
        mgr = connect_manager(
            FakeMCPServer("a", [mcp_tool("foo")]),
            FakeMCPServer("b", [mcp_tool("foo")]),
        )
        self.assertEqual(mgr.get_server_for_tool("foo"), "a")
        self.assertEqual(mgr.get_server_for_tool("mcp_b_foo"), "b")
        self.assertIsNone(mgr.get_server_for_tool("nope"))


class TestAnthropicConversion(unittest.TestCase):
    """_to_anthropic_tool — MCP inputSchema -> Anthropic input_schema."""

    def test_schema_conversion(self):
        result = MCPManager._to_anthropic_tool(mcp_tool("foo", "does foo"))
        self.assertEqual(result["name"], "foo")
        self.assertEqual(result["description"], "does foo")
        self.assertIn("input_schema", result)
        self.assertNotIn("inputSchema", result)
        self.assertEqual(result["input_schema"]["type"], "object")

    def test_description_falls_back_to_title(self):
        tool = {"name": "bar", "title": "The Bar Tool", "inputSchema": {"type": "object"}}
        result = MCPManager._to_anthropic_tool(tool)
        self.assertEqual(result["description"], "The Bar Tool")

    def test_missing_schema_defaults(self):
        result = MCPManager._to_anthropic_tool({"name": "baz"})
        self.assertEqual(result["input_schema"], {"type": "object"})


class _FakePipe:
    """Stands in for the server's stdout: hands back queued lines."""

    def __init__(self, lines):
        self._lines = [l.encode("utf-8") if isinstance(l, str) else l
                       for l in lines]

    def readline(self):
        return self._lines.pop(0) if self._lines else b""


class _FakeProc:
    def __init__(self, lines):
        self.stdout = _FakePipe(lines)
        self.stdin = None


class TestStdioNoiseDoesNotDesync(unittest.TestCase):
    """Non-JSON on stdout must cost one line, not the whole connection.

    The MCP spec says a stdio server writes only JSON-RPC to stdout; real
    servers break that. `_stdio_recv` used to json.loads exactly one line, so
    one warning raised JSONDecodeError *and* left the real reply queued —
    every later call then read the previous call's response, one off forever.
    Session 20260804_111107 hit this: after a single failed convert_to_pdf,
    get_document_text and add_paragraph — both working moments earlier — kept
    raising the same error, so the model abandoned the document tools and
    hand-rolled scripts instead. The conversion had actually succeeded.
    """

    def server(self, lines):
        from mcp_manager import MCPServer
        srv = MCPServer("noisy", {"type": "stdio", "command": "x"})
        srv._proc = _FakeProc(lines)
        srv.connected = True
        return srv

    def test_a_warning_line_is_skipped(self):
        srv = self.server(['UserWarning: deprecated\n',
                           '{"jsonrpc":"2.0","id":1,"result":{"content":'
                           '[{"type":"text","text":"converted"}]}}\n'])
        self.assertEqual(srv._stdio_call({"id": 1}), "converted")

    def test_the_connection_survives_for_the_next_call(self):
        srv = self.server(['noise\n',
                           '{"jsonrpc":"2.0","id":1,"result":{"content":'
                           '[{"type":"text","text":"first"}]}}\n',
                           '{"jsonrpc":"2.0","id":2,"result":{"content":'
                           '[{"type":"text","text":"second"}]}}\n'])
        self.assertEqual(srv._stdio_call({"id": 1}), "first")
        self.assertEqual(srv._stdio_call({"id": 2}), "second")

    def test_a_stale_reply_is_not_returned_for_this_request(self):
        srv = self.server(['{"jsonrpc":"2.0","id":1,"result":{"content":'
                           '[{"type":"text","text":"stale"}]}}\n',
                           '{"jsonrpc":"2.0","id":2,"result":{"content":'
                           '[{"type":"text","text":"mine"}]}}\n'])
        self.assertEqual(srv._stdio_call({"id": 2}), "mine")

    def test_a_dead_server_reports_instead_of_raising(self):
        srv = self.server([])          # EOF immediately
        out = srv._stdio_call({"id": 1})
        self.assertTrue(out.startswith("Error:"), out)
        self.assertFalse(srv.connected)

    def test_endless_noise_terminates(self):
        srv = self.server(['garbage\n'] * 5000)
        out = srv._stdio_call({"id": 1})
        self.assertTrue(out.startswith("Error:"), out)


if __name__ == "__main__":
    unittest.main()
