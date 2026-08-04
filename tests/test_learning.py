#!/usr/bin/env python3
"""
Phase 3 — real learning.

These are the Phase 3 acceptance criteria written as tests
(see docs/HISTORY.md). The two that matter most:

  * a preference must be *reinforced* before it becomes a belief, and
  * the prompt must stay flat in size as the store grows.

The first is what stops the store filling with hallucinated facts; the second
is what makes a memory system viable over years rather than weeks.

Run: python -m unittest discover -s tests -p "test_*.py"
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent.resolve()
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import learning
from learning import promotion, reflect, retrieval, store
from learning.corrections import detect_correction_signals
from learning.promotion import PROMOTE_AT, record_observation, remember
from learning.retrieval import recall


class LearningTestCase(unittest.TestCase):
    """Point the whole store at a scratch directory for each test."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self._saved = {
            k: getattr(store, k) for k in
            ("LEARNED_DIR", "GLOBAL_DIR", "PROJECTS_DIR", "SKILLS_DIR",
             "REFLECTION_LOG", "TOMBSTONES_PATH", "LEGACY_MEMORY_DIR",
             "LEGACY_NOTES_DIR", "_MIGRATION_MARKER")
        }
        store.LEARNED_DIR = root / "learned"
        store.GLOBAL_DIR = store.LEARNED_DIR / "global"
        store.PROJECTS_DIR = store.LEARNED_DIR / "projects"
        store.SKILLS_DIR = store.GLOBAL_DIR / "skills"
        store.REFLECTION_LOG = store.LEARNED_DIR / "reflection-log.jsonl"
        store.TOMBSTONES_PATH = store.LEARNED_DIR / "tombstones.json"
        store.LEGACY_MEMORY_DIR = root / "memory"
        store.LEGACY_NOTES_DIR = root / "self-notes"
        store._MIGRATION_MARKER = store.LEARNED_DIR / ".migrated"
        reflect.SKILLS_DIR = store.SKILLS_DIR
        reflect.REFLECTION_LOG = store.REFLECTION_LOG
        store.set_project(root / "project-a")

    def tearDown(self):
        for k, v in self._saved.items():
            setattr(store, k, v)
        reflect.SKILLS_DIR = store.SKILLS_DIR
        reflect.REFLECTION_LOG = store.REFLECTION_LOG
        store.set_project(None)
        self._tmp.cleanup()


class TestPromotionGate(LearningTestCase):
    """Never let one session write a permanent rule."""

    def test_fact_requires_evidence_before_going_active(self):
        for i in range(PROMOTE_AT - 1):
            record_observation("preference", "prefers PowerShell",
                               f"session {i}", "global")
            self.assertNotIn("PowerShell", recall("how do I list files"),
                             f"went active after only {i + 1} sighting(s)")

        record_observation("preference", "prefers PowerShell", "session 3", "global")
        self.assertIn("PowerShell", recall("how do I list files"))

    def test_status_progresses_observed_candidate_active(self):
        seen = []
        for _ in range(PROMOTE_AT):
            record, _ = record_observation("preference", "writes Ukrainian", "e", "global")
            seen.append(record["status"])
        self.assertEqual(seen, ["observed", "candidate", "active"])

    def test_promotion_is_reported_once(self):
        promotions = []
        for _ in range(PROMOTE_AT + 2):
            _, promoted = record_observation("preference", "likes tabs", "e", "global")
            promotions.append(promoted)
        self.assertEqual(promotions.count(True), 1,
                         "promotion must be announced on the transition only")

    def test_rephrased_fact_reinforces_rather_than_duplicating(self):
        record_observation("preference", "the user prefers PowerShell", "a", "global")
        record_observation("preference", "user prefers PowerShell shell", "b", "global")
        facts = store.load_facts("global")
        self.assertEqual(len(facts), 1, "near-identical facts must merge")
        self.assertEqual(facts[0]["evidence_count"], 2)

    def test_explicit_user_instruction_is_active_immediately(self):
        """The user said it outright — there is no inference to gate."""
        remember("explicit", "always use PowerShell, never bash",
                 evidence="user said so", scope="global")
        self.assertIn("PowerShell", recall("run a command"))


class TestPromptStaysFlat(LearningTestCase):
    """The property that makes this viable long-term."""

    def test_prompt_size_is_flat_in_stored_knowledge(self):
        import agent

        base = len(agent.build_system_prompt("list files"))
        # Each fact needs its own vocabulary: near-identical facts are merged
        # by find_similar (see test_rephrased_fact_reinforces_rather_than_
        # duplicating), which would make this pass for the wrong reason.
        for i in range(500):
            remember("explicit",
                     f"topic{i} handling needs approach{i} inside module{i}",
                     "synthetic", "global")
        self.assertEqual(len(store.load_facts("global")), 500)
        grown = len(agent.build_system_prompt("list files"))
        self.assertLess(grown, base * 1.5,
                        f"prompt grew from {base} to {grown} with 500 facts")

    def test_recall_returns_at_most_k(self):
        for i in range(50):
            remember("explicit", f"fact {i} about python testing", "e", "global")
        self.assertLessEqual(len(recall("python testing", k=5).splitlines()), 5)


class TestProjectScoping(LearningTestCase):
    """Facts about one codebase must not pollute another's prompt."""

    def test_project_facts_do_not_leak_across_projects(self):
        record_observation("project", "tests live in tests/", "e", "project")
        record_observation("project", "tests live in tests/", "e", "project")
        record_observation("project", "tests live in tests/", "e", "project")
        self.assertIn("tests live in", recall("where are the tests"))

        with store.use_project(Path(self._tmp.name) / "other-project"):
            self.assertNotIn("tests live in", recall("where are the tests"))

    def test_global_facts_are_visible_from_every_project(self):
        remember("explicit", "answer in Ukrainian", scope="global")
        with store.use_project(Path(self._tmp.name) / "other-project"):
            self.assertIn("Ukrainian", recall("anything"))


class TestCorrectionDetection(LearningTestCase):
    def test_correction_is_detected(self):
        msgs = [{"role": "user", "content": "use bash"},
                {"role": "assistant", "content": "..."},
                {"role": "user", "content": "no, I meant PowerShell"}]
        kinds = [s["kind"] for s in detect_correction_signals(msgs)]
        self.assertIn("explicit_correction", kinds)

    def test_repeated_request_is_detected(self):
        msgs = [{"role": "user", "content": "please generate the pdf report now"},
                {"role": "assistant", "content": "..."},
                {"role": "user", "content": "generate the pdf report please"}]
        kinds = [s["kind"] for s in detect_correction_signals(msgs)]
        self.assertIn("repeated_request", kinds)

    def test_permission_denial_is_a_signal(self):
        msgs = [
            {"role": "user", "content": "clean up"},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "run_command",
                 "input": {"command": "rm -rf /"}}]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1",
                 "content": "Error: user denied this tool call."}]},
        ]
        signals = detect_correction_signals(msgs)
        self.assertIn("permission_denied", [s["kind"] for s in signals])
        self.assertEqual(signals[0]["tool"], "run_command")

    def test_tool_error_loop_is_a_signal(self):
        def call(i):
            return [
                {"role": "assistant", "content": [
                    {"type": "tool_use", "id": f"t{i}", "name": "run_command",
                     "input": {"command": "python verify.py"}}]},
                {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": f"t{i}",
                     "content": "Error: no such file"}]},
            ]
        msgs = [{"role": "user", "content": "verify"}] + call(1) + call(2)
        self.assertIn("tool_error_loop",
                      [s["kind"] for s in detect_correction_signals(msgs)])

    def test_ordinary_conversation_produces_no_signals(self):
        msgs = [{"role": "user", "content": "please add a docstring to parse_config"},
                {"role": "assistant", "content": "Done."},
                {"role": "user", "content": "great, now run the tests"}]
        self.assertEqual(detect_correction_signals(msgs), [])


class TestReflection(LearningTestCase):
    def test_reflection_returns_empty_for_trivial_session(self):
        """The model must be allowed to learn nothing — this is what stops
        the store filling with hallucinated preferences."""
        msgs = [{"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"}]
        self.assertEqual(reflect.reflect_on_session(msgs, call_model=lambda **k: "{}"), {})

    def test_reflection_returns_empty_without_a_model(self):
        msgs = [{"role": "user", "content": "x"}] * 6
        self.assertEqual(reflect.reflect_on_session(msgs), {})

    def test_model_failure_never_propagates(self):
        def boom(**kwargs):
            raise RuntimeError("provider down")

        msgs = [{"role": "user", "content": "do a thing"}] * 6
        self.assertEqual(reflect.reflect_on_session(msgs, call_model=boom), {})

    def test_json_is_extracted_from_a_fenced_reply(self):
        msgs = [{"role": "user", "content": "use powershell"}] * 6
        reply = '```json\n{"user_preferences": [{"fact": "uses PowerShell", ' \
                '"confidence": 0.9, "evidence": "said so"}]}\n```'
        result = reflect.reflect_on_session(msgs, call_model=lambda **k: reply)
        self.assertEqual(result["user_preferences"][0]["fact"], "uses PowerShell")

    def test_shadow_mode_logs_but_writes_nothing(self):
        import os
        os.environ["TOMAS_REFLECT"] = "shadow"
        try:
            msgs = [{"role": "user", "content": "always use PowerShell"}] * 6
            reply = json.dumps({"user_preferences": [
                {"fact": "uses PowerShell", "confidence": 0.9, "evidence": "said so"}]})
            outcome = reflect.run_session_reflection(msgs, call_model=lambda **k: reply)

            self.assertEqual(outcome["mode"], "shadow")
            self.assertEqual(store.load_facts("global"), [],
                             "shadow mode must not write to the store")
            self.assertTrue(store.REFLECTION_LOG.exists(),
                            "shadow mode must still log what it would learn")
        finally:
            os.environ.pop("TOMAS_REFLECT", None)

    def test_active_mode_records_observations(self):
        import os
        os.environ["TOMAS_REFLECT"] = "active"
        try:
            msgs = [{"role": "user", "content": "always use PowerShell"}] * 6
            reply = json.dumps({"user_preferences": [
                {"fact": "uses PowerShell", "confidence": 0.9, "evidence": "said so"}]})
            reflect.run_session_reflection(msgs, call_model=lambda **k: reply)

            facts = store.load_facts("global")
            self.assertEqual(len(facts), 1)
            self.assertEqual(facts[0]["status"], "observed",
                             "one session is still only one piece of evidence")
        finally:
            os.environ.pop("TOMAS_REFLECT", None)

    def test_low_confidence_findings_are_dropped(self):
        reflect.apply_reflection({"user_preferences": [
            {"fact": "maybe likes vim", "confidence": 0.2, "evidence": "unclear"}]})
        self.assertEqual(store.load_facts("global"), [])

    def test_reflection_uses_a_cheap_model(self):
        self.assertEqual(reflect.cheapest_available_model("claude-opus-5"),
                         "claude-haiku-4-5")
        self.assertEqual(reflect.cheapest_available_model("gemini-3.1-pro"),
                         "gemini-3.5-flash-lite")
        self.assertEqual(reflect.cheapest_available_model("some-local-model"),
                         "some-local-model", "unknown family reuses the session model")

    def test_skill_is_only_written_once_the_candidate_recurs(self):
        candidate = {"skill_candidates": [
            {"name": "pdf-report", "trigger": "when asked for a PDF report",
             "body": "Use fpdf2 and write to latest_ai_news_report.pdf"}]}
        for _ in range(PROMOTE_AT - 1):
            reflect.apply_reflection(candidate)
            self.assertFalse((store.SKILLS_DIR / "pdf-report.md").exists())

        reflect.apply_reflection(candidate)
        written = store.SKILLS_DIR / "pdf-report.md"
        self.assertTrue(written.exists(), "a recurring candidate becomes a skill")
        self.assertIn("fpdf2", written.read_text(encoding="utf-8"))


class TestPrivacy(LearningTestCase):
    def test_secrets_are_not_persisted(self):
        remember("explicit", "my key is sk-ant-api03-AAAABBBBCCCCDDDDEEEEFFFF",
                 scope="global")
        written = store.facts_path("global").read_text(encoding="utf-8")
        self.assertNotIn("sk-ant-api03", written)
        self.assertIn("[redacted]", written)

    def test_secrets_are_stripped_from_evidence_too(self):
        record_observation("preference", "uses GitHub",
                           evidence="token ghp_AAAABBBBCCCCDDDDEEEEFFFFGGGG",
                           scope="global")
        written = store.facts_path("global").read_text(encoding="utf-8")
        self.assertNotIn("ghp_AAAA", written)

    def test_transcripts_are_redacted_before_reflection(self):
        transcript = reflect.render_transcript(
            [{"role": "user", "content": "here is my key sk-proj-ABCDEFGHIJKLMNOP"}])
        self.assertNotIn("sk-proj-ABCDEFGHIJKLMNOP", transcript)


class TestForgetting(LearningTestCase):
    def test_forget_removes_and_tombstones(self):
        record = remember("explicit", "prefers dark mode", scope="global")
        self.assertIn("dark mode", recall("theme"))

        removed = store.forget(record["id"])
        self.assertIsNotNone(removed)
        self.assertNotIn("dark mode", recall("theme"))

    def test_forgotten_facts_are_not_re_learned(self):
        record = remember("explicit", "prefers dark mode", scope="global")
        store.forget(record["id"])

        for _ in range(PROMOTE_AT + 1):
            record_observation("preference", "prefers dark mode", "e", "global")
        self.assertEqual(store.load_facts("global"), [],
                         "a tombstoned fact must stay forgotten")

    def test_forgetting_an_unknown_id_is_harmless(self):
        self.assertIsNone(store.forget("nope"))


class TestDecay(LearningTestCase):
    def test_unreinforced_facts_age_out(self):
        import time
        record_observation("preference", "transient idea", "e", "global")
        facts = store.load_facts("global")
        facts[0]["last_seen"] = time.time() - (promotion.DECAY_DAYS + 1) * 86400
        store.save_facts("global", facts)

        self.assertEqual(promotion.decay("global"), 1)
        self.assertEqual(store.load_facts("global"), [])

    def test_well_established_facts_are_kept_forever(self):
        import time
        remember("explicit", "always use PowerShell", scope="global")
        facts = store.load_facts("global")
        facts[0]["evidence_count"] = PROMOTE_AT * 2
        facts[0]["last_seen"] = time.time() - 3650 * 86400
        store.save_facts("global", facts)

        self.assertEqual(promotion.decay("global"), 0)


class TestMigration(LearningTestCase):
    def test_existing_memories_become_active_facts(self):
        store.LEGACY_MEMORY_DIR.mkdir(parents=True)
        (store.LEGACY_MEMORY_DIR / "shell.md").write_text(
            "---\nname: shell\ndescription: shell preference\n---\n\n"
            "The user always uses PowerShell on Windows.\n", encoding="utf-8")
        (store.LEGACY_MEMORY_DIR / "MEMORY.md").write_text(
            "- [shell](shell.md) - shell preference\n", encoding="utf-8")

        self.assertEqual(store.migrate_legacy_stores(), 1)
        facts = store.load_facts("global")
        self.assertEqual(facts[0]["status"], "active")
        self.assertIn("PowerShell", recall("which shell"))

    def test_migration_runs_only_once(self):
        store.LEGACY_MEMORY_DIR.mkdir(parents=True)
        (store.LEGACY_MEMORY_DIR / "a.md").write_text("fact one", encoding="utf-8")
        self.assertEqual(store.migrate_legacy_stores(), 1)
        self.assertEqual(store.migrate_legacy_stores(), 0)
        self.assertEqual(len(store.load_facts("global")), 1)

    def test_template_skills_and_tips_are_not_imported(self):
        """They contain no information derived from any real interaction."""
        store.LEGACY_MEMORY_DIR.mkdir(parents=True)
        self.assertEqual(store.migrate_legacy_stores(), 0)
        self.assertEqual(store.load_facts("global"), [])


class TestNeverRaises(LearningTestCase):
    """Nothing in the learning path may raise into the user's turn."""

    def test_recall_survives_a_corrupt_store(self):
        store.facts_path("global").parent.mkdir(parents=True, exist_ok=True)
        store.facts_path("global").write_text("{not json at all\n", encoding="utf-8")
        self.assertEqual(recall("anything"), "")

    def test_remember_with_empty_fact_is_a_no_op(self):
        self.assertIsNone(remember("explicit", "   ", scope="global"))
        self.assertEqual(store.load_facts("global"), [])

    def test_incognito_blocks_learning(self):
        learning.set_enabled(False)
        try:
            self.assertFalse(learning.is_enabled())
        finally:
            learning.set_enabled(True)


if __name__ == "__main__":
    unittest.main()
