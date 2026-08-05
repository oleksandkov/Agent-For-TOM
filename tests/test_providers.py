#!/usr/bin/env python3
"""
Provider layer tests (Phase 4).

Covers provider_manager (config, type detection, probing, capabilities),
openai_adapter (translation, real streaming, degradation), and the
capability-driven paths in agent.py.

The slow real-provider matrix at the bottom is the single most valuable test
here — it is exactly what was broken for every provider before Phase 0. It
skips providers without credentials, so a normal run does not need network.

Run:            python -m unittest tests.test_providers -v
Real providers: TOMAS_TEST_PROVIDERS=1 python -m unittest tests.test_providers -v
"""
import json
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
import openai_adapter
from tests import stub_provider


class ConfigTestCase(unittest.TestCase):
    """Redirects provider config to a temp file so real config is untouched."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self._saved = (pm.PROVIDERS_CONFIG_PATH, pm.ENV_FILE)
        pm.PROVIDERS_CONFIG_PATH = tmp / "providers.json"
        pm.ENV_FILE = tmp / ".env"

    def tearDown(self):
        pm.PROVIDERS_CONFIG_PATH, pm.ENV_FILE = self._saved
        self._tmp.cleanup()


# ══════════════════════════════════════════════════════════════════════
#  P4-1 — provider logic outside the TUI
# ══════════════════════════════════════════════════════════════════════

class TestProviderManagerIsUIFree(unittest.TestCase):

    def test_importable_without_agent_cli(self):
        """agent.py, a test, or a desktop app must be able to switch
        providers without importing the terminal UI."""
        self.assertNotIn("agent_cli", sys.modules.get("provider_manager").__dict__)
        source = (PROJECT_DIR / "provider_manager.py").read_text(encoding="utf-8")
        self.assertNotIn("import agent_cli", source)
        self.assertNotIn("msvcrt", source)

    def test_public_surface(self):
        for fn in ("list_providers", "get_active", "activate", "save",
                   "detect_type", "probe", "persist_capabilities"):
            self.assertTrue(callable(getattr(pm, fn)), fn)

    def test_agent_cli_uses_one_env_implementation(self):
        import agent_cli
        self.assertIs(agent_cli._set_env_key, pm.set_env_key)
        self.assertIs(agent_cli._drop_env_key, pm.drop_env_key)


class TestProviderConfig(ConfigTestCase):

    def test_save_and_read_back(self):
        p = pm.Provider(name="Test", type="openai", base_url="https://x/v1",
                        model="m", env={"ANTHROPIC_API_KEY": "k"})
        pm.save(p)
        loaded = pm.get("Test")
        self.assertEqual(loaded.type, "openai")
        self.assertEqual(loaded.model, "m")
        self.assertEqual(pm.get_active().name, "Test")

    def test_capabilities_round_trip(self):
        p = pm.Provider(name="T", type="custom", base_url="https://x/v1")
        p.capabilities.streaming = False
        p.capabilities.max_tools = 7
        pm.save(p)
        self.assertFalse(pm.get("T").capabilities.streaming)
        self.assertEqual(pm.get("T").capabilities.max_tools, 7)

    def test_persist_capabilities_updates_in_place(self):
        p = pm.Provider(name="T", type="custom", base_url="https://x/v1")
        pm.save(p)
        p.capabilities.tool_use = False
        pm.persist_capabilities(p)
        self.assertFalse(pm.get("T").capabilities.tool_use)

    def test_remove(self):
        pm.save(pm.Provider(name="Gone", type="custom"))
        self.assertTrue(pm.remove("Gone"))
        self.assertIsNone(pm.get("Gone"))
        self.assertIsNone(pm.get_active())

    def test_legacy_entry_without_type_is_usable(self):
        """Configs written before `type` was mandatory must still load."""
        pm.save_config({"active": "Old", "providers": {"Old": {
            "model": "m", "env": {"ANTHROPIC_BASE_URL": "https://openrouter.ai/api/v1"}}}})
        self.assertEqual(pm.get("Old").type, "openrouter")


# ══════════════════════════════════════════════════════════════════════
#  P4-2 — no runtime substring matching
# ══════════════════════════════════════════════════════════════════════

class TestTypeDetection(unittest.TestCase):

    def test_known_endpoints(self):
        cases = [
            ("https://openrouter.ai/api/v1", "openrouter"),
            ("http://127.0.0.1:6446", "zen"),
            ("https://opencode.ai/zen/v1", "zen"),
            ("http://localhost:11434/v1", "ollama"),
            ("https://api.anthropic.com", "anthropic"),
            ("https://api.openai.com/v1", "openai"),
            ("", "anthropic"),
        ]
        for url, want in cases:
            self.assertEqual(pm.detect_type(url), want, url)

    def test_unknown_endpoint_is_custom_not_broken(self):
        """A self-hosted endpoint used to fall through to 'other' and lose
        model lists, context windows and quirk handling."""
        self.assertEqual(pm.detect_type("https://llm.internal.corp/v1"), "custom")

    def test_custom_type_still_speaks_openai_wire(self):
        p = pm.Provider(name="c", type="custom")
        self.assertTrue(p.speaks_openai_wire)


class TestCapabilityDrivenLimits(ConfigTestCase):

    def test_tool_ceiling_comes_from_capabilities_not_model_name(self):
        """A model named `my-free-model` used to lose 75% of its tool budget
        for containing the word 'free'."""
        p = pm.Provider(name="P", type="anthropic", model="my-free-model")
        p.capabilities.max_tools = 128
        p.capabilities.probed_at = 1.0
        pm.save(p)
        self.assertEqual(agent.tool_ceiling(), 128)

    def test_probed_ceiling_is_respected(self):
        p = pm.Provider(name="P", type="custom", model="m")
        p.capabilities.max_tools = 24
        p.capabilities.probed_at = 1.0
        pm.save(p)
        self.assertEqual(agent.tool_ceiling(), 24)

    def test_unprobed_provider_gets_its_type_ceiling(self):
        pm.save(pm.Provider(name="Z", type="zen", model="m"))
        self.assertEqual(agent.tool_ceiling(), pm.KNOWN_TOOL_CEILINGS["zen"])

    def test_no_active_provider_is_not_an_error(self):
        pm.save_config({"active": None, "providers": {}})
        self.assertGreater(agent.tool_ceiling(), 0)


class TestProbe(ConfigTestCase):

    def test_probe_reads_context_window_from_endpoint(self):
        srv, url = stub_provider.serve()
        try:
            p = pm.Provider(name="S", type="custom", base_url=url, model="stub-model")
            caps = pm.probe(p)
            self.assertEqual(caps.context_window, 31337)
            self.assertTrue(caps.probed)
        finally:
            srv.shutdown()

    def test_probe_detects_missing_streaming(self):
        """_probe_streaming would have caught the Phase 0 bug at configuration
        time instead of in front of the user."""
        srv, url = stub_provider.serve(stub_provider.NoStreamStub)
        try:
            p = pm.Provider(name="S", type="custom", base_url=url, model="stub-model")
            self.assertFalse(pm.probe(p).streaming)
        finally:
            srv.shutdown()

    def test_probe_detects_missing_tool_support(self):
        srv, url = stub_provider.serve(stub_provider.NoToolsStub)
        try:
            p = pm.Provider(name="S", type="custom", base_url=url, model="stub-model")
            caps = pm.probe(p)
            self.assertFalse(caps.tool_use)
            self.assertFalse(caps.parallel_tool_calls)
        finally:
            srv.shutdown()

    def test_probe_of_unreachable_endpoint_degrades_to_defaults(self):
        """A probe against a dead endpoint must not block configuration."""
        p = pm.Provider(name="Down", type="custom",
                        base_url="http://127.0.0.1:1/v1", model="m")
        caps = pm.probe(p)
        self.assertTrue(caps.streaming)
        self.assertTrue(caps.tool_use)
        self.assertEqual(caps.context_window, 200_000)

    def test_list_models(self):
        srv, url = stub_provider.serve()
        try:
            p = pm.Provider(name="S", type="custom", base_url=url)
            self.assertEqual(pm.list_models(p), ["stub-model"])
        finally:
            srv.shutdown()

    def test_ollama_gets_a_small_default_context(self):
        """Compaction must respect the real window, not the cloud default."""
        p = pm.Provider(name="O", type="ollama",
                        base_url="http://127.0.0.1:1/v1", model="m")
        self.assertEqual(pm.probe(p, quick=True).context_window, 8192)


# ══════════════════════════════════════════════════════════════════════
#  P4-3 — degrade, never fail
# ══════════════════════════════════════════════════════════════════════

class TestDegradation(ConfigTestCase):

    def test_degrade_persists(self):
        p = pm.Provider(name="P", type="custom", base_url="https://x/v1")
        pm.save(p)
        agent.degrade_capability("streaming", "test")
        self.assertFalse(pm.get("P").capabilities.streaming)

    def test_degrade_is_idempotent(self):
        p = pm.Provider(name="P", type="custom")
        p.capabilities.streaming = False
        pm.save(p)
        agent.degrade_capability("streaming")      # must not raise
        self.assertFalse(pm.get("P").capabilities.streaming)

    def test_degrade_without_active_provider_is_silent(self):
        pm.save_config({"active": None, "providers": {}})
        agent.degrade_capability("streaming")      # must not raise

    def test_transient_failure_does_not_disable_streaming(self):
        """A 429 means the endpoint was busy, not that it cannot stream.

        Regression: the real-provider matrix hit a rate limit and the run
        wrote `streaming: false` into the provider config, disabling
        streaming permanently over a temporary condition.
        """
        from core.loop import is_retryable_error
        rate_limited = RuntimeError(
            'HTTP 429 from https://x/v1/chat/completions: '
            '{"error":{"type":"FreeUsageLimitError","message":"Rate limit exceeded."}}')
        self.assertTrue(is_retryable_error(rate_limited))

        p = pm.Provider(name="P", type="custom", base_url="https://x/v1")
        pm.save(p)
        self.assertTrue(pm.get("P").capabilities.streaming)

        # What the loop shim does when the core reports a retryable failure.
        state_error_retryable = is_retryable_error(rate_limited)
        if not state_error_retryable:
            agent.degrade_capability("streaming")
        self.assertTrue(pm.get("P").capabilities.streaming,
                        "a rate limit must not be recorded as a capability gap")

    def test_genuine_stream_failure_is_recorded(self):
        from core.loop import is_retryable_error
        not_supported = openai_adapter.ProviderStreamError(
            "endpoint did not stream (Content-Type: application/json)")
        self.assertFalse(is_retryable_error(not_supported))

        p = pm.Provider(name="P", type="custom", base_url="https://x/v1")
        pm.save(p)
        if not is_retryable_error(not_supported):
            agent.degrade_capability("streaming")
        self.assertFalse(pm.get("P").capabilities.streaming)

    def test_state_carries_the_streaming_failure_reason(self):
        from core.state import AgentState
        state = AgentState(system_prompt="", messages=[], get_client=lambda: None,
                           get_model=lambda: "m", responder=_Responder())
        self.assertIsNone(state.streaming_error)
        self.assertFalse(state.streaming_error_retryable)

    def test_no_streaming_capability_disables_streaming_for_the_turn(self):
        p = pm.Provider(name="P", type="custom")
        p.capabilities.streaming = False
        p.capabilities.probed_at = 1.0
        pm.save(p)
        state = agent.build_state("sys", [{"role": "user", "content": "hi"}], _Responder())
        self.assertFalse(state.streaming_enabled)

    def test_no_tool_use_falls_back_to_text_protocol(self):
        """The capability gap costs a feature, not the session: the tools are
        described in the prompt and driven by a text protocol instead."""
        p = pm.Provider(name="P", type="custom")
        p.capabilities.tool_use = False
        p.capabilities.probed_at = 1.0
        pm.save(p)
        state = agent.build_state("sys", [{"role": "user", "content": "hi"}], _Responder())
        self.assertEqual(state.tools, [])
        self.assertIn("tool_call", state.system_prompt)
        self.assertIn("read_file", state.system_prompt)

    def test_no_system_prompt_support_moves_it_into_messages(self):
        p = pm.Provider(name="P", type="custom")
        p.capabilities.system_prompt = False
        p.capabilities.probed_at = 1.0
        pm.save(p)
        state = agent.build_state("SYSTEM-TEXT",
                                  [{"role": "user", "content": "hi"}], _Responder())
        self.assertEqual(state.system_prompt, "")
        self.assertIn("SYSTEM-TEXT", json.dumps(state.messages))

    def test_max_output_tokens_respected(self):
        p = pm.Provider(name="P", type="custom")
        p.capabilities.max_output_tokens = 512
        p.capabilities.probed_at = 1.0
        pm.save(p)
        state = agent.build_state("s", [{"role": "user", "content": "hi"}], _Responder())
        self.assertEqual(state.max_tokens, 512)


class TestTextToolProtocol(unittest.TestCase):

    def test_parses_a_call(self):
        calls = agent.parse_text_tool_calls(
            'ok\n```tool_call\n{"name": "read_file", "input": {"file_path": "a.py"}}\n```')
        self.assertEqual(calls, [{"name": "read_file",
                                  "input": {"file_path": "a.py"}}])

    def test_parses_several(self):
        text = ('```tool_call\n{"name": "a", "input": {}}\n```\n'
                '```tool_call\n{"name": "b", "input": {"x": 1}}\n```')
        self.assertEqual([c["name"] for c in agent.parse_text_tool_calls(text)],
                         ["a", "b"])

    def test_malformed_block_is_skipped_not_fatal(self):
        self.assertEqual(agent.parse_text_tool_calls("```tool_call\nnot json\n```"), [])

    def test_no_blocks(self):
        self.assertEqual(agent.parse_text_tool_calls("just prose"), [])

    def test_description_lists_tools(self):
        described = agent.describe_tools_as_text(agent.TOOLS)
        for t in agent.TOOLS:
            self.assertIn(t["name"], described)


class _Responder:
    def ask(self, event):
        return "allow"


# ══════════════════════════════════════════════════════════════════════
#  P4-4 — in-process OpenAI adapter
# ══════════════════════════════════════════════════════════════════════

class TestOpenAIAdapter(unittest.TestCase):

    def setUp(self):
        self.srv, self.url = stub_provider.serve()
        self.adapter = openai_adapter.OpenAICompatAdapter(self.url, "k")

    def tearDown(self):
        self.srv.shutdown()

    def test_blocking_call_returns_anthropic_shape(self):
        r = self.adapter.messages.create(
            model="stub-model", max_tokens=32, system="s",
            messages=[{"role": "user", "content": "hi"}], tools=[])
        self.assertEqual(r.content[0].type, "text")
        self.assertEqual(r.content[0].text, "blocking reply")
        self.assertEqual(r.stop_reason, "end_turn")
        self.assertEqual(r.usage.input_tokens, 7)

    def test_tool_call_translated(self):
        r = self.adapter.messages.create(
            model="stub-model", max_tokens=32, system="s",
            messages=[{"role": "user", "content": "hi"}],
            tools=[{"name": "list_files", "description": "d",
                    "input_schema": {"type": "object", "properties": {}}}])
        block = r.content[0]
        self.assertEqual(block.type, "tool_use")
        self.assertEqual(block.name, "list_files")
        self.assertEqual(block.input, {"path": "."})
        self.assertEqual(r.stop_reason, "tool_use")

    def test_streaming_is_incremental(self):
        """Real streaming, not Phase 0's replay of a finished response."""
        deltas = []
        with self.adapter.messages.stream(
                model="stub-model", max_tokens=32, system="s",
                messages=[{"role": "user", "content": "hi"}], tools=[]) as st:
            for ev in st:
                if ev.type == "content_block_delta":
                    deltas.append(ev.delta.text)
        self.assertEqual(deltas, ["Hel", "lo"])
        self.assertEqual(st.get_final_message().content[0].text, "Hello")

    def test_streamed_tool_call_reassembles_fragments(self):
        srv, url = stub_provider.serve(stub_provider.StreamingToolStub)
        try:
            adapter = openai_adapter.OpenAICompatAdapter(url, "k")
            announced = False
            with adapter.messages.stream(model="m", max_tokens=8, system="",
                                         messages=[], tools=[]) as st:
                for ev in st:
                    if ev.type == "content_block_start":
                        announced = True
            final = st.get_final_message()
            block = [b for b in final.content if b.type == "tool_use"][0]
            self.assertTrue(announced)
            self.assertEqual(block.name, "read_file")
            self.assertEqual(block.input, {"file_path": "a.py"})
            self.assertEqual(final.stop_reason, "tool_use")
        finally:
            srv.shutdown()

    def test_non_streaming_endpoint_raises_recoverable_error(self):
        srv, url = stub_provider.serve(stub_provider.NoStreamStub)
        try:
            adapter = openai_adapter.OpenAICompatAdapter(url, "k")
            with self.assertRaises(openai_adapter.ProviderStreamError):
                with adapter.messages.stream(model="m", max_tokens=8, system="",
                                             messages=[], tools=[]) as st:
                    list(st)
        finally:
            srv.shutdown()

    def test_no_daemon_required(self):
        """The point of the adapter: translation without the HTTP hop."""
        source = (PROJECT_DIR / "openai_adapter.py").read_text(encoding="utf-8")
        self.assertNotIn("start_proxy", source)
        self.assertNotIn("HTTPServer", source)

    def test_request_body_carries_model_and_max_tokens(self):
        """Regression: `anthropic_to_openai` returns only `messages`, so the
        adapter must supply the rest. Shipping without this produced a live
        401 — "Model {{model}} is not supported" — while every stub passed,
        because the stubs did not check.
        """
        self.adapter.messages.create(
            model="stub-model", max_tokens=123, system="s",
            messages=[{"role": "user", "content": "hi"}], tools=[])
        sent = self.srv.last_request
        self.assertEqual(sent.get("model"), "stub-model")
        self.assertEqual(sent.get("max_tokens"), 123)
        self.assertIn("messages", sent)

    def test_streamed_request_body_carries_model(self):
        with self.adapter.messages.stream(
                model="stub-model", max_tokens=64, system="s",
                messages=[{"role": "user", "content": "hi"}], tools=[]) as st:
            list(st)
        self.assertEqual(self.srv.last_request.get("model"), "stub-model")
        self.assertTrue(self.srv.last_request.get("stream"))

    def test_tools_are_translated_into_the_body(self):
        self.adapter.messages.create(
            model="stub-model", max_tokens=32, system="s",
            messages=[{"role": "user", "content": "hi"}],
            tools=[{"name": "list_files", "description": "d",
                    "input_schema": {"type": "object", "properties": {}}}])
        sent = self.srv.last_request
        self.assertEqual(sent["tools"][0]["function"]["name"], "list_files")

    def test_missing_model_is_rejected_by_the_stub(self):
        """The stub now enforces what the real endpoint enforces."""
        with self.assertRaises(RuntimeError):
            self.adapter.messages.create(
                model="", max_tokens=8, system="s",
                messages=[{"role": "user", "content": "hi"}], tools=[])

    def test_response_blocks_survive_a_second_request(self):
        """The loop appends `response.content` into `messages`, so the next
        request re-translates the same blocks.

        Regression: blocks supported attribute access only, so the turn
        *after* a tool call died with "'_Block' object has no attribute
        'get'". A single-request stub round-trip never reaches this.
        """
        first = self.adapter.messages.create(
            model="stub-model", max_tokens=32, system="s",
            messages=[{"role": "user", "content": "hi"}],
            tools=[{"name": "list_files", "description": "d",
                    "input_schema": {"type": "object", "properties": {}}}])
        tool_block = first.content[0]
        self.assertEqual(tool_block.type, "tool_use")

        conversation = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": first.content},   # blocks, not dicts
            {"role": "user", "content": [{"type": "tool_result",
                                          "tool_use_id": tool_block.id,
                                          "content": "a\nb"}]},
        ]
        second = self.adapter.messages.create(
            model="stub-model", max_tokens=32, system="s",
            messages=conversation, tools=[])
        self.assertTrue(second.content)
        sent = self.srv.last_request
        assistant = [m for m in sent["messages"] if m["role"] == "assistant"][0]
        self.assertEqual(assistant["tool_calls"][0]["function"]["name"], "list_files")

    def test_block_is_readable_both_ways(self):
        block = openai_adapter._Block(
            {"type": "tool_use", "id": "t1", "name": "n", "input": {"k": 1}})
        self.assertEqual(block.type, block.get("type"))
        self.assertEqual(block.name, block["name"])
        self.assertEqual(block.input, block.get("input"))


class TestAdapterSelection(ConfigTestCase):

    def test_openai_wire_provider_uses_adapter(self):
        pm.save(pm.Provider(name="P", type="openrouter",
                            base_url="https://openrouter.ai/api/v1"))
        self.assertTrue(openai_adapter.should_use_adapter())

    def test_anthropic_provider_does_not(self):
        pm.save(pm.Provider(name="A", type="anthropic",
                            base_url="https://api.anthropic.com"))
        self.assertFalse(openai_adapter.should_use_adapter())

    def test_env_override_forces_the_daemon(self):
        pm.save(pm.Provider(name="P", type="zen", base_url="http://127.0.0.1:6446"))
        os.environ["TOMAS_ZEN_PROXY"] = "1"
        try:
            self.assertFalse(openai_adapter.should_use_adapter())
        finally:
            del os.environ["TOMAS_ZEN_PROXY"]

    def test_zen_adapter_points_upstream_not_at_the_proxy_port(self):
        pm.save(pm.Provider(name="Z", type="zen", base_url="http://127.0.0.1:6446",
                            model="m"))
        adapter = openai_adapter.build_from_active()
        self.assertIsNotNone(adapter)
        self.assertNotIn("6446", adapter.base_url)


# ══════════════════════════════════════════════════════════════════════
#  P4-8 — tool selection
# ══════════════════════════════════════════════════════════════════════

def _tool(name, desc=""):
    return {"name": name, "description": desc,
            "input_schema": {"type": "object", "properties": {}}}


class TestToolSelection(unittest.TestCase):

    def setUp(self):
        self.extra = [
            _tool("take_screenshot", "capture a screenshot of the browser page"),
            _tool("browser_navigate", "navigate the browser to a URL"),
            _tool("sql_query", "run a SQL query against a postgres database"),
            _tool("send_email", "send an email message to a recipient"),
            _tool("git_commit", "create a git commit in the repository"),
        ]
        self.pool = agent.TOOLS + self.extra
        self.budget = len(agent.TOOLS) + 2

    def names(self, tools):
        builtin = {t["name"] for t in agent.TOOLS}
        return [t["name"] for t in tools if t["name"] not in builtin]

    def test_builtins_are_always_kept(self):
        selected, _ = agent.select_tools(self.pool, "anything", len(agent.TOOLS))
        self.assertEqual({t["name"] for t in selected},
                         {t["name"] for t in agent.TOOLS})

    def test_browser_question_selects_browser_tools(self):
        selected, _ = agent.select_tools(
            self.pool, "take a screenshot of the browser page", self.budget)
        self.assertIn("take_screenshot", self.names(selected))

    def test_database_question_selects_database_tools(self):
        selected, _ = agent.select_tools(
            self.pool, "run a sql query against the postgres database", self.budget)
        self.assertIn("sql_query", self.names(selected))

    def test_selection_follows_the_user_across_a_pivot(self):
        """A user who moves from file edits to browser automation should get
        browser tools when they ask for them."""
        agent.reset_session_state()
        first, _ = agent.select_tools(self.pool, "commit this to git", self.budget)
        second, _ = agent.select_tools(self.pool, "screenshot the browser", self.budget)
        self.assertIn("git_commit", self.names(first))
        self.assertIn("take_screenshot", self.names(second))

    def test_a_real_pivot_reselects(self):
        # One MCP slot, so the set cannot cover both capabilities at once and
        # the pivot has to actually displace something.
        agent.reset_session_state()
        budget = len(agent.TOOLS) + 1
        first, _ = agent.select_tools(self.pool, "commit this to git", budget)
        second, _ = agent.select_tools(self.pool, "screenshot the browser", budget)
        self.assertEqual(self.names(first), ["git_commit"])
        self.assertEqual(self.names(second), ["take_screenshot"])

    def test_the_set_is_held_byte_stable_while_it_still_covers_the_turn(self):
        """Prefix caching is binary: one changed byte in the tool block and the
        system prompt, the tools and the whole conversation are re-processed.
        Selecting purely by score swapped 38 of 64 slots between consecutive
        turns, so nothing was ever cached."""
        agent.reset_session_state()
        first, _ = agent.select_tools(self.pool, "screenshot the browser", self.budget)
        # A follow-up needing nothing new must produce the identical payload.
        second, _ = agent.select_tools(self.pool, "now screenshot it again", self.budget)
        self.assertEqual(json.dumps(first), json.dumps(second))

    def test_a_selected_set_is_serialised_in_a_stable_order(self):
        # Ordering the chosen tools by score meant an unchanged set still
        # produced different bytes each turn, missing the cache for nothing.
        # (When everything fits there is no selection, and the caller's pool
        # order is already stable, so only the constrained path sorts.)
        agent.reset_session_state()
        selected, _ = agent.select_tools(
            self.pool, "screenshot the browser page", len(agent.TOOLS) + 3)
        names = self.names(selected)
        self.assertEqual(names, sorted(names))
        self.assertLess(len(names), len(self.extra), "budget must actually bind")

    def test_cache_control_marks_the_prefix_when_supported(self):
        # ~6,300 identical tokens ship every turn (system prompt + tools). One
        # breakpoint at the end of the system block covers both, because the
        # cache hierarchy is tools -> system -> messages.
        caps = pm.Capabilities(
            prompt_caching=True, system_prompt=True, tool_use=True)
        real = agent._active_capabilities
        agent._active_capabilities = lambda: caps
        try:
            state = agent.build_state("SYSTEM", [{"role": "user", "content": "hi"}],
                                      _Responder())
        finally:
            agent._active_capabilities = real
        self.assertIsInstance(state.system_prompt, list)
        self.assertEqual(state.system_prompt[0]["cache_control"]["type"], "ephemeral")
        self.assertIn("SYSTEM", state.system_prompt[0]["text"])

    def test_no_cache_control_on_openai_wire(self):
        # Those providers cache server-side and have no cache_control field.
        caps = pm.Capabilities(
            prompt_caching=False, system_prompt=True, tool_use=True)
        real = agent._active_capabilities
        agent._active_capabilities = lambda: caps
        try:
            state = agent.build_state("SYSTEM", [{"role": "user", "content": "hi"}],
                                      _Responder())
        finally:
            agent._active_capabilities = real
        self.assertIsInstance(state.system_prompt, str)

    def test_a_block_system_prompt_flattens_for_the_openai_wire(self):
        # Defensive: if one ever reaches that path it must not leak the field.
        from zen_proxy import anthropic_to_openai
        body = anthropic_to_openai({
            "system": [{"type": "text", "text": "SYS",
                        "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(body["messages"][0], {"role": "system", "content": "SYS"})
        self.assertNotIn("cache_control", json.dumps(body))

    def test_withheld_tools_are_reported(self):
        selected, withheld = agent.select_tools(self.pool, "screenshot", self.budget)
        self.assertTrue(withheld)
        self.assertEqual(len(selected) + len(withheld), len(self.pool))

    def test_everything_fits_means_nothing_withheld(self):
        selected, withheld = agent.select_tools(self.pool, "x", 999)
        self.assertEqual(withheld, [])
        self.assertEqual(len(selected), len(self.pool))

    def test_no_context_is_stable_not_random(self):
        a, _ = agent.select_tools(self.pool, "", self.budget)
        b, _ = agent.select_tools(self.pool, "", self.budget)
        self.assertEqual(self.names(a), self.names(b))

    def test_notice_names_the_gap(self):
        """A silent capability gap is unrecoverable — the model cannot ask
        for a tool it has no idea exists."""
        notice = agent.withheld_tools_notice(self.extra)
        self.assertIn("5", notice)
        self.assertIn("not loaded", notice)

    def test_empty_notice_when_nothing_withheld(self):
        self.assertEqual(agent.withheld_tools_notice([]), "")

    def test_last_user_text_skips_tool_results(self):
        messages = [
            {"role": "user", "content": "screenshot the page"},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "1",
                                               "name": "x", "input": {}}]},
            {"role": "user", "content": [{"type": "tool_result",
                                          "tool_use_id": "1", "content": "ok"}]},
        ]
        self.assertEqual(agent._last_user_text(messages), "screenshot the page")


class TestServerWorkingSet(unittest.TestCase):
    """A server the turn is about must arrive as a usable set.

    Scoring each tool alone gave the model `create_document` and
    `convert_to_pdf` while `add_heading`/`add_paragraph`/`add_table` scored
    0.0 and were withheld — nothing in those names repeats the user's words.
    It could open a document and convert one but not fill one, so it wrote a
    .docx with `write_file` that was not a real .docx.
    """

    #: Shaped like the real word-docs server: a couple of tools whose names
    #: echo the request, a working set that does not, and a long tail of
    #: specialised variants that should lose to all of it.
    DOCS = ([_tool("create_document", "Create a new Word document"),
             _tool("convert_to_pdf", "Convert a Word document to PDF format"),
             _tool("add_heading", "Add a heading to a Word document"),
             _tool("add_paragraph", "Add a paragraph to a Word document"),
             _tool("add_table", "Add a table to a Word document")]
            + [_tool(f"set_table_cell_variant_{i}", "specialised table tweak")
               for i in range(20)])

    OTHER = ([_tool("create_entities", "create entities in the knowledge graph"),
              _tool("create_relations", "create relations in the knowledge graph")]
             + [_tool(f"browser_{v}", "browser automation")
                for v in ("click", "type", "close", "resize", "drop", "evaluate")])

    def setUp(self):
        self.pool = agent.TOOLS + self.DOCS + self.OTHER
        owner = {t["name"]: "word-docs" for t in self.DOCS}
        owner.update({t["name"]: "other" for t in self.OTHER})
        self.server_of = lambda n: owner.get(n)

    def select(self, context, budget):
        selected, _ = agent.select_tools(self.pool, context, budget,
                                         server_of=self.server_of)
        return {t["name"] for t in selected}

    def test_the_working_set_survives_a_tight_budget(self):
        names = self.select("create a document and convert it to pdf",
                            len(agent.TOOLS) + 8)
        for needed in ("create_document", "convert_to_pdf",
                       "add_heading", "add_paragraph", "add_table"):
            self.assertIn(needed, names, f"{needed} was withheld")

    def test_specialised_variants_lose_to_the_working_set(self):
        names = self.select("create a document and convert it to pdf",
                            len(agent.TOOLS) + 8)
        self.assertFalse([n for n in names if n.startswith("set_table_cell_variant")])

    def test_a_server_is_ranked_by_its_total_not_its_best_tool(self):
        """`create_entities` ties `create_document` on score alone. The
        request is about documents, and summed relevance says so."""
        names = self.select("create a document and convert it to pdf",
                            len(agent.TOOLS) + 6)
        self.assertIn("add_heading", names)

    def test_an_unrelated_request_does_not_drag_in_the_document_set(self):
        names = self.select("click the button in the browser",
                            len(agent.TOOLS) + 4)
        self.assertNotIn("add_heading", names)

    def test_quota_never_exceeds_the_budget(self):
        selected, withheld = agent.select_tools(
            self.pool, "create a document", len(agent.TOOLS) + 3,
            server_of=self.server_of)
        self.assertEqual(len(selected), len(agent.TOOLS) + 3)
        self.assertEqual(len(selected) + len(withheld), len(self.pool))

    def test_selection_is_stable_across_calls(self):
        a = self.select("create a document and convert it to pdf", len(agent.TOOLS) + 8)
        b = self.select("create a document and convert it to pdf", len(agent.TOOLS) + 8)
        self.assertEqual(a, b)

    def test_plain_names_outrank_qualified_ones(self):
        self.assertGreater(agent.tool_simplicity("add_heading"),
                           agent.tool_simplicity("add_footnote_robust"))
        self.assertGreater(agent.tool_simplicity("add_footnote_robust"),
                           agent.tool_simplicity("set_table_cell_shading"))

    def test_ungrouped_tools_still_select_by_relevance(self):
        """No MCP manager (or all built-ins) must not break selection."""
        selected, _ = agent.select_tools(
            agent.TOOLS + self.DOCS, "convert to pdf", len(agent.TOOLS) + 1,
            server_of=lambda n: None)
        self.assertIn("convert_to_pdf", {t["name"] for t in selected})

    def test_a_confirmation_keeps_the_tools_it_confirms(self):
        """Session 20260804_144250: the request naming docx/pdf selected the
        whole working set, then "зроби новий", "2" and "зроби це" each
        selected none of it — three words carry no keywords. By the time the
        user said "yes, do it", the tools to do it were gone."""
        messages = [{"role": "user",
                     "content": "create a document and convert it to pdf"}]
        for follow in ("do it", "2", "yes correct", "no,"):
            messages.append({"role": "assistant", "content": "which number?"})
            messages.append({"role": "user", "content": follow})
            selected, _ = agent.select_tools(
                self.pool, agent._recent_user_text(messages),
                len(agent.TOOLS) + 8, server_of=self.server_of)
            names = {t["name"] for t in selected}
            self.assertIn("add_heading", names,
                          f"working set lost after {follow!r}")


class TestToolContextWindow(unittest.TestCase):
    """What tool selection reads as "what the user is doing"."""

    def ctx(self, *user_texts):
        messages = []
        for t in user_texts:
            messages.append({"role": "user", "content": t})
            messages.append({"role": "assistant", "content": "ok"})
        return agent._recent_user_text(messages)

    def test_the_newest_message_is_included(self):
        self.assertIn("newest", self.ctx("oldest", "newest"))

    def test_earlier_turns_are_included(self):
        self.assertIn("oldest", self.ctx("oldest", "newest"))

    def test_the_newest_message_outweighs_the_rest(self):
        """Frequency ranks the keywords, so a conversation must still be able
        to change subject."""
        ctx = self.ctx("alpha", "beta")
        self.assertGreater(ctx.count("beta"), ctx.count("alpha"))

    def test_tool_results_are_not_user_intent(self):
        messages = [
            {"role": "user", "content": "the real request"},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "1", "name": "x", "input": {}}]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "1", "content": "junk"}]},
        ]
        self.assertIn("the real request", agent._recent_user_text(messages))
        self.assertNotIn("junk", agent._recent_user_text(messages))

    def test_the_window_is_bounded(self):
        ctx = self.ctx(*[f"turn{i}" for i in range(40)])
        self.assertNotIn("turn0 ", ctx)
        self.assertLessEqual(len(ctx), agent.TOOL_CONTEXT_CHARS)

    def test_no_user_turns_is_empty(self):
        self.assertEqual(agent._recent_user_text([]), "")
        self.assertEqual(
            agent._recent_user_text([{"role": "assistant", "content": "hi"}]), "")


# ══════════════════════════════════════════════════════════════════════
#  P4-6 — the real-provider matrix
# ══════════════════════════════════════════════════════════════════════

def configured_providers():
    """Providers with enough configuration to actually call."""
    try:
        return [p for p in pm.list_providers() if p.base_url or p.type == "zen"]
    except Exception:
        return []


_UNAVAILABLE_MARKERS = (
    "429", "rate limit", "rate_limit", "too many requests", "usagelimit",
    "quota", "insufficient", "503", "502", "504", "timed out", "timeout",
    "temporarily unavailable",
)


def _is_unavailable(message: str) -> bool:
    """True when a failure is the environment, not the provider integration.

    Being rate-limited is the same class of condition as having no
    credentials: it says nothing about whether the code works, so it skips
    rather than failing the release gate.
    """
    lowered = (message or "").lower()
    return any(m in lowered for m in _UNAVAILABLE_MARKERS)


@unittest.skipUnless(os.environ.get("TOMAS_TEST_PROVIDERS") == "1",
                     "set TOMAS_TEST_PROVIDERS=1 to run against real providers")
class TestRealProviders(unittest.TestCase):
    """The single most valuable test in the suite: a real tool round-trip
    against every configured provider. It is exactly what was broken for
    every provider before Phase 0. Slow, needs credentials, run before a
    release."""

    def test_provider_completes_a_tool_round_trip(self):
        providers = configured_providers()
        if not providers:
            self.skipTest("no providers configured")
        failures, unavailable = [], []
        for provider in providers:
            with self.subTest(provider=provider.name):
                agent.reset_session_state()
                try:
                    pm.activate(provider.name)
                    agent.reinit_client()
                    agent.COMBINED_TOOLS = agent.TOOLS
                    agent.ALL_TOOLS = list(agent.TOOLS)
                    messages = [{"role": "user", "content":
                                 "Use list_files on the current directory."}]
                    reply = agent.agent_loop(agent.build_system_prompt(""), messages)
                    if not (reply or "").strip():
                        # An empty reply after exhausted retries is the
                        # environment (rate limit, outage), not the code.
                        # Phase 6's telemetry says which.
                        reason = "; ".join(f.get("error", f.get("reason", ""))
                                           for f in agent.session_telemetry()["failed_turns"])
                        if _is_unavailable(reason):
                            unavailable.append(f"{provider.name}: {reason[:80]}")
                            continue
                        failures.append(f"{provider.name}: empty reply ({reason[:80]})")
                        continue
                    self.assertFalse(reply.startswith("I'm sorry"),
                                     f"{provider.name}: {reply[:120]}")
                except Exception as e:
                    (unavailable if _is_unavailable(str(e)) else failures).append(
                        f"{provider.name}: {str(e)[:120]}")
        if unavailable and not failures:
            self.skipTest("provider(s) unavailable: " + "; ".join(unavailable))
        self.assertEqual(failures, [])

    def test_probe_every_configured_provider(self):
        for provider in configured_providers():
            with self.subTest(provider=provider.name):
                caps = pm.probe(provider)
                self.assertGreater(caps.context_window, 0)
                self.assertTrue(caps.probed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
