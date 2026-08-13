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
    AssistantMessage,
    ContinuationGranted,
    ContinuationNeeded,
    ErrorOccurred,
    LoopDetected,
    RetryScheduled,
    TextDelta,
    ToolFinished,
    ToolResultTruncated,
    ToolStarted,
    TruncatedOutputDiscarded,
    TurnFinished,
)
from core.loop import clip_result, detect_loop, run_turn
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


class TestBypassContinuation(unittest.TestCase):
    """Bypass mode: the turn is never stopped to ask whether to keep going.

    `yolo` answers "may this tool run?" — a different question from "may the
    turn continue?", and it never silenced the second. A real session on
    deepseek-v4-flash-free approved every one of its 56 tool calls and still
    halted at the 40-call checkpoint, saved with complete=False in the middle
    of the task.
    """

    def _client(self, tool_calls: int):
        """A client that asks for `tool_calls` tools, then finishes."""
        responses = [
            FakeResponse([tool_block(id=f"tu_{i}", input={"n": i})], "tool_use")
            for i in range(tool_calls)
        ]
        responses.append(FakeResponse([text_block("done")], "end_turn"))
        return stub_client(*responses)

    def test_bypass_does_not_ask_at_the_checkpoint(self):
        state = make_state(self._client(6), tool_budget=1, auto_continue=True)
        adapter = TestAdapter(continue_answers=[False])   # would stop, if asked
        reply = adapter.run(state, "long task")
        self.assertEqual(reply, "done")
        self.assertEqual(adapter.continue_asks, 0,
                         "bypass mode must not put the question at all")
        self.assertEqual(len(adapter.of(ToolFinished)), 6)

    def test_without_bypass_the_question_is_still_asked(self):
        state = make_state(self._client(6), tool_budget=1, auto_continue=False)
        adapter = TestAdapter(continue_answers=[True])
        adapter.run(state, "long task")
        self.assertGreater(adapter.continue_asks, 0,
                           "the checkpoint must survive for every other mode")

    def test_extensions_are_still_announced(self):
        """A turn silently running to hundreds of calls is indistinguishable
        from a runaway — the events are the only trace of why it kept going."""
        state = make_state(self._client(4), tool_budget=1, auto_continue=True)
        adapter = TestAdapter()
        adapter.run(state, "go")
        self.assertTrue(adapter.of(ContinuationNeeded))
        self.assertTrue(adapter.of(ContinuationGranted))

    def test_auto_continuation_is_bounded(self):
        """"Do not ask me" must not become "bill me without limit"."""
        state = make_state(self._client(20), tool_budget=1, auto_continue=True,
                           max_auto_continuations=2)
        adapter = TestAdapter(continue_answers=[False])
        adapter.run(state, "runaway")
        self.assertEqual(adapter.continue_asks, 1,
                         "past the cap the user must be asked again")

    def test_the_cap_hands_control_back_rather_than_killing_the_turn(self):
        state = make_state(self._client(20), tool_budget=1, auto_continue=True,
                           max_auto_continuations=2)
        adapter = TestAdapter(continue_answers=[True])
        reply = adapter.run(state, "long but legitimate")
        self.assertEqual(reply, "done", "answering yes must resume normally")

    def test_a_stuck_loop_still_stops_in_bypass(self):
        """Loop detection is not negotiable, and bypass must not negotiate it."""
        state = make_state(stub_client(
            *[FakeResponse([tool_block(id="tu_1", input={"a": 1})], "tool_use")] * 6,
            FakeResponse([text_block("gave up")], "end_turn"),
        ), tool_budget=100, auto_continue=True)
        adapter = TestAdapter()
        adapter.run(state, "go")
        self.assertTrue(adapter.of(LoopDetected),
                        "bypass must not disable loop detection")


class TestContinuationPolicy(unittest.TestCase):
    """The policy lives on the state, not in the responder — so it is testable
    without a front end, and no adapter can get it wrong on its own."""

    def _state(self, **kw):
        return make_state(stub_client(FakeResponse([], "end_turn")), **kw)

    def test_default_always_asks(self):
        state = self._state()
        self.assertTrue(state.needs_continuation_approval(0))
        self.assertTrue(state.needs_continuation_approval(99))

    def test_bypass_stops_asking_until_the_cap(self):
        state = self._state(auto_continue=True, max_auto_continuations=3)
        self.assertFalse(state.needs_continuation_approval(0))
        self.assertFalse(state.needs_continuation_approval(2))
        self.assertTrue(state.needs_continuation_approval(3),
                        "at the cap the question comes back")


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

    def test_returned_error_string_is_not_a_success(self):
        """Handlers signal soft failure by *returning* "Error: ...", never by
        raising. Scoring `ok` on "did not raise" made 116/116 tool calls in
        the 20260811 sweep report OK while four were command timeouts and one
        was an unreachable MCP server."""
        state = make_state(stub_client(
            FakeResponse([tool_block()], "tool_use"),
            FakeResponse([text_block("done")], "end_turn"),
        ), execute_tool=lambda n, a: "Error: command timed out after 120s")
        adapter = TestAdapter()

        adapter.run(state, "go")
        self.assertFalse(adapter.of(ToolFinished)[0].ok)

    def test_failed_tool_result_is_flagged_to_the_model(self):
        """Without is_error the model has to infer failure from prose."""
        messages: list = []
        state = make_state(stub_client(
            FakeResponse([tool_block()], "tool_use"),
            FakeResponse([text_block("done")], "end_turn"),
        ), messages=messages,
           execute_tool=lambda n, a: "Error: no MCP servers are connected.")
        TestAdapter().run(state, "go")

        results = [b for m in messages if isinstance(m.get("content"), list)
                   for b in m["content"]
                   if isinstance(b, dict) and b.get("type") == "tool_result"]
        self.assertTrue(results, "no tool_result block was recorded")
        self.assertTrue(results[0].get("is_error"))

    def test_successful_tool_result_carries_no_error_flag(self):
        messages: list = []
        state = make_state(stub_client(
            FakeResponse([tool_block()], "tool_use"),
            FakeResponse([text_block("done")], "end_turn"),
        ), messages=messages, execute_tool=lambda n, a: "[exit 0 — ok]\nhello")
        TestAdapter().run(state, "go")

        results = [b for m in messages if isinstance(m.get("content"), list)
                   for b in m["content"]
                   if isinstance(b, dict) and b.get("type") == "tool_result"]
        self.assertTrue(results)
        self.assertNotIn("is_error", results[0])

    def test_file_content_beginning_with_error_is_not_a_failure(self):
        """read_file prefixes every line with a number, so a log file whose
        first line reads "Error: ..." cannot be mistaken for a failed call."""
        body = "     1\tError: something inside a log file\n"
        state = make_state(stub_client(
            FakeResponse([tool_block()], "tool_use"),
            FakeResponse([text_block("done")], "end_turn"),
        ), execute_tool=lambda n, a: body)
        adapter = TestAdapter()

        adapter.run(state, "go")
        self.assertTrue(adapter.of(ToolFinished)[0].ok)


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

    def test_a_partial_reply_is_also_retried_with_more_room(self):
        """A half-written document is not a partial success.

        This used to require the reply to be *empty* before retrying, on the
        grounds that re-running re-bills work already done. But the retry runs
        with four times the budget, so it does not reproduce the same cut-off —
        and the case it excluded is the one users actually hit: a lab manual
        truncated mid-sentence, with the only advice being an env var that was
        itself clamped and inert.
        """
        state, adapter, reply = self._run([text_block("ЛАБОРАТОРНА РОБОТА №6")])
        self.assertGreater(state.max_tokens, 8192)
        self.assertTrue(
            [e for e in adapter.of(ErrorOccurred)
             if "Retrying once with" in e.message],
            "a partial truncation must escalate like an empty one")

    def test_discarding_a_partial_reply_is_announced(self):
        """On the streamed path those characters are already on screen."""
        state, adapter, reply = self._run([text_block("ЛАБОРАТОРНА РОБОТА №6")])
        discards = adapter.of(TruncatedOutputDiscarded)
        self.assertEqual(len(discards), 1, "the user must be told once")
        self.assertEqual(discards[0].previous_limit, 8192)
        self.assertGreater(discards[0].new_limit, 8192)
        self.assertGreater(discards[0].discarded_chars, 0)

    def test_a_partial_reply_is_still_kept_and_flagged_once_retries_run_out(self):
        state, adapter, reply = self._run([text_block("ЛАБОРАТОРНА РОБОТА №6")])
        self.assertIn("ЛАБОРАТОРНА", reply, "half a document still beats none")
        self.assertIn("cut off", adapter.of(ErrorOccurred)[0].message)

    def test_a_failed_retry_hands_back_what_it_discarded(self):
        """Escalation must not be able to leave the user worse off.

        The retry throws away a partial answer to ask for a complete one. If
        that second call never lands — a quota, a 5xx — returning nothing would
        make escalating strictly worse than not escalating, which is a trap:
        the feature would cost users the very output it exists to protect.
        """
        client = MagicMock()
        client.messages.create.side_effect = [
            FakeResponse([text_block("ЛАБОРАТОРНА РОБОТА №6")], "max_tokens"),
            Exception("insufficient_quota"),
        ]
        state = make_state(client)
        adapter = TestAdapter()
        reply = adapter.run(state, "make me a lab guide")
        self.assertIn("ЛАБОРАТОРНА", reply,
                      "the discarded partial answer was lost outright")
        self.assertEqual(adapter.of(TurnFinished)[0].reply, reply)
        self.assertEqual(state.messages[-1],
                         {"role": "assistant", "content": reply},
                         "and it must survive into the transcript")

    def test_a_successful_retry_replaces_rather_than_appends(self):
        """The discarded text must not be concatenated with the retry."""
        client = MagicMock()
        client.messages.create.side_effect = [
            FakeResponse([text_block("first half")], "max_tokens"),
            FakeResponse([text_block("the complete answer")], "end_turn"),
        ]
        state = make_state(client)
        adapter = TestAdapter()
        reply = adapter.run(state, "go")
        self.assertEqual(reply, "the complete answer")
        self.assertNotIn("first half", reply)
        self.assertEqual([m for m in state.messages if m["role"] == "assistant"],
                         [{"role": "assistant", "content": "the complete answer"}])

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


class FakeStreamEvent:
    def __init__(self, type, **kw):
        self.type = type
        for k, v in kw.items():
            setattr(self, k, v)


class FakeStream:
    """Stands in for `client.messages.stream(...)`."""

    def __init__(self, text, stop_reason):
        self._text = text
        self._stop_reason = stop_reason

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        for chunk in self._text:
            yield FakeStreamEvent(
                "content_block_delta",
                delta=FakeStreamEvent("text_delta", text=chunk))
        yield FakeStreamEvent("message_stop")

    def get_final_message(self):
        return FakeResponse([text_block(self._text)], self._stop_reason)


def streaming_client(text, stop_reason, *after):
    """A client whose streamed call ends with `stop_reason`."""
    client = MagicMock()
    client.messages.stream.return_value = FakeStream(text, stop_reason)
    if after:
        client.messages.create.side_effect = list(after)
    else:
        client.messages.create.return_value = FakeResponse(
            [text_block("done")], "end_turn")
    return client


class TestTruncationOnTheStreamingPath(unittest.TestCase):
    """The path that was actually broken, and had no test at all.

    Escalation was added to the non-streamed branch only, while `_stream_call`
    reported the truncation itself and returned — so on any provider that
    streams (most of them) the retry never ran. A live session showed the
    post-escalation message quoting the *original* 8192 limit, which is the
    tell: the number never went up.
    """

    def _state(self, client, **kw):
        kw.setdefault("execute_tool", lambda n, a: "ok")
        kw.setdefault("responder", AutoApprove())
        return AgentState(
            system_prompt="sys", messages=[], get_client=lambda: client,
            get_model=lambda: "test-model", streaming_enabled=True, **kw)

    def test_an_empty_streamed_truncation_escalates(self):
        state = self._state(streaming_client("", "max_tokens"))
        adapter = TestAdapter()
        adapter.run(state, "write me a lab guide")
        messages = [e.message for e in adapter.of(ErrorOccurred)]
        self.assertTrue(any("Retrying once with" in m for m in messages),
                        f"no escalation on the streaming path: {messages}")
        self.assertGreater(state.max_tokens, 8192)

    def test_the_escalated_limit_is_what_gets_reported(self):
        # The live symptom was the final message still quoting 8192.
        state = self._state(streaming_client("", "max_tokens"))
        adapter = TestAdapter()
        adapter.run(state, "go")
        final = adapter.of(ErrorOccurred)[-1].message
        self.assertIn(str(state.max_tokens), final)

    def test_a_partial_streamed_reply_is_retried_then_kept_and_flagged(self):
        state = self._state(streaming_client("ЛАБОРАТОРНА РОБОТА", "max_tokens"))
        adapter = TestAdapter()
        reply = adapter.run(state, "go")
        self.assertIn("ЛАБОРАТОРНА", reply)
        self.assertIn("cut off", adapter.of(ErrorOccurred)[0].message)
        self.assertGreater(state.max_tokens, 8192,
                           "a partial reply must escalate once, like an empty one")

    def test_the_streamed_discard_is_announced_before_the_answer_restarts(self):
        """Streaming has already printed the discarded text, so silence here
        looks like the agent repeating itself half-way through a document."""
        state = self._state(streaming_client("ЛАБОРАТОРНА РОБОТА", "max_tokens"))
        adapter = TestAdapter()
        adapter.run(state, "go")
        self.assertEqual(len(adapter.of(TruncatedOutputDiscarded)), 1)

    def test_a_normal_streamed_finish_reports_nothing(self):
        state = self._state(streaming_client("all done", "end_turn"))
        adapter = TestAdapter()
        reply = adapter.run(state, "hi")
        self.assertEqual(reply, "all done")
        self.assertEqual(adapter.of(ErrorOccurred), [])


class PathParity(unittest.TestCase):
    """Run one scenario down both model paths and require the same behaviour.

    `run_turn` calls the model two ways — streamed, and non-streamed when tools
    are wanted or streaming is off — and the two branches are written
    separately. Nothing made them agree, and the default in every test here was
    `streaming_enabled=False`, so the streamed branch was effectively untested:
    an escalation retry was added to one branch, shipped, and did nothing at all
    for the providers that stream. Which is most of them.

    Subclass this and write the assertion once. It runs twice.
    """

    def run_scenario(self, text, stop_reason, streaming):
        """Drive one turn either way. Returns (reply, state, adapter)."""
        if streaming:
            client = streaming_client(text, stop_reason)
        else:
            client = MagicMock()
            content = [text_block(text)] if text else []
            client.messages.create.return_value = FakeResponse(content, stop_reason)
        state = AgentState(
            system_prompt="sys", messages=[], get_client=lambda: client,
            get_model=lambda: "test-model", streaming_enabled=streaming,
            execute_tool=lambda n, a: "ok", responder=AutoApprove())
        adapter = TestAdapter()
        reply = adapter.run(state, "go")
        return reply, state, adapter

    def both(self, text, stop_reason):
        """Yields (label, reply, state, adapter) for each path."""
        for streaming in (False, True):
            with self.subTest(path="streamed" if streaming else "non-streamed"):
                yield self.run_scenario(text, stop_reason, streaming)


class TestTruncationBehavesTheSameOnBothPaths(PathParity):

    def test_an_empty_truncation_escalates_either_way(self):
        for reply, state, adapter in self.both("", "max_tokens"):
            messages = [e.message for e in adapter.of(ErrorOccurred)]
            self.assertTrue(any("Retrying once with" in m for m in messages),
                            f"no escalation: {messages}")
            self.assertGreater(state.max_tokens, 8192)

    def test_the_reported_limit_is_the_escalated_one_either_way(self):
        for reply, state, adapter in self.both("", "max_tokens"):
            self.assertIn(str(state.max_tokens),
                          adapter.of(ErrorOccurred)[-1].message)

    def test_a_partial_truncation_escalates_either_way(self):
        for reply, state, adapter in self.both("ЛАБОРАТОРНА РОБОТА", "max_tokens"):
            self.assertIn("ЛАБОРАТОРНА", reply)
            self.assertIn("cut off", adapter.of(ErrorOccurred)[0].message)
            self.assertGreater(state.max_tokens, 8192)

    def test_the_discard_is_announced_either_way(self):
        for reply, state, adapter in self.both("ЛАБОРАТОРНА РОБОТА", "max_tokens"):
            discards = adapter.of(TruncatedOutputDiscarded)
            self.assertEqual(len(discards), 1,
                             "the discard notice must not depend on the path")
            self.assertEqual(discards[0].previous_limit, 8192)

    def test_escalation_stays_bounded_to_once_either_way(self):
        # Otherwise a model that always truncates loops, quadrupling the bill.
        for reply, state, adapter in self.both("partial", "max_tokens"):
            self.assertEqual(len(adapter.of(TruncatedOutputDiscarded)), 1)
            self.assertEqual(
                len([e for e in adapter.of(ErrorOccurred)
                     if "Retrying once with" in e.message]), 1)

    def test_a_normal_finish_is_silent_either_way(self):
        for reply, state, adapter in self.both("all done", "end_turn"):
            self.assertEqual(reply, "all done")
            self.assertEqual(adapter.of(ErrorOccurred), [])

    def test_the_reply_is_recorded_either_way(self):
        # Without this the streaming path silently loses the assistant turn.
        for reply, state, adapter in self.both("remembered", "end_turn"):
            self.assertEqual(state.messages[-1],
                             {"role": "assistant", "content": "remembered"})

    def test_the_failure_reaches_the_session_file_either_way(self):
        for reply, state, adapter in self.both("", "max_tokens"):
            self.assertIn("max_tokens", state.last_error or "")

    def test_the_text_reaches_the_user_either_way(self):
        # The two paths deliver it differently on purpose — streamed as
        # TextDelta as it arrives, non-streamed as one AssistantMessage — and
        # emitting both would render the reply twice. What must not differ is
        # that the user sees it. Asserted on the outcome, not the mechanism.
        for reply, state, adapter in self.both("visible text", "end_turn"):
            rendered = "".join(e.text for e in adapter.of(TextDelta)) \
                or "".join(e.text for e in adapter.of(AssistantMessage))
            self.assertEqual(rendered, "visible text")


class TestToolHandlingConvergesOnOnePath(unittest.TestCase):
    """Tool events live only in the non-streamed branch, and that is correct.

    A streamed call that wants tools returns immediately and falls through to
    `_model_call`, because streaming does not yield complete tool_use blocks.
    So permission prompts, loop detection, result truncation and the
    continuation budget all run in exactly one place. Written down because the
    asymmetry looks like the escalation bug and is not: a future reader who
    "fixes" it by duplicating tool handling into the streamed branch would be
    creating the very divergence this file guards against.
    """

    def test_a_streamed_tool_request_is_completed_non_streamed(self):
        client = streaming_client(
            "", "tool_use",
            FakeResponse([tool_block()], "tool_use"),
            FakeResponse([text_block("done")], "end_turn"))
        state = AgentState(
            system_prompt="sys", messages=[], get_client=lambda: client,
            get_model=lambda: "test-model", streaming_enabled=True,
            execute_tool=lambda n, a: "ok", responder=AutoApprove())
        adapter = TestAdapter()
        reply = adapter.run(state, "list the files")
        self.assertEqual(reply, "done")
        self.assertTrue(adapter.of(ToolStarted), "tools must still run")
        self.assertTrue(adapter.of(ToolFinished))


#: The exact body that ended a live session: a thinking-mode DeepSeek behind
#: Zen, refusing a conversation that arrived without the reasoning it had
#: produced. The user saw this JSON verbatim, prefixed with "An unexpected
#: error occurred", and nothing telling them what to do about it.
ZEN_REASONING_400 = RuntimeError(
    'HTTP 400 from https://opencode.ai/zen/v1/chat/completions: '
    '{"error":{"param":null,"type":"invalid_request_error",'
    '"code":"invalid_request_error","message":"Error from provider (Console): '
    'Upstream request failed: [invalid_request_error] The `reasoning_content` '
    'in the thinking mode must be passed back to the API."}}')


class TestClientErrorsAreDiagnosed(PathParity):
    """A 4xx must be named, whichever way the call was made.

    Client errors were diagnosed only inside the `anthropic.InternalServerError`
    branch — which the in-process adapter never raises. It reports a rejected
    request as a plain RuntimeError carrying the upstream's JSON, so on every
    OpenAI-wire provider the diagnosis was skipped and the body was dumped raw.

    Nothing here asserts a retry: a 4xx is fatal to the turn before and after
    this change, because the retry would send the identical payload.
    """

    def both_failing(self, err):
        """Drive one failing turn down each path. Yields (state, adapter)."""
        for streaming in (False, True):
            with self.subTest(path="streamed" if streaming else "non-streamed"):
                client = MagicMock()
                client.messages.create.side_effect = err
                client.messages.stream.side_effect = err
                state = AgentState(
                    system_prompt="sys", messages=[], get_client=lambda: client,
                    get_model=lambda: "test-model", streaming_enabled=streaming,
                    execute_tool=lambda n, a: "ok", responder=AutoApprove())
                adapter = TestAdapter()
                adapter.run(state, "go")
                yield state, adapter

    def test_a_rejected_request_is_never_reported_as_unexpected(self):
        for state, adapter in self.both_failing(ZEN_REASONING_400):
            messages = [e.message for e in adapter.of(ErrorOccurred)]
            self.assertTrue(messages, "the failure must be reported at all")
            self.assertFalse(
                any("An unexpected error occurred" in m for m in messages),
                f"a 400 is a known outcome, not a surprise: {messages}")

    def test_the_thinking_mode_rejection_says_what_to_do(self):
        for state, adapter in self.both_failing(ZEN_REASONING_400):
            final = adapter.of(ErrorOccurred)[-1].message
            self.assertIn("thinking mode", final)
            self.assertIn("/model", final, "the user needs an action, not a name")

    def test_the_upstream_sentence_survives_the_excerpt(self):
        """The adapter prints `message` and never `detail`, and a head-only
        excerpt of this body is all JSON preamble — the words that identify
        the problem are the last ones."""
        for state, adapter in self.both_failing(ZEN_REASONING_400):
            final = adapter.of(ErrorOccurred)[-1].message
            self.assertIn("reasoning_content", final)

    def test_the_reason_reaches_the_session_file(self):
        for state, adapter in self.both_failing(ZEN_REASONING_400):
            self.assertIn("400", state.last_error or "")

    def test_a_refused_key_is_not_described_as_an_oversized_payload(self):
        err = RuntimeError('HTTP 401 from https://api.example/v1/chat/completions: '
                           '{"error":{"message":"Missing Authentication header"}}')
        for state, adapter in self.both_failing(err):
            final = adapter.of(ErrorOccurred)[-1].message
            self.assertIn("/provider", final)
            self.assertNotIn("/compact", final)

    def test_a_retryable_body_keeps_the_retry_it_had(self):
        """`is_retryable_error` wins over `is_client_error` here exactly as it
        already does in the SDK branch. Asserted so that tightening the
        diagnosis never quietly converts a transient failure into a fatal one."""
        err = RuntimeError("HTTP 400: upstream timeout while routing")
        for state, adapter in self.both_failing(err):
            self.assertTrue(adapter.of(RetryScheduled),
                            "a body naming a timeout must still be retried")


class TestTheCacheHookRunsBeforeEveryCall(PathParity):
    """Prompt-cache breakpoints must be re-cut against the history that exists
    *now*, not the history that existed when the turn started.

    The host was asked once per user turn. A turn that makes 29 tool calls then
    appends 58 messages behind marks placed before any of them existed, so the
    fastest-growing part of the transcript was the part that never got cached —
    the case `mark_history_for_caching` itself names as needing it most.
    """

    def test_the_hook_fires_before_the_model_call_either_way(self):
        for streaming in (False, True):
            with self.subTest(path="streamed" if streaming else "non-streamed"):
                seen = []
                client = (streaming_client("hi", "end_turn") if streaming
                          else stub_client(FakeResponse([text_block("hi")], "end_turn")))
                state = AgentState(
                    system_prompt="sys", messages=[], get_client=lambda: client,
                    get_model=lambda: "test-model", streaming_enabled=streaming,
                    execute_tool=lambda n, a: "ok", responder=AutoApprove(),
                    before_model_call=lambda msgs: seen.append(len(msgs)))
                TestAdapter().run(state, "go")
                self.assertTrue(seen, "the host never got a chance to re-mark")

    def test_it_is_called_again_for_each_step_of_a_tool_turn(self):
        """One call per turn was the bug. Three model calls means three
        chances to move the breakpoint forward."""
        client = stub_client(
            FakeResponse([tool_block(id="tu_1")], "tool_use"),
            FakeResponse([tool_block(id="tu_2", name="read_file")], "tool_use"),
            FakeResponse([text_block("done")], "end_turn"))
        seen = []
        state = make_state(client,
                           before_model_call=lambda msgs: seen.append(len(msgs)))
        TestAdapter().run(state, "go")
        self.assertEqual(len(seen), 3, f"one hook call per model call: {seen}")

    def test_the_hook_sees_the_history_that_has_grown(self):
        """It is handed the live list, so a breakpoint it places lands on the
        tool results this turn just produced."""
        client = stub_client(
            FakeResponse([tool_block()], "tool_use"),
            FakeResponse([text_block("done")], "end_turn"))
        seen = []
        state = make_state(client,
                           before_model_call=lambda msgs: seen.append(len(msgs)))
        TestAdapter().run(state, "go")
        self.assertLess(seen[0], seen[-1],
                        "the second call must see the tool round-trip appended")

    def test_a_hook_that_raises_cannot_kill_the_turn(self):
        """It runs before every request in the session. A caching hint that can
        end a turn is worse than no caching at all."""
        def explode(msgs):
            raise RuntimeError("breakpoint maths went wrong")

        for reply, state, adapter in self.both("all done", "end_turn"):
            pass  # baseline: both paths reply normally
        client = stub_client(FakeResponse([text_block("all done")], "end_turn"))
        state = make_state(client, before_model_call=explode)
        self.assertEqual(TestAdapter().run(state, "go"), "all done")

    def test_no_hook_is_a_supported_configuration(self):
        # Front ends that do not care about caching must not have to supply one.
        state = make_state(stub_client(
            FakeResponse([text_block("fine")], "end_turn")))
        self.assertIsNone(state.before_model_call)
        self.assertEqual(TestAdapter().run(state, "go"), "fine")


class TestCachedInputIsAccounted(unittest.TestCase):
    """What the provider served from cache is reported, never subtracted.

    Folding it into the total would make a well-cached turn look cheap and hide
    the number worth watching: how much is still being re-sent at full price.
    """

    @staticmethod
    def _usage(input_tokens, cached):
        return MagicMock(input_tokens=input_tokens, output_tokens=5,
                         cached_input_tokens=cached)

    def test_the_cached_share_is_recorded(self):
        response = FakeResponse([text_block("hi")], "end_turn")
        response.usage = self._usage(1000, 900)
        state = make_state(stub_client(response))
        TestAdapter().run(state, "go")
        self.assertEqual(state.usage["input"], 1000)
        self.assertEqual(state.usage["cached_input"], 900)

    def test_it_accumulates_across_the_turn(self):
        first = FakeResponse([tool_block()], "tool_use")
        first.usage = self._usage(1000, 400)
        second = FakeResponse([text_block("done")], "end_turn")
        second.usage = self._usage(1200, 1100)
        state = make_state(stub_client(first, second))
        TestAdapter().run(state, "go")
        self.assertEqual(state.usage["total_input"], 2200)
        self.assertEqual(state.usage["total_cached_input"], 1500)

    def test_the_anthropic_sdk_spelling_is_read_too(self):
        """The native SDK says `cache_read_input_tokens`, and Anthropic is the
        one provider where the agent places the breakpoints itself — so this is
        the path whose caching most needs to be measurable."""
        response = FakeResponse([text_block("hi")], "end_turn")
        response.usage = MagicMock(input_tokens=1000, output_tokens=5,
                                   cache_read_input_tokens=880)
        del response.usage.cached_input_tokens
        state = make_state(stub_client(response))
        TestAdapter().run(state, "go")
        self.assertEqual(state.usage["cached_input"], 880)

    def test_a_provider_that_says_nothing_reports_zero(self):
        """A counter that is summed and rendered with `:,` must never hold a
        non-number, whatever shape of usage object turns up."""
        response = FakeResponse([text_block("hi")], "end_turn")
        response.usage = MagicMock(input_tokens=10, output_tokens=5)
        del response.usage.cached_input_tokens
        del response.usage.cache_read_input_tokens
        state = make_state(stub_client(response))
        TestAdapter().run(state, "go")
        self.assertEqual(state.usage["cached_input"], 0)
        self.assertGreater(state.usage["input"], 0)

    def test_a_nonsense_usage_field_becomes_zero(self):
        response = FakeResponse([text_block("hi")], "end_turn")
        response.usage = MagicMock(input_tokens=10, output_tokens=5,
                                   cached_input_tokens="lots")
        state = make_state(stub_client(response))
        TestAdapter().run(state, "go")
        self.assertEqual(state.usage["cached_input"], 0)


class TestClippedResultsKeepTheirTail(unittest.TestCase):
    """Head-only clipping dropped the half that carries the diagnosis.

    A failing command's exit line, a traceback's exception, a test runner's
    failure count: all at the end. The model was handed the banner and none of
    the error, and then reported the tool as having produced nothing useful.
    """

    def test_a_short_result_is_returned_untouched(self):
        self.assertEqual(clip_result("small", 100), "small")

    def test_both_ends_survive(self):
        text = "HEAD" + ("x" * 5000) + "TAIL"
        clipped = clip_result(text, 1000)
        self.assertTrue(clipped.startswith("HEAD"))
        self.assertTrue(clipped.endswith("TAIL"))

    def test_the_budget_is_unchanged(self):
        """Same characters of content as head-only clipping kept, just taken
        from both ends — this is not a licence to carry more context."""
        clipped = clip_result("y" * 9000, 1000)
        self.assertEqual(sum(c == "y" for c in clipped), 1000,
                         "exactly the limit survives, from both ends")
        self.assertLessEqual(len(clipped) - 1000, 200,
                             "the marker is the only overhead")

    def test_the_gap_is_named_so_the_model_can_re_run(self):
        clipped = clip_result("z" * 9000, 1000)
        self.assertIn("cut from the middle", clipped)
        self.assertIn("9,000", clipped)

    def test_a_limit_too_small_to_split_behaves_as_before(self):
        clipped = clip_result("q" * 500, 2)
        self.assertTrue(clipped.startswith("qq"))
        self.assertIn("truncated", clipped)

    def test_the_turn_clips_with_it(self):
        """The wiring, not just the helper: a tool returning more than the
        ceiling must reach the model with its tail intact."""
        client = stub_client(
            FakeResponse([tool_block()], "tool_use"),
            FakeResponse([text_block("done")], "end_turn"))
        state = make_state(
            client,
            execute_tool=lambda n, a: "START" + ("m" * 40_000) + "FINISH",
            max_result_chars=2_000)
        adapter = TestAdapter()
        adapter.run(state, "run it")

        self.assertTrue(adapter.of(ToolResultTruncated))
        sent = state.messages[2]["content"][0]["content"]
        self.assertTrue(sent.startswith("START"))
        self.assertTrue(sent.endswith("FINISH"),
                        "the tail is where a failing tool says why")


class TestTheStreamingCallReportsRatherThanDecides(unittest.TestCase):
    """The architectural rule that keeps the two paths from drifting again.

    `_stream_call` gathers what happened; `run_turn` decides what to do about
    it. The moment the streaming call starts making policy — reporting an
    error, retrying, ending the turn — that policy exists in one branch only,
    which is exactly how the escalation bug happened.
    """

    @staticmethod
    def _stream_call_source() -> str:
        import inspect

        from core import loop
        return inspect.getsource(loop._stream_call)

    def test_the_streaming_call_yields_no_policy_events(self):
        source = self._stream_call_source()
        found = [name for name in
                 ("ErrorOccurred", "_report_truncation", "_escalate", "TurnFinished")
                 if name in source]
        self.assertEqual(
            found, [],
            f"_stream_call must report, not decide: {found} belongs in run_turn. "
            f"Policy placed here exists on the streamed path only, which is how "
            f"the escalation retry shipped doing nothing for streaming providers.")

    def test_it_records_the_stop_reason_for_the_caller(self):
        self.assertIn("last_stop_reason", self._stream_call_source(),
                      "run_turn cannot handle truncation it is never told about")


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
