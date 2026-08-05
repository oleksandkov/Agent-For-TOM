#!/usr/bin/env python3
"""
Standing rules — the instruction-following fix.

These lock in the finding from SIMULATION_ANALYSIS_REVIEW.md. One 30-turn
session ran two standing rules at once:

  * "end every report with My Lord", from ~/.tomas/instructions/AGENT.md —
    static text under an imperative heading — was obeyed 29/29.
  * "always append the date", from the fact store — retrieved, under the
    heading "What I've learned about this user and project" — was obeyed 0/29,
    while being present in the prompt on all 29 of those turns.

The rule was never the problem. The channel was. A directive is unconditional,
so scoring it for relevance against the current message is a category error:
the message it is relevant to is every message. These tests assert that
directives bypass retrieval, bypass the evidence gate, and land in the
imperative section of the prompt.

Run: python -m unittest discover -s tests -p "test_*.py"
"""
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent.resolve()
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import learning
from learning import promotion, reflect, retrieval, store
from learning.promotion import PROMOTE_AT, record_observation, remember
from learning.retrieval import directives_for_prompt, recall
from learning.store import KIND_DIRECTIVE, KIND_EXPLICIT


class StoreTestCase(unittest.TestCase):
    """Point the whole store at a scratch directory for each test."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self._saved = {
            k: getattr(store, k) for k in
            ("LEARNED_DIR", "GLOBAL_DIR", "PROJECTS_DIR", "SKILLS_DIR",
             "REFLECTION_LOG", "TOMBSTONES_PATH", "LEGACY_MEMORY_DIR",
             "LEGACY_NOTES_DIR", "_MIGRATION_MARKER", "_REPAIR_MARKER")
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
        store._REPAIR_MARKER = store.LEARNED_DIR / ".repaired-frontmatter"
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


class TestClassification(unittest.TestCase):
    """An unconditional rule and a conditional preference are different things."""

    def test_unconditional_phrasing_is_a_directive(self):
        for text in ("Always append the current date to responses.",
                     "Never use tabs.",
                     "From now on, reply in Ukrainian.",
                     "Each report must be ended with My Lord.",
                     "Every response should open with a summary."):
            self.assertTrue(store.looks_like_directive(text), text)

    def test_conditional_preference_is_not_a_directive(self):
        for text in ("The user prefers PowerShell over bash.",
                     "Tests live in tests/.",
                     "The user is working on a Django project.",
                     "prefers short answers"):
            self.assertFalse(store.looks_like_directive(text), text)

    def test_ukrainian_and_russian_phrasing_is_detected(self):
        # The agent's own AGENT.md defaults to Ukrainian, so a rule the user
        # sets will very often not be in English.
        for text in ("Завжди відповідай українською.",
                     "Ніколи не видаляй файли без запиту.",
                     "Всегда используй PEP 484.",
                     "Никогда не пиши без типов."):
            self.assertTrue(store.looks_like_directive(text), text)


class TestDirectivesBypassRetrieval(StoreTestCase):
    """A rule that applies to every turn cannot be selected by relevance."""

    def test_directive_is_returned_for_a_totally_unrelated_query(self):
        remember(KIND_DIRECTIVE, "Always append the date 2026-08-05.")
        # Zero keyword overlap — this is the exact query shape that scored the
        # date rule below MIN_SCORE for 29 consecutive turns.
        rendered = directives_for_prompt()
        self.assertIn("2026-08-05", rendered)

    def test_directive_never_competes_for_the_retrieval_budget(self):
        remember(KIND_DIRECTIVE, "Always append the date 2026-08-05.")
        remember(KIND_EXPLICIT, "The user prefers PowerShell over bash.")
        recalled = recall("which shell should I use")
        self.assertIn("PowerShell", recalled)
        self.assertNotIn("2026-08-05", recalled,
                         "directive leaked into recall and spent top-k budget")

    def test_directives_survive_a_store_full_of_other_facts(self):
        remember(KIND_DIRECTIVE, "Always sign off with My Lord.")
        for i in range(50):
            remember(KIND_EXPLICIT, f"Unrelated project fact number {i}.")
        self.assertIn("My Lord", directives_for_prompt(),
                      "a rule fell off the end as the store grew — the exact "
                      "failure retrieval was introduced to prevent, reappearing")


class TestDirectivesSkipTheEvidenceGate(StoreTestCase):
    """The gate is for inferences. The user typing a rule is not an inference."""

    def test_a_directive_is_active_on_first_write(self):
        remember(KIND_DIRECTIVE, "Always reply in Ukrainian.")
        self.assertIn("Ukrainian", directives_for_prompt())

    def test_an_inferred_note_still_has_to_earn_its_place(self):
        for i in range(PROMOTE_AT - 1):
            record_observation("note", "Always seems to want short answers",
                               f"session {i}", "global")
        self.assertEqual(directives_for_prompt(), "")
        self.assertNotIn("short answers", recall("answer length"))

    def test_restating_a_preference_as_a_rule_promotes_it(self):
        remember(KIND_EXPLICIT, "Use PEP 484 type annotations.")
        self.assertEqual(directives_for_prompt(), "")
        remember(KIND_DIRECTIVE, "Use PEP 484 type annotations.")
        self.assertIn("PEP 484", directives_for_prompt())

    def test_a_tombstoned_directive_is_not_re_learned(self):
        remember(KIND_DIRECTIVE, "Always shout.")
        fact_id = [f for f in store.load_facts("global")][0]["id"]
        store.forget(fact_id)
        self.assertEqual(directives_for_prompt(), "")


class TestDirectiveBudget(StoreTestCase):
    """Always-on costs budget on every turn, so the cap has to be real."""

    # Deliberately unalike: `find_similar` merges anything above 0.75
    # similarity, so "thing number 1" / "thing number 2" would collapse into a
    # single reinforced fact and every budget assertion would pass vacuously.
    RULES = [
        "Always append the current date to every reply.",
        "Never use tab characters for indentation.",
        "Always write commit messages in the imperative mood.",
        "Never delete a file without asking permission first.",
        "Always include type annotations in Python code.",
        "Every response must open with a one-line summary.",
        "Never abbreviate variable names in generated code.",
        "Always cite the file and line number when quoting code.",
        "Never answer in English when the user writes Ukrainian.",
        "Always run the test suite before reporting a fix as done.",
        "Never introduce a new dependency without flagging it.",
        "Always prefer editing an existing file over creating one.",
        "Every SQL query must be parameterised, never interpolated.",
    ]

    def _store(self, count):
        for text in self.RULES[:count]:
            remember(KIND_DIRECTIVE, text)

    def test_fixtures_really_are_distinct(self):
        # Guards the guard: if dedup ever swallows these, the budget tests
        # below stop testing anything and would silently pass forever.
        self._store(len(self.RULES))
        stored = [f for f in store.load_facts("global")
                  if f["kind"] == KIND_DIRECTIVE]
        self.assertEqual(len(stored), len(self.RULES))

    def test_the_number_of_directives_is_capped(self):
        self._store(len(self.RULES))
        self.assertLessEqual(len(store.load_directives()), store.MAX_DIRECTIVES)

    def test_going_over_budget_is_reported_not_silent(self):
        self._store(len(self.RULES))
        self.assertIn("not shown", directives_for_prompt(),
                      "rules were dropped without telling anyone — silently "
                      "losing a rule the user set is the whole bug class")

    def test_newest_rules_win_the_budget(self):
        self._store(store.MAX_DIRECTIVES)
        remember(KIND_DIRECTIVE, "Always greet the user by name on turn one.")
        self.assertIn("greet the user by name", directives_for_prompt())

    def test_under_budget_shows_no_notice(self):
        self._store(3)
        rendered = directives_for_prompt()
        self.assertNotIn("not shown", rendered)
        self.assertEqual(rendered.count("\n"), 2, rendered)


class TestFrontmatterRepair(StoreTestCase):
    """The /note bridge used to bake a note's YAML frontmatter into the fact."""

    POLLUTED = ("response_formatting: ---\nid: note-20260805_082001-baf2e9\n"
                "created_at: 1785907201.6\nupdated_at: 1785907201.6\n"
                "type: insight\ntags: []\nsource_session: None\n"
                "auto_generated: false\n---\n\n"
                "Always end every response with the current date: 2026-08-05")

    def _write(self, fact_text, kind="note", status=store.STATUS_OBSERVED):
        record = store.new_fact(kind, fact_text, "evidence", status=status)
        store.save_facts("global", store.load_facts("global") + [record])

    def test_repair_strips_the_frontmatter(self):
        self._write(self.POLLUTED)
        store.repair_frontmatter_facts()
        fact = store.load_facts("global")[0]
        self.assertNotIn("auto_generated", fact["fact"])
        self.assertIn("Always end every response", fact["fact"])

    def test_repair_recovers_the_keywords(self):
        self._write(self.POLLUTED)
        store.repair_frontmatter_facts()
        keywords = store.load_facts("global")[0]["keywords"]
        # Before: every slot was created_at / updated_at / auto_generated / the
        # note id, so the fact could not be retrieved by its own subject.
        self.assertIn("date", keywords)
        self.assertNotIn("auto_generated", keywords)
        self.assertNotIn("created_at", keywords)

    def test_repair_reclassifies_a_recovered_rule_as_a_directive(self):
        self._write(self.POLLUTED)
        store.repair_frontmatter_facts()
        self.assertIn("2026-08-05", directives_for_prompt())

    def test_repair_promotes_pre_existing_rules_stored_as_explicit(self):
        # These predate the directive kind. They are precisely the rules that
        # were being injected as background trivia and ignored.
        self._write("Rule to append date: Always append current date 2026-08-05.",
                    kind=KIND_EXPLICIT, status=store.STATUS_ACTIVE)
        report = store.repair_frontmatter_facts()
        self.assertEqual(report["reclassified"], 1)
        self.assertIn("2026-08-05", directives_for_prompt())

    def test_repair_leaves_ordinary_facts_alone(self):
        self._write("The user prefers PowerShell.", kind=KIND_EXPLICIT,
                    status=store.STATUS_ACTIVE)
        store.repair_frontmatter_facts()
        fact = store.load_facts("global")[0]
        self.assertEqual(fact["kind"], KIND_EXPLICIT)
        self.assertEqual(fact["fact"], "The user prefers PowerShell.")

    def test_repair_runs_once(self):
        self._write(self.POLLUTED)
        first = store.repair_frontmatter_facts()
        second = store.repair_frontmatter_facts()
        self.assertEqual(first["repaired"], 1)
        self.assertEqual(second["repaired"], 0)


class TestHarnessProbePurge(StoreTestCase):
    """A test that leaves rows in the real store spends the user's budget."""

    def test_tagged_rows_are_removed_and_others_kept(self):
        remember(KIND_EXPLICIT, "Harness probe: written by tests/simulate.py",
                 evidence=f"{store.HARNESS_EVIDENCE_TAG} self-note note-1")
        remember(KIND_EXPLICIT, "The user prefers PowerShell.",
                 evidence="user said so")
        removed = store.purge_harness_probes()
        self.assertEqual(removed, 1)
        remaining = [f["fact"] for f in store.load_facts("global")]
        self.assertEqual(remaining, ["The user prefers PowerShell."])


if __name__ == "__main__":
    unittest.main(verbosity=2)
