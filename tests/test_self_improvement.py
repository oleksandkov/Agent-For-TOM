#!/usr/bin/env python3
"""
The self-improvement loop: does the agent actually get better?

Three defects this locks down, all found by inspecting a real install rather
than by reading the code:

  * Reflection shipped in `shadow` mode and nobody turned it on. The
    reflection log held ONE entry, `promoted: []`, for the life of the
    install — a learning system that cost a model call per session and had
    never once learned anything.
  * Corrections were detected, handed to reflection, and therefore discarded.
    Being corrected is the strongest signal a session produces.
  * A rule stays in force until removed, so a rule that has silently become
    wrong keeps being obeyed — "always append 2026-08-05" is correct for
    exactly one day, and two contradictory rules both apply with no way for
    the user to see the clash.

Run: python -m unittest discover -s tests -p "test_*.py"
"""
import os
import sys
import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent.resolve()
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import agent
import learning
from learning import promotion, reflect, store
from learning.promotion import PROMOTE_AT, promote_corrections
from learning.store import KIND_DIRECTIVE, dates_in, find_conflicts, stale_dates

from tests.test_directives import StoreTestCase


class TestReflectionIsOn(unittest.TestCase):
    """Shipping it disabled made the safe choice the invisible one."""

    def setUp(self):
        self._saved = os.environ.get("TOMAS_REFLECT")
        os.environ.pop("TOMAS_REFLECT", None)

    def tearDown(self):
        os.environ.pop("TOMAS_REFLECT", None)
        if self._saved is not None:
            os.environ["TOMAS_REFLECT"] = self._saved

    def test_the_default_is_active(self):
        self.assertEqual(reflect.mode(), "active")

    def test_shadow_and_off_still_work(self):
        for value in ("shadow", "off", "ACTIVE", "  shadow  "):
            os.environ["TOMAS_REFLECT"] = value
            self.assertEqual(reflect.mode(), value.strip().lower())

    def test_an_unrecognised_value_falls_back_to_active(self):
        # Failing closed here would silently disable learning again.
        os.environ["TOMAS_REFLECT"] = "banana"
        self.assertEqual(reflect.mode(), "active")


class TestReflectionPromptDemandsDurableFacts(unittest.TestCase):
    """The one live run produced episodes, not preferences."""

    def test_the_durability_test_is_stated(self):
        prompt = reflect.REFLECTION_PROMPT
        self.assertIn("COMPLETELY", prompt)
        self.assertIn("three months from now", prompt)

    def test_the_real_failure_is_used_as_the_bad_example(self):
        # Naming the actual observed failure beats an abstract instruction.
        self.assertIn("internet providers in Ukraine", reflect.REFLECTION_PROMPT)

    def test_every_field_says_what_not_to_write(self):
        prompt = reflect.REFLECTION_PROMPT
        for field in ("user_preferences", "corrections", "skill_candidates",
                      "project_notes"):
            self.assertIn(field, prompt)
        self.assertIn("BAD", prompt)
        self.assertIn("GOOD", prompt)


class TestCorrectionsBecomeRules(StoreTestCase):

    RULE_CORRECTION = [
        {"role": "user", "content": "no, I said always use type annotations in python"},
        {"role": "assistant", "content": "ok"},
    ]

    def test_a_repeated_correction_becomes_a_standing_rule(self):
        seen = []
        for _ in range(PROMOTE_AT):
            promote_corrections(self.RULE_CORRECTION)
            seen.append(store.load_facts("global")[0]["status"])
        self.assertEqual(seen, ["observed", "candidate", "active"])
        self.assertIn("type annotations", learning.directives_for_prompt())

    def test_one_correction_is_not_enough(self):
        # An inference drawn from a single annoyed message is exactly what the
        # gate exists to hold back.
        promote_corrections(self.RULE_CORRECTION)
        self.assertEqual(learning.directives_for_prompt(), "")

    def test_a_correction_that_states_no_rule_is_ignored(self):
        # "no, not like that" says something went wrong, not what to do.
        vague = [{"role": "user", "content": "no, not like that"},
                 {"role": "assistant", "content": "ok"}]
        for _ in range(PROMOTE_AT + 2):
            promote_corrections(vague)
        self.assertEqual(learning.directives_for_prompt(), "")

    def test_an_ordinary_message_is_not_a_correction(self):
        calm = [{"role": "user", "content": "please always use type hints"},
                {"role": "assistant", "content": "ok"}]
        for _ in range(PROMOTE_AT + 2):
            promote_corrections(calm)
        self.assertEqual(learning.directives_for_prompt(), "",
                         "a request that was never a correction was promoted "
                         "through the correction path")

    def test_the_same_correction_twice_in_one_session_counts_once(self):
        # Otherwise a user repeating themselves inside a single session would
        # jump the gate in one go.
        doubled = self.RULE_CORRECTION + self.RULE_CORRECTION
        promote_corrections(doubled)
        self.assertEqual(store.load_facts("global")[0]["evidence_count"], 1)

    def test_it_never_raises_on_junk(self):
        for junk in ([], [{}], [{"role": "user"}], [{"role": "user", "content": None}]):
            self.assertEqual(promote_corrections(junk), [])


class TestStaleness(unittest.TestCase):

    def test_dates_are_found_in_both_formats(self):
        self.assertEqual(dates_in("append 2026-08-05 and 31.12.2026"),
                         ["2026-08-05", "2026-12-31"])

    def test_a_rule_naming_today_is_not_stale(self):
        self.assertEqual(stale_dates("append 2026-08-05", today="2026-08-05"), [])

    def test_a_rule_naming_another_day_is_stale(self):
        self.assertEqual(stale_dates("append 2026-08-05", today="2026-09-01"),
                         ["2026-08-05"])

    def test_a_rule_with_no_date_is_never_stale(self):
        self.assertEqual(stale_dates("always use type hints", today="2026-09-01"), [])


class TestStalenessReachesTheModel(StoreTestCase):

    def test_the_prompt_annotates_an_expired_rule(self):
        import time as time_mod
        learning.remember(KIND_DIRECTIVE, "Always append the date 2026-08-05.")
        real = time_mod.strftime
        time_mod.strftime = lambda f, *a: ("2026-09-01" if f == "%Y-%m-%d"
                                           else real(f, *a))
        try:
            rendered = learning.directives_for_prompt()
        finally:
            time_mod.strftime = real
        # Annotated, never rewritten: "append 2026-08-05" might genuinely mean
        # that date, and only the user knows which.
        self.assertIn("2026-08-05", rendered)
        self.assertIn("today is 2026-09-01", rendered)


class TestConflicts(unittest.TestCase):

    def _d(self, fid, text):
        return {"id": fid, "fact": text}

    def test_always_versus_never_on_the_same_subject_is_flagged(self):
        pairs = find_conflicts([
            self._d("a", "Always use tabs for indentation in python code."),
            self._d("b", "Never use tabs for indentation in python code."),
        ])
        self.assertEqual(pairs, [("a", "b")])

    def test_unrelated_rules_do_not_conflict(self):
        self.assertEqual(find_conflicts([
            self._d("a", "Always append the current date to every reply."),
            self._d("b", "Never delete a file without asking first."),
        ]), [])

    def test_two_positive_rules_do_not_conflict(self):
        self.assertEqual(find_conflicts([
            self._d("a", "Always use tabs for indentation in python code."),
            self._d("b", "Always use tabs for indentation in python files."),
        ]), [])

    def test_ukrainian_polarity_is_understood(self):
        pairs = find_conflicts([
            self._d("a", "Завжди використовуй табуляцію для відступів у коді."),
            self._d("b", "Ніколи не використовуй табуляцію для відступів у коді."),
        ])
        self.assertEqual(pairs, [("a", "b")])

    def test_empty_and_single_inputs_are_safe(self):
        self.assertEqual(find_conflicts([]), [])
        self.assertEqual(find_conflicts([self._d("a", "Always do X.")]), [])


class TestRulesCommand(StoreTestCase):

    def test_it_reports_when_there_is_nothing(self):
        # The empty state is also the discovery surface: it is where someone
        # who typed /rules to find out what rules are learns how to set one.
        out = agent.handle_slash_command("rules", [])
        self.assertIn("No rules yet", out)
        self.assertIn("/rules add", out)

    def test_it_lists_rules_with_ids(self):
        record = learning.remember(KIND_DIRECTIVE, "Always reply in Ukrainian.")
        out = agent.handle_slash_command("rules", [])
        self.assertIn("Always reply in Ukrainian", out)
        self.assertIn(record["id"], out)

    def test_it_warns_about_a_conflict(self):
        learning.remember(KIND_DIRECTIVE, "Always use tabs for indentation in python.")
        learning.remember(KIND_DIRECTIVE, "Never use tabs for indentation in python.")
        self.assertIn("conflicts", agent.handle_slash_command("rules", []))

    def test_forget_removes_the_rule(self):
        record = learning.remember(KIND_DIRECTIVE, "Always shout every reply.")
        out = agent.handle_slash_command(f"rules forget {record['id']}", [])
        self.assertIn("Rule removed", out)
        self.assertEqual(learning.directives_for_prompt(), "")

    def test_a_forgotten_rule_is_not_inferred_again(self):
        # The tombstone binds reflection, which is where a re-learned rule
        # would otherwise reappear without the user asking for it.
        record = learning.remember(KIND_DIRECTIVE, "Always shout every reply.")
        agent.handle_slash_command(f"rules forget {record['id']}", [])
        for _ in range(PROMOTE_AT + 2):
            promotion.record_observation(
                KIND_DIRECTIVE, "Always shout every reply.", "inferred", "global")
        self.assertEqual(learning.directives_for_prompt(), "")

    def test_stating_a_forgotten_rule_again_restores_it(self):
        # Deliberately NOT blocked: one /forget must not permanently blacklist
        # a rule the user later changes their mind about.
        record = learning.remember(KIND_DIRECTIVE, "Always shout every reply.")
        agent.handle_slash_command(f"rules forget {record['id']}", [])
        learning.remember(KIND_DIRECTIVE, "Always shout every reply.")
        self.assertIn("shout", learning.directives_for_prompt())

    def test_forget_with_an_unknown_id_says_so(self):
        out = agent.handle_slash_command("rules forget deadbeef", [])
        self.assertIn("No rule", out)
        self.assertIn("deadbeef", out)

    def test_forget_without_an_id_shows_usage(self):
        self.assertIn("Usage", agent.handle_slash_command("rules forget", []))

    def test_the_command_is_registered(self):
        # Otherwise _intercept_slash_command falls through to the model.
        self.assertIn("rules", agent.SLASH_COMMANDS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
