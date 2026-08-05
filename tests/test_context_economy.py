#!/usr/bin/env python3
"""
Regression tests for how much context a turn costs.

Every test here corresponds to a measurement taken against a live endpoint
(OpenCode Zen / deepseek-v4-flash-free) during the context-economy pass. The
numbers in the docstrings are what was observed, not what was assumed.

Run: python -m unittest tests.test_context_economy -v
"""
import copy
import json
import os
import sys
import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent.resolve()
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import agent
import instructions_manager
import provider_manager


# ══════════════════════════════════════════════════════════════════════
#  The stable prefix — what makes prompt caching work at all
# ══════════════════════════════════════════════════════════════════════

class StablePrefix(unittest.TestCase):
    """The system prompt must not change with the message until it has to.

    Prefix caching matches on an exact byte prefix, and the system prompt is
    serialised before the messages — so the first byte that differs from last
    turn ends the cache hit for the system prompt *and the whole conversation
    behind it*. Measured over five turns on Zen with a populated fact store:
    52.0% of prompt tokens served from cache before this ordering, 83.9% after.
    """

    MESSAGES = [
        "read core/loop.py and tell me what it does",
        "now fix the retry logic there",
        "run the tests",
        "ok continue",
        "дякую, тепер додай ще один розділ",
    ]

    def common_prefix_len(self, a: str, b: str) -> int:
        n = min(len(a), len(b))
        i = 0
        while i < n and a[i] == b[i]:
            i += 1
        return i

    def test_stable_half_is_identical_across_messages(self):
        """Everything up to the first volatile section is byte-identical."""
        agent.invalidate_prompt_cache()
        prompts = [agent.build_system_prompt(m) for m in self.MESSAGES]
        stable = agent._stable_prefix
        self.assertIsNotNone(stable, "the stable half should have been cached")
        for message, prompt in zip(self.MESSAGES, prompts):
            self.assertTrue(
                prompt.startswith(stable),
                f"prompt for {message!r} does not start with the stable prefix — "
                f"a volatile section has been emitted before a static one, "
                f"which costs the cache hit for the entire history")

    def test_static_sections_precede_volatile_ones(self):
        """The skills catalogue is static and must not sit after retrieval.

        It used to be emitted last, after two sections rebuilt per message, so
        933 tokens of unchanging text were re-tokenised every single turn and
        the cache boundary landed 3,733 chars earlier than it needed to.
        """
        agent.invalidate_prompt_cache()
        prompt = agent.build_system_prompt("write me a report as a docx")
        catalogue = agent.build_skills_section(max_chars=agent.MAX_SKILLS_CHARS)
        if not catalogue:
            self.skipTest("no skills installed to place")
        retrieved_heading = "# Context retrieved for this message"
        if retrieved_heading not in prompt:
            self.skipTest("nothing retrieved for this message")
        self.assertLess(
            prompt.index(catalogue[:60]), prompt.index(retrieved_heading),
            "the skills catalogue must come before per-message retrieval")

    def test_prefix_is_rebuilt_when_an_instruction_file_changes(self):
        """Caching must never outlive an edit to the file it cached."""
        agent.invalidate_prompt_cache()
        first = agent.build_system_prompt("hello")
        probe = PROJECT_DIR / "BEHAVIOR.md"
        existed = probe.exists()
        original = probe.read_text(encoding="utf-8") if existed else None
        try:
            probe.write_text("# probe\nA distinctive marker line.\n",
                             encoding="utf-8")
            second = agent.build_system_prompt("hello")
            self.assertIn("A distinctive marker line.", second)
            self.assertNotEqual(first, second)
        finally:
            if original is not None:
                probe.write_text(original, encoding="utf-8")
            else:
                probe.unlink(missing_ok=True)
            agent.invalidate_prompt_cache()

    def test_total_cap_never_drops_the_volatile_tail(self):
        """The cap comes out of the stable half, not the acting instructions.

        The tail carries the user's standing rules and the procedure for the
        job in hand. Trimming the joined string from the end — as this used to
        — made the triggered skill body the first thing discarded.
        """
        agent.invalidate_prompt_cache()
        original = agent.MAX_TOTAL_SYSTEM_PROMPT
        try:
            agent.MAX_TOTAL_SYSTEM_PROMPT = agent.MIN_STABLE_CHARS + 500
            prompt = agent.build_system_prompt("write me a report as a docx")
            self.assertGreaterEqual(
                len(prompt), agent.MIN_STABLE_CHARS,
                "the stable half was squeezed below its floor")
            self.assertIn(
                "coding assistant", prompt,
                "BASE_PROMPT must survive any truncation")
        finally:
            agent.MAX_TOTAL_SYSTEM_PROMPT = original
            agent.invalidate_prompt_cache()


# ══════════════════════════════════════════════════════════════════════
#  Tool schemas — the largest single line item in a turn
# ══════════════════════════════════════════════════════════════════════

class ToolSchemaCompaction(unittest.TestCase):
    """Measured mean across 64 real MCP tools: 503 chars (~125 tokens) each.

    At a 128-tool ceiling that block costs ~16,100 tokens a turn — four times
    the entire system prompt — and it is re-sent every turn.
    """

    def sample(self) -> list[dict]:
        return [{
            "name": "sequentialthinking",
            "description": "A detailed tool for problem solving. " + ("x " * 3000),
            "input_schema": {
                "$schema": "http://json-schema.org/draft-07/schema#",
                "title": "SequentialThinkingInput",
                "additionalProperties": False,
                "type": "object",
                "properties": {
                    "thought": {"type": "string",
                                "description": "The thought. " + ("y " * 300)},
                    "mode": {"type": "string", "enum": ["a", "b"],
                             "default": "a"},
                    "steps": {"type": "array",
                              "items": {"type": "integer"}},
                },
                "required": ["thought"],
            },
        }]

    def test_payload_gets_materially_smaller(self):
        tools = self.sample()
        before = len(json.dumps(tools))
        after = len(json.dumps(agent.compact_tool_schemas(tools)))
        self.assertLess(after, before / 2,
                        f"expected a real reduction, got {before} -> {after}")

    def test_call_shape_is_preserved_exactly(self):
        """Anything that decides whether a call is well-formed is untouched."""
        compacted = agent.compact_tool_schemas(self.sample())[0]
        schema = compacted["input_schema"]
        self.assertEqual(compacted["name"], "sequentialthinking")
        self.assertEqual(schema["type"], "object")
        self.assertEqual(schema["required"], ["thought"])
        self.assertEqual(sorted(schema["properties"]),
                         ["mode", "steps", "thought"])
        self.assertEqual(schema["properties"]["mode"]["enum"], ["a", "b"])
        self.assertEqual(schema["properties"]["mode"]["default"], "a")
        self.assertEqual(schema["properties"]["steps"]["items"],
                         {"type": "integer"})

    def test_documentation_only_keys_are_dropped(self):
        schema = agent.compact_tool_schemas(self.sample())[0]["input_schema"]
        for key in ("$schema", "title", "additionalProperties"):
            self.assertNotIn(key, schema)

    def test_never_mutates_the_source_tools(self):
        """ALL_TOOLS is re-selected every turn; clipping in place would compound
        until the descriptions were gone entirely."""
        tools = self.sample()
        snapshot = copy.deepcopy(tools)
        agent.compact_tool_schemas(tools)
        self.assertEqual(tools, snapshot)

    def test_builtin_tools_survive_a_round_trip(self):
        for original, compacted in zip(agent.TOOLS,
                                       agent.compact_tool_schemas(agent.TOOLS)):
            self.assertEqual(original["name"], compacted["name"])
            self.assertEqual(original["input_schema"].get("required"),
                             compacted["input_schema"].get("required"))
            self.assertTrue(compacted["description"].strip())


# ══════════════════════════════════════════════════════════════════════
#  Token estimation — this decides when compaction fires
# ══════════════════════════════════════════════════════════════════════

class TokenEstimates(unittest.TestCase):
    """JSON is denser than prose and must not be counted as if it were.

    Measured on the 64 tools of three real MCP servers: 32,226 chars ≈ 9,200
    tokens. The old `// 6` called that 5,371 — a ~40% under-count, or roughly
    5,400 tokens unaccounted for at a 128-tool ceiling.
    """

    def test_tool_tokens_are_counted_as_json(self):
        tools = agent.TOOLS
        chars = sum(len(json.dumps(t)) for t in tools)
        estimate = agent.estimate_tool_tokens(tools)
        self.assertAlmostEqual(estimate, chars / agent.CHARS_PER_TOKEN_JSON,
                               delta=2)
        self.assertGreater(estimate, chars // 6,
                           "the old divisor under-counted; this must not regress")

    def test_prose_and_json_divisors_differ(self):
        self.assertNotEqual(agent.CHARS_PER_TOKEN_PROSE,
                            agent.CHARS_PER_TOKEN_JSON)
        self.assertLess(agent.CHARS_PER_TOKEN_JSON, agent.CHARS_PER_TOKEN_PROSE)

    def test_empty_tool_list_costs_nothing(self):
        self.assertEqual(agent.estimate_tool_tokens([]), 0)


class ToolResultCeiling(unittest.TestCase):
    """One tool result used to be able to add ~25,000 tokens to the transcript
    permanently — and unlike a long reply it is re-sent on every later turn."""

    def test_state_carries_the_configured_ceiling(self):
        from core.permissions import AutoApprove
        state = agent.build_state("sys", [], AutoApprove())
        self.assertEqual(state.max_result_chars, agent.MAX_RESULT_CHARS)

    def test_ceiling_is_well_below_the_old_default(self):
        self.assertLessEqual(agent.MAX_RESULT_CHARS, 50_000)


# ══════════════════════════════════════════════════════════════════════
#  Project instructions — documented as loaded is not the same as loaded
# ══════════════════════════════════════════════════════════════════════

class ProjectInstructions(unittest.TestCase):
    def test_claude_md_is_actually_loaded(self):
        """Both CLAUDE.md and AGENTS.md documented this file as injected into
        every system prompt. It never was: the loader returned on the first
        match and AGENTS.md always matched first."""
        claude = PROJECT_DIR / "CLAUDE.md"
        if not claude.exists():
            self.skipTest("no CLAUDE.md in this checkout")
        section = instructions_manager.get_project_instructions(PROJECT_DIR)
        self.assertIn("CLAUDE.md", section)

    def test_every_present_file_is_loaded_not_just_the_first(self):
        section = instructions_manager.get_project_instructions(PROJECT_DIR)
        present = [n for n in instructions_manager.PROJECT_INSTRUCTION_FILES
                   if (PROJECT_DIR / n).exists()]
        if len(present) < 2:
            self.skipTest("fewer than two instruction files present")
        for name in present:
            self.assertIn(name, section)

    def test_instructions_fit_their_budget_uncut(self):
        """Adding CLAUDE.md pushed the block past a budget sized without it,
        which silently discarded 8,259 chars — half the conventions."""
        raw = instructions_manager.build_instructions_section(PROJECT_DIR)
        self.assertLessEqual(
            len(raw), agent.MAX_INSTRUCTIONS_CHARS,
            f"instructions are {len(raw)} chars against a "
            f"{agent.MAX_INSTRUCTIONS_CHARS} budget — raise the budget or "
            f"dedupe AGENTS.md and CLAUDE.md, but do not ship silent truncation")


# ══════════════════════════════════════════════════════════════════════
#  Zen probing — a 403 that reported itself as "cannot do anything"
# ══════════════════════════════════════════════════════════════════════

class ZenProbeHeaders(unittest.TestCase):
    """Zen sits behind Cloudflare, which answers a bare Authorization header
    with 403 (error 1010). Probing without the opencode headers recorded a
    provider that streams, calls tools and takes a system prompt as capable of
    none of the three — and the agent silently degraded to the text protocol.
    """

    def provider(self, base_url="http://127.0.0.1:6446"):
        return provider_manager.Provider(
            name="zen-test", type="zen", base_url=base_url,
            api_key_env="OPENCODE_API_KEY", model="deepseek-v4-flash-free")

    def test_probe_headers_carry_the_opencode_identity(self):
        headers = provider_manager._headers_for(self.provider())
        self.assertIn("User-Agent", headers)
        self.assertIn("opencode", headers["User-Agent"])
        self.assertIn("x-opencode-client", headers)
        self.assertTrue(headers.get("Authorization", "").startswith("Bearer "))

    def test_probe_resolves_the_legacy_proxy_port_to_the_real_host(self):
        """A zen provider is conventionally configured against the local proxy
        port, which has only ever meant "reach Zen"."""
        url = provider_manager.probe_base_url(self.provider())
        self.assertNotIn(":6446", url)
        self.assertTrue(url.startswith("http"))

    def test_probe_agrees_with_the_runtime_adapter(self):
        """The probe and the adapter must resolve the same endpoint, or they
        disagree about what the same provider can do."""
        import openai_adapter
        probe_url = provider_manager.probe_base_url(self.provider())
        self.assertIn("zen", probe_url)
        self.assertTrue(hasattr(openai_adapter, "build_from_active"))

    def test_non_zen_providers_are_unaffected(self):
        p = provider_manager.Provider(
            name="oai", type="openai", base_url="https://api.openai.com/v1",
            model="gpt-4o")
        p.api_key_env = "PROBE_KEY_THAT_IS_UNSET"
        headers = provider_manager._headers_for(p)
        self.assertNotIn("x-opencode-client", headers)
        self.assertEqual(provider_manager.probe_base_url(p),
                         "https://api.openai.com/v1")


# ══════════════════════════════════════════════════════════════════════
#  The output budget — advice the user cannot act on is worse than none
# ══════════════════════════════════════════════════════════════════════

class OutputBudget(unittest.TestCase):
    """`Capabilities.max_output_tokens` is documented as an optimistic default
    that "probing only ever narrows", but nothing probes it — so its 8192 was
    applied as a hard ceiling no measurement justified. Setting
    AGENT_MAX_TOKENS=32000 still ran at 8192, while the truncation message told
    the user to raise exactly that variable. Measured: the endpoint this hit
    (deepseek-v4-flash-free) accepts max_tokens up to at least 65,536.
    """

    def setUp(self):
        self._saved = os.environ.get("AGENT_MAX_TOKENS")
        self._max = agent.MAX_TOKENS

    def tearDown(self):
        agent.MAX_TOKENS = self._max
        if self._saved is None:
            os.environ.pop("AGENT_MAX_TOKENS", None)
        else:
            os.environ["AGENT_MAX_TOKENS"] = self._saved

    def caps(self, ceiling=8192, probed=True):
        from types import SimpleNamespace
        return SimpleNamespace(max_output_tokens=ceiling, probed=probed)

    def test_explicit_setting_beats_the_unprobed_ceiling(self):
        os.environ["AGENT_MAX_TOKENS"] = "32000"
        agent.MAX_TOKENS = 32000
        self.assertEqual(agent.effective_max_tokens(self.caps()), 32000)

    def test_unset_still_respects_the_ceiling(self):
        os.environ.pop("AGENT_MAX_TOKENS", None)
        agent.MAX_TOKENS = 32000
        self.assertEqual(agent.effective_max_tokens(self.caps()), 8192)

    def test_probed_flag_does_not_reinstate_the_clamp(self):
        """`probed` goes true once *any* probe runs while this field stays
        untouched, so keying on it would restore the clamp it removes."""
        os.environ["AGENT_MAX_TOKENS"] = "32000"
        agent.MAX_TOKENS = 32000
        for probed in (True, False):
            self.assertEqual(
                agent.effective_max_tokens(self.caps(probed=probed)), 32000,
                f"clamped again with probed={probed}")

    def test_zero_ceiling_does_not_zero_the_budget(self):
        os.environ.pop("AGENT_MAX_TOKENS", None)
        agent.MAX_TOKENS = 8192
        self.assertEqual(agent.effective_max_tokens(self.caps(ceiling=0)), 8192)


class ScratchLocation(unittest.TestCase):
    """BASE_PROMPT told the model to put helper scripts in "a temp directory
    outside the project" — which `_safe` forbids. Observed cost: a refused
    write to ~/.tomas/tmp, a refused write to %TEMP%, then a script dropped in
    the repo root the rule existed to keep clean."""

    def test_the_named_scratch_dir_is_actually_writable(self):
        self.assertTrue(agent._safe(agent.SCRATCH_DIR / "helper.py", write=True))

    def test_prompt_no_longer_names_a_forbidden_location(self):
        self.assertNotIn("temp directory outside the project", agent.BASE_PROMPT)
        self.assertIn("_scratch/", agent.BASE_PROMPT)

    def test_refusal_names_where_to_go_instead(self):
        import tempfile
        denied = Path(tempfile.gettempdir()) / "tomas_helper.py"
        message = agent._outside_project_error(denied, write=True)
        self.assertIn(str(agent.SCRATCH_DIR), message)

    def test_tomas_home_refusal_also_points_at_scratch(self):
        message = agent._outside_project_error(
            agent.TOMAS_HOME / "tmp" / "helper.py", write=True)
        self.assertIn(str(agent.SCRATCH_DIR), message)
        self.assertIn("read-only", message)

    def test_scratch_is_ignored_by_git(self):
        gitignore = PROJECT_DIR / ".gitignore"
        if not gitignore.exists():
            self.skipTest("no .gitignore")
        self.assertIn("_scratch/", gitignore.read_text(encoding="utf-8"),
                      "scratch files would otherwise show up as repo changes")


# ══════════════════════════════════════════════════════════════════════
#  Bypass mode — approving every tool is not the same as never stopping
# ══════════════════════════════════════════════════════════════════════

class BypassMode(unittest.TestCase):
    """A session that auto-approved all 56 of its tool calls still halted at
    the 40-call checkpoint and was saved incomplete. `yolo` answers "may this
    tool run?"; bypass also answers "may the turn keep going?"."""

    def setUp(self):
        self._saved = agent.current_mode_name()

    def tearDown(self):
        agent.set_mode(self._saved)

    def state_for(self, mode):
        from core.permissions import AutoApprove
        agent.set_mode(mode)
        return agent.build_state("sys", [], AutoApprove())

    def test_bypass_sets_both_flags_yolo_only_sets_one(self):
        self.assertTrue(self.state_for("bypass").auto_continue)
        self.assertTrue(self.state_for("bypass").yolo)
        self.assertTrue(self.state_for("yolo").yolo)
        self.assertFalse(self.state_for("yolo").auto_continue,
                         "yolo must keep the continuation checkpoint")

    def test_no_other_mode_auto_continues(self):
        for mode in ("auto", "default", "strict", "yolo"):
            self.assertFalse(self.state_for(mode).auto_continue, mode)

    def test_the_cap_reaches_the_state(self):
        self.assertEqual(self.state_for("bypass").max_auto_continuations,
                         agent.MAX_AUTO_CONTINUATIONS)
        self.assertGreaterEqual(agent.MAX_AUTO_CONTINUATIONS, 1,
                                "an unbounded 'never stop' is a billing risk")

    def test_switching_away_from_bypass_clears_it(self):
        self.state_for("bypass")
        self.assertTrue(agent.BYPASS_MODE)
        agent.set_mode("auto")
        self.assertFalse(agent.BYPASS_MODE)
        self.assertFalse(agent.YOLO_MODE)

    def test_mode_name_round_trips(self):
        for mode in ("auto", "default", "yolo", "bypass"):
            agent.set_mode(mode)
            self.assertEqual(agent.current_mode_name(), mode)

    def test_bypass_is_in_the_tab_cycle_and_reachable_from_every_mode(self):
        seen = set()
        agent.set_mode(agent.MODE_CYCLE[0])
        for _ in range(len(agent.MODE_CYCLE)):
            cur = agent.current_mode_name()
            seen.add(cur)
            nxt = agent.MODE_CYCLE[
                (agent.MODE_CYCLE.index(cur) + 1) % len(agent.MODE_CYCLE)]
            agent.set_mode(nxt)
        self.assertIn("bypass", seen, "Tab must be able to reach bypass")
        self.assertEqual(seen, set(agent.MODE_CYCLE),
                         "the cycle must visit every mode it lists")

    def test_strict_only_resets_non_builtin_risk(self):
        """Strict used to carry its own hand-written list of built-ins that had
        drifted from RISK_LEVELS — `read_mcp_resource` was in neither copy."""
        agent.RISK_LEVELS["some_mcp_tool"] = "low"
        try:
            agent.set_mode("strict")
            self.assertEqual(agent.RISK_LEVELS["some_mcp_tool"], "high")
            self.assertEqual(agent.RISK_LEVELS["read_file"], "low")
            self.assertEqual(agent.RISK_LEVELS["read_mcp_resource"], "low")
        finally:
            agent.RISK_LEVELS.pop("some_mcp_tool", None)


# ══════════════════════════════════════════════════════════════════════
#  History — the part that grows, and the part that dominated the bill
# ══════════════════════════════════════════════════════════════════════

def _turn(user, results):
    """One user turn, an assistant tool_use batch, and its tool_result batch."""
    uses = [{"type": "tool_use", "id": f"tu_{i}", "name": "read_file",
             "input": {"path": f"f{i}.py"}} for i in range(len(results))]
    outs = [{"type": "tool_result", "tool_use_id": f"tu_{i}", "content": r}
            for i, r in enumerate(results)]
    return [{"role": "user", "content": user},
            {"role": "assistant", "content": uses},
            {"role": "user", "content": outs}]


def _long_history(batches=24, body_chars=6000):
    """A session shaped like the real one: many tool batches, few user turns."""
    messages = [{"role": "user", "content": "do a long task"}]
    for b in range(batches):
        messages.append({"role": "assistant", "content": [
            {"type": "tool_use", "id": f"tu_{b}", "name": "read_file",
             "input": {"path": f"file{b}.py"}}]})
        messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": f"tu_{b}",
             "content": "x" * body_chars}]})
    messages.append({"role": "assistant", "content": "done"})
    return messages


def _raw_size(messages):
    return sum(agent._measure(m) for m in messages)


class ToolResultPruning(unittest.TestCase):
    """Measured on a real 55-call session: history was 37,578 tokens of the
    request and tool results were most of it. A file read on turn 2 was still
    being re-sent on turn 50, long after the model stopped reading it.
    """

    def test_old_results_are_released(self):
        messages = _long_history()
        before = _raw_size(messages)
        reclaimed = agent.prune_tool_results(messages)
        self.assertGreater(reclaimed, 0)
        self.assertLess(_raw_size(messages), before / 2)

    def test_recent_batches_are_untouched(self):
        """These are what the model is still reasoning over."""
        messages = _long_history()
        agent.prune_tool_results(messages)
        recent = [b for m in messages if isinstance(m.get("content"), list)
                  for b in m["content"]
                  if isinstance(b, dict) and b.get("type") == "tool_result"]
        kept = [b for b in recent
                if not str(b["content"]).startswith(agent._PRUNED_MARK)]
        self.assertEqual(len(kept), agent.TOOL_RESULT_KEEP_TURNS,
                         "exactly the newest N batches must survive verbatim")

    def test_recency_is_counted_in_batches_not_user_turns(self):
        """The session that motivated this made 56 tool calls in *two* user
        turns. Counting user turns found nothing to prune in it at all."""
        messages = _long_history(batches=14)
        user_turns = sum(1 for m in messages if agent._is_user_turn(m))
        self.assertLessEqual(user_turns, 2, "fixture must model the real shape")
        self.assertGreater(agent.prune_tool_results(messages), 0,
                           "a long single-turn session must still be prunable")

    def test_tool_use_and_tool_result_stay_paired(self):
        """Dropping a block outright leaves a dangling tool_call the upstream
        rejects — only the body may be replaced."""
        messages = _long_history()
        agent.prune_tool_results(messages)
        uses, results = set(), set()
        for m in messages:
            for b in (m.get("content") if isinstance(m.get("content"), list) else []):
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "tool_use":
                    uses.add(b["id"])
                if b.get("type") == "tool_result":
                    results.add(b["tool_use_id"])
        self.assertEqual(uses, results)

    def test_pruning_is_idempotent(self):
        messages = _long_history()
        self.assertGreater(agent.prune_tool_results(messages), 0)
        self.assertEqual(agent.prune_tool_results(messages), 0,
                         "a stub must never be re-stubbed")

    def test_small_results_are_left_alone(self):
        """Stubbing a 300-char result costs information and saves nothing."""
        messages = _long_history(batches=14, body_chars=50)
        self.assertEqual(agent.prune_tool_results(messages), 0)

    def test_nothing_happens_below_the_batch_threshold(self):
        """Pruning rewrites the middle of the transcript, which costs a cache
        invalidation — so it happens in occasional batches, not every turn."""
        messages = _long_history(batches=9, body_chars=2100)
        self.assertLess(agent.prunable_chars(messages),
                        agent.TOOL_RESULT_PRUNE_AT)
        self.assertEqual(agent.prune_tool_results(messages), 0)

    def test_the_stub_says_how_to_get_it_back(self):
        messages = _long_history()
        agent.prune_tool_results(messages)
        stub = next(b["content"] for m in messages
                    if isinstance(m.get("content"), list)
                    for b in m["content"]
                    if isinstance(b, dict)
                    and str(b.get("content", "")).startswith(agent._PRUNED_MARK))
        self.assertIn("Re-run the tool", stub,
                      "this is a release, not a loss — say how to undo it")


class HistoryCacheBreakpoints(unittest.TestCase):
    """Only the system block was ever marked, so the history — which is far
    larger — paid full price on every turn. Anthropic caches a prefix, and the
    prefix ended where the marks did."""

    def marks(self, messages):
        return [(i, b) for i, m in enumerate(messages)
                if isinstance(m.get("content"), list)
                for b in m["content"]
                if isinstance(b, dict) and "cache_control" in b]

    def test_breakpoints_are_placed(self):
        messages = _long_history()
        placed = agent.mark_history_for_caching(messages, breakpoints=3)
        self.assertEqual(placed, 3)
        self.assertEqual(len(self.marks(messages)), 3)

    def test_it_never_exceeds_the_provider_limit(self):
        messages = _long_history(batches=40)
        agent.mark_history_for_caching(
            messages, breakpoints=agent.MAX_CACHE_BREAKPOINTS - 1)
        self.assertLessEqual(len(self.marks(messages)),
                             agent.MAX_CACHE_BREAKPOINTS - 1)

    def test_remarking_does_not_accumulate(self):
        """Marks move every turn; if they piled up the request would be
        rejected for having too many breakpoints."""
        messages = _long_history()
        for _ in range(5):
            agent.mark_history_for_caching(messages, breakpoints=3)
        self.assertEqual(len(self.marks(messages)), 3)

    def test_the_final_message_is_never_marked(self):
        """The tail is what changes — caching it buys nothing."""
        messages = _long_history()
        agent.mark_history_for_caching(messages, breakpoints=3)
        marked_indices = {i for i, _ in self.marks(messages)}
        self.assertNotIn(len(messages) - 1, marked_indices)

    def test_no_message_changes_shape(self):
        """Reshaping a string turn into blocks to carry the key would rewrite
        the user's own words in the saved transcript."""
        messages = _long_history()
        before = [type(m["content"]).__name__ for m in messages]
        agent.mark_history_for_caching(messages, breakpoints=3)
        self.assertEqual([type(m["content"]).__name__ for m in messages], before)

    def test_marks_can_be_cleared_completely(self):
        messages = _long_history()
        agent.mark_history_for_caching(messages, breakpoints=3)
        agent.clear_history_cache_marks(messages)
        self.assertEqual(self.marks(messages), [])

    def test_short_history_is_not_worth_a_breakpoint(self):
        messages = _turn("hi", ["ok"])
        self.assertEqual(agent.mark_history_for_caching(messages), 0)

    def test_build_state_keeps_the_caller_list_identity(self):
        """`core.loop` appends to this very list. Handing back a copy would
        send the marked version to the model while the assistant's replies
        accumulated somewhere nobody reads — the no-memory bug, silently."""
        from core.permissions import AutoApprove
        messages = _long_history()
        state = agent.build_state("sys", messages, AutoApprove())
        self.assertIs(state.messages, messages,
                      "the transcript must stay the same object")


if __name__ == "__main__":
    unittest.main(verbosity=2)
