#!/usr/bin/env python3
"""
Regression tests for the agent core (Phase 0 bugs, rewritten against events).

These are the Phase 0 tests from test_agent_loop.py, restated against
core.loop.run_turn and the TestAdapter. Assertions read as specifications
about what the core *emits*, rather than what it printed.

Run: python -m unittest discover -s tests -p "test_*.py"
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

PROJECT_DIR = Path(__file__).parent.parent.resolve()
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from adapters.test import TestAdapter
from core.events import (
    ContinuationNeeded,
    ErrorOccurred,
    LoopDetected,
    ToolFinished,
    ToolStarted,
    TurnFinished,
)
from core.loop import detect_loop, run_turn
from core.permissions import ApprovalStore, AutoApprove, DenyAll
from core.state import AgentState


class FakeBlock:
    """Stands in for an Anthropic SDK content block."""

    def __init__(self, type, **kw):
        self.type = type
        for k, v in kw.items():
            setattr(self, k, v)


class FakeResponse:
    def __init__(self, content, stop_reason):
        self.content = content
        self.stop_reason = stop_reason
        self.usage = MagicMock(input_tokens=10, output_tokens=5)


def text_block(text):
    return FakeBlock("text", text=text)


def tool_block(id="tu_1", name="list_files", input=None):
    return FakeBlock("tool_use", id=id, name=name, input=input or {})


def make_state(client, **kw):
    kw.setdefault("execute_tool", lambda n, a: "ok")
    # Explicit: AgentState has no default responder on purpose.
    kw.setdefault("responder", AutoApprove())
    return AgentState(
        system_prompt="sys",
        messages=kw.pop("messages", []),
        get_client=lambda: client,
        get_model=lambda: "test-model",
        streaming_enabled=False,  # exercise the non-streaming path
        **kw,
    )


def stub_client(*responses):
    client = MagicMock()
    if len(responses) == 1:
        client.messages.create.return_value = responses[0]
    else:
        client.messages.create.side_effect = list(responses)
    return client


class TestTranscriptIntegrity(unittest.TestCase):
    """P0-1: the agent must record its own turns."""

    def test_tool_round_trip_records_assistant_turn(self):
        """The assistant's tool_use message must sit between the user's
        request and the tool result. OpenAI-format upstreams reject a
        `role: tool` message that does not follow `tool_calls`."""
        client = stub_client(
            FakeResponse([tool_block()], "tool_use"),
            FakeResponse([text_block("done")], "end_turn"),
        )
        state = make_state(client)
        adapter = TestAdapter()

        reply = adapter.run(state, "list the files")

        self.assertEqual(reply, "done")
        self.assertEqual(
            [m["role"] for m in state.messages],
            ["user", "assistant", "user", "assistant"],
        )
        tool_use_turn = state.messages[1]["content"]
        self.assertTrue(any(getattr(b, "type", None) == "tool_use"
                            for b in tool_use_turn))
        self.assertEqual(state.messages[2]["content"][0]["type"], "tool_result")
        self.assertEqual(state.messages[2]["content"][0]["tool_use_id"], "tu_1")
        self.assertEqual(client.messages.create.call_count, 2)

    def test_plain_reply_is_recorded(self):
        """Without this the model has no memory of what it just said."""
        state = make_state(stub_client(
            FakeResponse([text_block("472")], "end_turn")))
        TestAdapter().run(state, "pick a number")

        self.assertEqual([m["role"] for m in state.messages],
                         ["user", "assistant"])
        self.assertEqual(state.messages[-1]["content"], "472")

    def test_second_turn_sees_the_first_reply(self):
        """End-to-end shape of the amnesia bug: the model must be sent its
        own previous answer on the following turn."""
        client = stub_client(
            FakeResponse([text_block("472")], "end_turn"),
            FakeResponse([text_block("472 again")], "end_turn"),
        )
        state = make_state(client)
        TestAdapter().run(state, "pick a number")
        TestAdapter().run(state, "what did you pick?")

        self.assertEqual([m["role"] for m in state.messages],
                         ["user", "assistant", "user", "assistant"])

    def test_every_tool_use_gets_a_result(self):
        """Parallel tool calls: a dangling tool_use with no matching
        tool_result makes the next request malformed."""
        state = make_state(stub_client(
            FakeResponse([tool_block(id="tu_1"), tool_block(id="tu_2")],
                         "tool_use"),
            FakeResponse([text_block("both done")], "end_turn"),
        ))
        TestAdapter().run(state, "list twice")

        results = state.messages[2]["content"]
        self.assertEqual({r["tool_use_id"] for r in results}, {"tu_1", "tu_2"})


class TestToolBudget(unittest.TestCase):
    """The budget is a checkpoint, not a ceiling — a long task must be able
    to finish."""

    def test_budget_exhaustion_asks_and_continues(self):
        """Answering yes must extend the budget and keep executing, not
        abandon the task."""
        state = make_state(stub_client(
            FakeResponse([tool_block(id="tu_1", input={"a": 1})], "tool_use"),
            FakeResponse([tool_block(id="tu_2", input={"b": 2})], "tool_use"),
            FakeResponse([text_block("finished")], "end_turn"),
        ), tool_budget=1)
        adapter = TestAdapter(continue_answers=[True])

        reply = adapter.run(state, "long task")

        self.assertEqual(reply, "finished")
        self.assertTrue(adapter.of(ContinuationNeeded))
        # Both tools actually ran — the second was not refused.
        self.assertEqual(len(adapter.of(ToolFinished)), 2)
        self.assertTrue(all(e.ok for e in adapter.of(ToolFinished)))

    def test_declining_winds_down_with_a_summary(self):
        """Answering no must still leave a well-formed transcript."""
        state = make_state(stub_client(
            FakeResponse([tool_block(id="tu_1", input={"a": 1})], "tool_use"),
            FakeResponse([tool_block(id="tu_2", input={"b": 2})], "tool_use"),
            FakeResponse([text_block("here's what I got")], "end_turn"),
        ), tool_budget=1)
        adapter = TestAdapter(continue_answers=[False])

        reply = adapter.run(state, "long task")

        self.assertEqual(reply, "here's what I got")
        # Every tool_use still got a matching tool_result.
        refusal = state.messages[-2]["content"]
        results = [b for b in refusal if b.get("type") == "tool_result"]
        self.assertEqual({r["tool_use_id"] for r in results}, {"tu_2"})
        self.assertTrue(any(b.get("type") == "text" for b in refusal))

    def test_distinct_calls_are_never_treated_as_a_loop(self):
        """A long run of different calls is progress, not a loop."""
        sigs = [f"read_file:{{'p': '{i}'}}" for i in range(50)]
        self.assertIsNone(detect_loop(sigs))


class TestLoopDetection(unittest.TestCase):
    """Genuine non-progress must stop fast, regardless of budget."""

    def test_identical_call_repeated_is_a_loop(self):
        self.assertIsNotNone(detect_loop(["a", "a", "a"]))

    def test_two_step_cycle_is_a_loop(self):
        self.assertIsNotNone(detect_loop(["a", "b", "a", "b", "a", "b"]))

    def test_three_step_cycle_is_a_loop(self):
        self.assertIsNotNone(
            detect_loop(["a", "b", "c", "a", "b", "c", "a", "b", "c"]))

    def test_near_miss_is_not_a_loop(self):
        self.assertIsNone(detect_loop(["a", "b", "a", "b", "a", "c"]))

    def test_loop_stops_the_turn_even_with_budget_left(self):
        same = tool_block(id="tu", input={"command": "python verify.py"})
        state = make_state(stub_client(
            FakeResponse([same], "tool_use"),
            FakeResponse([same], "tool_use"),
            FakeResponse([same], "tool_use"),
            FakeResponse([text_block("gave up")], "end_turn"),
        ), tool_budget=100)
        adapter = TestAdapter()

        reply = adapter.run(state, "go")

        self.assertEqual(reply, "gave up")
        self.assertTrue(adapter.of(LoopDetected))
        self.assertEqual(adapter.of(ContinuationNeeded), [],
                         "a stuck loop must not ask to continue")


class TestPermissions(unittest.TestCase):
    """Decisions flow through the responder; nothing mutates risk tiers."""

    def test_denied_tool_does_not_execute(self):
        executed = []
        state = make_state(
            stub_client(
                FakeResponse([tool_block(name="run_command",
                                         input={"command": "rm -rf /"})],
                             "tool_use"),
                FakeResponse([text_block("ok")], "end_turn"),
            ),
            execute_tool=lambda n, a: executed.append(n) or "ran",
            risk_of=lambda n, a=None: "high",
            auto_approve_low=False,
        )
        adapter = TestAdapter(responder=DenyAll())

        adapter.run(state, "delete everything")

        self.assertEqual(executed, [], "denied tool must not run")
        self.assertFalse(adapter.of(ToolFinished)[0].ok)

    def test_auto_approve_runs_headlessly(self):
        state = make_state(
            stub_client(
                FakeResponse([tool_block(name="run_command",
                                         input={"command": "ls"})], "tool_use"),
                FakeResponse([text_block("ok")], "end_turn"),
            ),
            risk_of=lambda n, a=None: "high",
            auto_approve_low=False,
        )
        adapter = TestAdapter(responder=AutoApprove())
        self.assertEqual(adapter.run(state, "list"), "ok")
        self.assertTrue(adapter.of(ToolFinished)[0].ok)

    def test_state_refuses_to_default_its_responder(self):
        """A permission system must not fail open. Omitting the responder has
        to be an error, not a silent grant of AutoApprove on every tool."""
        with self.assertRaises(TypeError):
            AgentState(
                system_prompt="sys",
                messages=[],
                get_client=lambda: None,
                get_model=lambda: "m",
            )

    def test_run_wires_the_responder_itself(self):
        """adapter.run() exists so the wiring cannot be forgotten."""
        state = make_state(
            stub_client(FakeResponse([text_block("ok")], "end_turn")),
            responder=AutoApprove(),
        )
        adapter = TestAdapter(responder=DenyAll())
        adapter.run(state, "hi")
        self.assertIs(state.responder, adapter)

    def test_approval_is_scoped_to_the_exact_call(self):
        """Approving `git status` must not thereby approve `rm -rf /`."""
        store = ApprovalStore()
        store.approve("run_command", {"command": "git status"})
        self.assertTrue(store.is_approved("run_command", {"command": "git status"}))
        self.assertFalse(store.is_approved("run_command", {"command": "rm -rf /"}))

    def test_tool_events_carry_risk_and_origin(self):
        state = make_state(
            stub_client(
                FakeResponse([tool_block(name="read_file")], "tool_use"),
                FakeResponse([text_block("ok")], "end_turn"),
            ),
            risk_of=lambda n, a=None: "low",
            origin_of=lambda n: "MCP: files",
        )
        adapter = TestAdapter()
        adapter.run(state, "read")

        started = adapter.of(ToolStarted)[0]
        self.assertEqual(started.risk, "low")
        self.assertEqual(started.origin, "MCP: files")


class TestToolFailureIsContained(unittest.TestCase):
    def test_raising_tool_does_not_kill_the_turn(self):
        def boom(name, args):
            raise RuntimeError("disk on fire")

        state = make_state(stub_client(
            FakeResponse([tool_block()], "tool_use"),
            FakeResponse([text_block("recovered")], "end_turn"),
        ), execute_tool=boom)
        adapter = TestAdapter()

        self.assertEqual(adapter.run(state, "go"), "recovered")
        finished = adapter.of(ToolFinished)[0]
        self.assertFalse(finished.ok)
        self.assertIn("disk on fire", finished.result)


class TestUsageAccounting(unittest.TestCase):
    def test_totals_accumulate_across_calls(self):
        state = make_state(stub_client(
            FakeResponse([tool_block()], "tool_use"),
            FakeResponse([text_block("done")], "end_turn"),
        ))
        adapter = TestAdapter()
        adapter.run(state, "go")

        self.assertEqual(state.usage["calls"], 2)
        self.assertEqual(state.usage["total_input"], 20)
        self.assertEqual(state.usage["input"], 10, "last call, for the ctx %")
        self.assertEqual(adapter.of(TurnFinished)[0].usage["total_output"], 10)


class TestTruncationIsReported(unittest.TestCase):
    """A turn cut off at the output limit must say so.

    From a real session on `deepseek-v4-flash-free`. Four consecutive turns
    showed `4,096 out` and no reply at all — the user asked "so where the
    files?", "where it is?", "create it?" and got silence each time. The model
    was a reasoning model spending the whole output budget thinking, hitting
    the cap before emitting any text or tool call. `stop_reason` was read only
    to answer "did it ask for tools?", so `max_tokens` fell through the same
    branch as a normal finish and vanished.
    """

    def _run(self, content):
        state = make_state(stub_client(FakeResponse(content, "max_tokens")))
        adapter = TestAdapter()
        reply = adapter.run(state, "make me a lab guide")
        return state, adapter, reply

    def test_an_empty_truncated_reply_is_retried_with_more_room(self):
        # Recoverable exactly once: reporting and stopping leaves the user to
        # discover an env var by themselves.
        state, adapter, reply = self._run([])
        errors = adapter.of(ErrorOccurred)
        self.assertEqual(len(errors), 2, "expected an escalation then a report")
        self.assertIn("Retrying once with", errors[0].message)
        self.assertGreater(state.max_tokens, 8192)
        self.assertEqual(reply, "")

    def test_the_escalation_is_bounded(self):
        from core.loop import MAX_OUTPUT_CEILING
        state, _, _ = self._run([])
        self.assertLessEqual(state.max_tokens, MAX_OUTPUT_CEILING)

    def test_it_only_escalates_once_per_turn(self):
        # Otherwise a model that never answers loops, quadrupling the bill.
        _, adapter, _ = self._run([])
        self.assertEqual(
            len([e for e in adapter.of(ErrorOccurred)
                 if "Retrying once with" in e.message]), 1)

    def test_the_final_message_changes_the_advice(self):
        # After escalation has been spent, "raise the limit" is no longer the
        # useful move — the shape of the request is.
        _, adapter, _ = self._run([])
        final = adapter.of(ErrorOccurred)[-1].message
        self.assertIn("one piece at a time", final)

    def test_a_partial_reply_is_not_retried(self):
        # The model was writing, so re-running only re-bills the same work.
        state, adapter, reply = self._run([text_block("ЛАБОРАТОРНА РОБОТА №6")])
        self.assertEqual(state.max_tokens, 8192)
        self.assertEqual(
            [e for e in adapter.of(ErrorOccurred)
             if "Retrying once with" in e.message], [])

    def test_a_partial_reply_is_kept_but_flagged(self):
        state, adapter, reply = self._run([text_block("ЛАБОРАТОРНА РОБОТА №6")])
        self.assertIn("ЛАБОРАТОРНА", reply, "half a document still beats none")
        self.assertIn("cut off", adapter.of(ErrorOccurred)[0].message)

    def test_the_failure_is_recorded_for_the_session_file(self):
        # Otherwise the transcript saves as a turn that simply produced nothing.
        state, _, _ = self._run([])
        self.assertIn("max_tokens", state.last_error or "")

    def test_a_normal_finish_reports_nothing(self):
        state = make_state(stub_client(FakeResponse([text_block("done")], "end_turn")))
        adapter = TestAdapter()
        adapter.run(state, "hi")
        self.assertEqual(adapter.of(ErrorOccurred), [])

    def test_a_truncated_tool_request_is_not_flagged_as_a_dead_end(self):
        # stop_reason "tool_use" means the call did come through.
        state = make_state(stub_client(
            FakeResponse([tool_block()], "tool_use"),
            FakeResponse([text_block("done")], "end_turn"),
        ))
        adapter = TestAdapter()
        adapter.run(state, "go")
        self.assertEqual(adapter.of(ErrorOccurred), [])


class TestOutputTokenCeiling(unittest.TestCase):
    """Nothing probes max_output_tokens, so its default is the real limit."""

    def test_the_default_is_not_below_the_agent_ceiling(self):
        import agent
        import provider_manager
        default = provider_manager.Capabilities().max_output_tokens
        # agent applies min(MAX_TOKENS, max_output_tokens) as a hard ceiling, so
        # a low unmeasured default silently caps every provider. It was 4096.
        self.assertGreaterEqual(default, agent.MAX_TOKENS)

    def test_the_ceiling_is_configurable(self):
        import os

        import agent
        saved = os.environ.get("AGENT_MAX_TOKENS")
        try:
            os.environ["AGENT_MAX_TOKENS"] = "32000"
            self.assertEqual(agent._env_int("AGENT_MAX_TOKENS", 8192, 256), 32000)
            for junk in ("", "abc", "0", "-5"):
                os.environ["AGENT_MAX_TOKENS"] = junk
                self.assertEqual(agent._env_int("AGENT_MAX_TOKENS", 8192, 256), 8192)
        finally:
            os.environ.pop("AGENT_MAX_TOKENS", None)
            if saved is not None:
                os.environ["AGENT_MAX_TOKENS"] = saved


if __name__ == "__main__":
    unittest.main()
