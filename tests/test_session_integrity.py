#!/usr/bin/env python3
"""
Regression tests for session integrity and telemetry (Phase 6, P6-11 / P6-8).

The defect these guard against: session 20260803_122837_60bfa9 was saved with
eight user prompts and zero assistant messages — 1.4 KB on disk — and was then
described in a generated report as eight turns of completed work including a
118-test suite run. Nothing in the file said otherwise, and its token_usage
was a process-global counter showing 1.6M input tokens for work that never
happened.

Run: python -m unittest tests.test_session_integrity -v
"""
import json
import sys
import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent.resolve()
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import agent
import session_manager


def turn(role: str, text: str) -> dict:
    return {"role": role, "content": text}


class TestTranscriptAudit(unittest.TestCase):

    def test_complete_transcript(self):
        msgs = [turn("user", "a"), turn("assistant", "b"),
                turn("user", "c"), turn("assistant", "d")]
        audit = session_manager.audit_transcript(msgs)
        self.assertTrue(audit["complete"])
        self.assertEqual(audit["orphaned_user_turns"], [])

    def test_trailing_user_turn_is_orphaned(self):
        """The last turn produced nothing."""
        msgs = [turn("user", "a"), turn("assistant", "b"), turn("user", "c")]
        audit = session_manager.audit_transcript(msgs)
        self.assertFalse(audit["complete"])
        self.assertEqual(audit["orphaned_user_turns"], [2])

    def test_consecutive_user_turns_are_orphaned(self):
        """The exact shape of session 122837: prompts with no replies."""
        msgs = [turn("user", f"t{i}") for i in range(8)]
        audit = session_manager.audit_transcript(msgs)
        self.assertFalse(audit["complete"])
        self.assertEqual(len(audit["orphaned_user_turns"]), 8)
        self.assertEqual(audit["assistant_messages"], 0)

    def test_mid_session_gap_detected(self):
        """Session 122232: turns 1-2 replied, turns 3-6 did not."""
        msgs = [turn("user", "a"), turn("assistant", "b"),
                turn("user", "c"), turn("user", "d"), turn("user", "e")]
        audit = session_manager.audit_transcript(msgs)
        self.assertFalse(audit["complete"])
        self.assertIn(2, audit["orphaned_user_turns"])

    def test_empty_transcript_is_complete(self):
        self.assertTrue(session_manager.audit_transcript([])["complete"])


class SessionFileTestCase(unittest.TestCase):
    """Saves real session files and removes them afterwards."""

    def setUp(self):
        self.saved: list[str] = []

    def tearDown(self):
        for sid in self.saved:
            (session_manager.get_session_dir() / f"{sid}.json").unlink(missing_ok=True)

    def save(self, messages, telemetry=None, **kw) -> dict:
        sid = session_manager.save_session(
            messages, summary="phase6 test", model="test",
            token_usage=kw.pop("token_usage", {"input": 0, "output": 0, "calls": 0}),
            telemetry=telemetry if telemetry is not None else
            {"turn_metrics": {}, "tool_log": [], "failed_turns": []},
            **kw)
        self.saved.append(sid)
        path = session_manager.get_session_dir() / f"{sid}.json"
        return json.loads(path.read_text(encoding="utf-8"))


class TestIncompleteSessionMarked(SessionFileTestCase):

    def test_incomplete_session_marked(self):
        data = self.save([turn("user", f"t{i}") for i in range(4)])
        self.assertFalse(data["complete"])
        self.assertIn("incomplete_reason", data)
        self.assertEqual(data["incomplete_reason"]["assistant_messages"], 0)

    def test_complete_session_marked(self):
        data = self.save([turn("user", "a"), turn("assistant", "b")])
        self.assertTrue(data["complete"])
        self.assertNotIn("incomplete_reason", data)

    def test_failed_turn_marks_session_incomplete(self):
        """Retries exhausted on one turn taints the session even if a later
        turn succeeded — report 3's 'bug 7' made visible."""
        data = self.save(
            [turn("user", "a"), turn("assistant", "b")],
            telemetry={"turn_metrics": {}, "tool_log": [],
                       "failed_turns": [{"turn": 1, "reason": "RateLimitError",
                                         "error": "429 rate_limit"}]})
        self.assertFalse(data["complete"])
        self.assertEqual(
            data["incomplete_reason"]["failed_turns"][0]["reason"], "RateLimitError")

    def test_round_trips_through_load(self):
        data = self.save([turn("user", "a"), turn("assistant", "b")])
        loaded = session_manager.load_session(data["id"])
        self.assertIsNotNone(loaded)


class TestTelemetryReachesDisk(SessionFileTestCase):
    """`session_data` lists its keys explicitly, so anything session_telemetry()
    grows is silently dropped unless it is added here too.

    context_events was produced correctly for the whole V3/V4 work and never
    written, which made compaction unobservable in exactly the reports it was
    added for. These tests assert against the *file*, not against
    session_telemetry(), because that is the gap the earlier tests missed.
    """

    def test_context_events_are_written(self):
        data = self.save(
            [turn("user", "a"), turn("assistant", "b")],
            telemetry={"turn_metrics": {}, "tool_log": [], "failed_turns": [],
                       "context_events": [{"turn": 3, "strategy": "summary",
                                           "before_tokens": 200_000,
                                           "after_tokens": 40_000,
                                           "reclaimed_tokens": 160_000}]})
        self.assertIn("context_events", data)
        self.assertEqual(data["context_events"][0]["reclaimed_tokens"], 160_000)

    def test_failed_turns_are_kept_on_a_complete_session(self):
        """They used to live only inside incomplete_reason, so a session that
        ended complete threw away the record of turns that produced nothing."""
        data = self.save([turn("user", "a"), turn("assistant", "b")])
        self.assertIn("failed_turns", data)
        self.assertEqual(data["failed_turns"], [])

    def test_low_content_turns_are_written(self):
        data = self.save(
            [turn("user", "a"), turn("assistant", "b")],
            telemetry={"turn_metrics": {}, "tool_log": [], "failed_turns": [],
                       "low_content_turns": [{"turn": 2, "reply_chars": 8,
                                              "reply": "My Lord."}]})
        self.assertEqual(data["low_content_turns"][0]["reply"], "My Lord.")

    def test_a_heuristic_flag_does_not_make_a_session_incomplete(self):
        """low_content_turns reports, it does not judge — a false positive
        must not be able to mark good work as unfinished."""
        data = self.save(
            [turn("user", "a"), turn("assistant", "b")],
            telemetry={"turn_metrics": {}, "tool_log": [], "failed_turns": [],
                       "low_content_turns": [{"turn": 1, "reply_chars": 5,
                                              "reply": "Done."}]})
        self.assertTrue(data["complete"])

    def test_missing_keys_default_rather_than_raise(self):
        """Old telemetry dicts (and any front end that builds its own) must
        still save."""
        data = self.save([turn("user", "a"), turn("assistant", "b")],
                         telemetry={})
        self.assertEqual(data["context_events"], [])
        self.assertEqual(data["low_content_turns"], [])

    def test_live_telemetry_supplies_every_saved_key(self):
        """Guards the other direction: session_telemetry() must not stop
        producing a key that save_session writes."""
        import agent
        telemetry = agent.session_telemetry()
        for key in ("turn_metrics", "tool_log", "failed_turns",
                    "context_events", "low_content_turns"):
            self.assertIn(key, telemetry)


class TestTelemetry(SessionFileTestCase):

    def test_tool_log_roundtrip(self):
        log = [{"turn": 2, "tool": "write_file", "exit": 0, "duration_sec": 25.1},
               {"turn": 8, "tool": "run_command", "exit": 1, "duration_sec": 957.2,
                "error": "FPDFException: Not enough horizontal space"}]
        data = self.save(
            [turn("user", "a"), turn("assistant", "b")],
            telemetry={"turn_metrics": {"total_duration_sec": 1590.84,
                                        "avg_turn_sec": 132.57,
                                        "turn_timings": [25.1, 114.0]},
                       "tool_log": log, "failed_turns": []})
        self.assertEqual(data["tool_log"], log)
        self.assertEqual(data["turn_metrics"]["total_duration_sec"], 1590.84)

    def test_failing_tool_call_has_nonzero_exit(self):
        agent.reset_session_state()
        agent._record_tool_call(
            "run_command", {"command": "x"},
            "[exit 1 — FAILED (exit 1)]\nboom", duration_ms=1200, ok=True)
        entry = agent.session_telemetry()["tool_log"][-1]
        self.assertEqual(entry["exit"], 1)
        self.assertEqual(entry["duration_sec"], 1.2)
        self.assertIn("boom", entry["error"])

    def test_successful_tool_call_has_zero_exit(self):
        agent.reset_session_state()
        agent._record_tool_call("read_file", {}, "     1\tcontents", 40, True)
        entry = agent.session_telemetry()["tool_log"][-1]
        self.assertEqual(entry["exit"], 0)
        self.assertNotIn("error", entry)

    def test_tool_error_recorded_without_exit_prefix(self):
        agent.reset_session_state()
        agent._record_tool_call("read_file", {}, "Error: file not found: x", 5, False)
        entry = agent.session_telemetry()["tool_log"][-1]
        self.assertEqual(entry["exit"], 1)
        self.assertIn("not found", entry["error"])

    def test_tool_log_does_not_duplicate_payloads(self):
        """Arguments and results are already in `messages`; duplicating them
        is how a 6-turn session file reached 190 KB."""
        agent.reset_session_state()
        agent._record_tool_call("write_file",
                                {"content": "SECRET" * 500}, "ok", 10, True)
        entry = agent.session_telemetry()["tool_log"][-1]
        self.assertNotIn("SECRET", json.dumps(entry))


class TestSessionTokenIsolation(unittest.TestCase):

    def test_token_usage_is_per_session(self):
        """Sessions 122232 and 122837 carried byte-identical usage because
        the counter was a module global that was never reset."""
        agent.reset_session_state()
        agent._session_tokens["input"] += 1_640_061
        agent._session_tokens["calls"] += 80
        first = dict(agent._session_tokens)

        agent.reset_session_state()          # next session begins
        second = dict(agent._session_tokens)

        self.assertNotEqual(first, second)
        # Every counter, not one named list of them: asserting the exact key
        # set made adding a cache-hit counter look like a session-isolation
        # regression, which is the opposite of what this test is guarding.
        self.assertTrue(second, "the counters must still exist after a reset")
        self.assertEqual([k for k, v in second.items() if v != 0], [],
                         f"a counter survived the session boundary: {second}")

    def test_reset_clears_telemetry(self):
        agent.reset_session_state()
        agent._record_tool_call("read_file", {}, "x", 1, True)
        agent._turn_timings.append(12.5)
        self.assertTrue(agent.session_telemetry()["tool_log"])

        agent.reset_session_state()
        telemetry = agent.session_telemetry()
        self.assertEqual(telemetry["tool_log"], [])
        self.assertEqual(telemetry["turn_metrics"]["turn_timings"], [])
        self.assertEqual(telemetry["failed_turns"], [])

    def test_turn_metrics_computed(self):
        agent.reset_session_state()
        agent._turn_timings.extend([10.0, 20.0, 30.0])
        tm = agent.session_telemetry()["turn_metrics"]
        self.assertEqual(tm["total_duration_sec"], 60.0)
        self.assertEqual(tm["avg_turn_sec"], 20.0)


class TestBackfill(SessionFileTestCase):

    def test_backfill_marks_legacy_incomplete_session(self):
        """Pre-Phase-6 files carry no signal at all; the backfill adds one."""
        path = session_manager.get_session_dir() / "_test_legacy_incomplete.json"
        self.saved.append("_test_legacy_incomplete")
        path.write_text(json.dumps({
            "id": "_test_legacy_incomplete", "messages":
                [turn("user", "a"), turn("user", "b")],
        }), encoding="utf-8")

        marked = session_manager.backfill_completeness()
        self.assertIn("_test_legacy_incomplete", marked)
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertFalse(data["complete"])
        self.assertIn("backfilled", data["incomplete_reason"]["note"])

    def test_backfill_leaves_complete_sessions_alone(self):
        path = session_manager.get_session_dir() / "_test_legacy_complete.json"
        self.saved.append("_test_legacy_complete")
        original = json.dumps({
            "id": "_test_legacy_complete",
            "messages": [turn("user", "a"), turn("assistant", "b")],
        })
        path.write_text(original, encoding="utf-8")

        session_manager.backfill_completeness()
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertNotIn("complete", data)

    def test_backfill_is_idempotent(self):
        path = session_manager.get_session_dir() / "_test_legacy_idem.json"
        self.saved.append("_test_legacy_idem")
        path.write_text(json.dumps({
            "id": "_test_legacy_idem", "messages": [turn("user", "a")],
        }), encoding="utf-8")

        first = session_manager.backfill_completeness()
        second = session_manager.backfill_completeness()
        self.assertIn("_test_legacy_idem", first)
        self.assertNotIn("_test_legacy_idem", second)


class TestCorpus(unittest.TestCase):

    def test_no_unmarked_incomplete_sessions_on_disk(self):
        """The corpus must not contain a session that looks finished but is
        not — the condition that let report 3 describe work that never ran."""
        unmarked = []
        for path in sorted(session_manager.get_session_dir().glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            audit = session_manager.audit_transcript(data.get("messages", []))
            if not audit["complete"] and data.get("complete", True):
                unmarked.append(path.name)
        self.assertEqual(unmarked, [], f"unmarked incomplete sessions: {unmarked}")

    def test_existing_sessions_are_readable(self):
        """The flag is additive — older files must still load."""
        sessions = sorted(session_manager.get_session_dir().glob("*.json"))
        if not sessions:
            self.skipTest("no sessions on disk")
        for path in sessions[:5]:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn("messages", data)
            self.assertIsInstance(data.get("complete", True), bool)


if __name__ == "__main__":
    unittest.main(verbosity=2)
