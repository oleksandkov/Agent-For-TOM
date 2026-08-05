#!/usr/bin/env python3
"""
The three delivery paths a standing rule has to survive.

Storing a rule correctly (tests/test_directives.py) is only half of it. It then
has to reach the model in a form that gets complied with, keep reaching it as
the transcript grows, and not be swallowed on the way in. This file covers:

  * the system prompt — directives in the imperative section, not the dossier,
  * the conversation — periodic in-context reinforcement, because compliance in
    the real transcripts was bimodal *per session*: whatever the model did on
    turn 1 it kept doing for 29 more, and by turn 20 the transcript is a louder
    signal than the system prompt,
  * the note bridge — what the user typed, not the YAML wrapper around it,
  * slash commands sent as messages, which were being forwarded to the model as
    prose and then hallucinated or reimplemented via the shell.

Run: python -m unittest discover -s tests -p "test_*.py"
"""
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent.resolve()
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import agent
import learning
import self_notes
from learning import reflect, store
from learning.promotion import remember
from learning.store import KIND_DIRECTIVE, KIND_EXPLICIT

from tests.test_directives import StoreTestCase


class TestSystemPromptChannel(StoreTestCase):
    """0/29 vs 29/29 came down to which heading the rule sat under."""

    def test_a_directive_reaches_the_imperative_section(self):
        remember(KIND_DIRECTIVE, "Always append the date 2026-08-05.")
        # A query with no keyword overlap at all — the shape that scored below
        # MIN_SCORE on every coding turn of the failing sessions.
        prompt = agent.build_system_prompt("Search for 'def redact' in store.py")
        self.assertIn("Standing rules from the user", prompt)
        self.assertIn("2026-08-05", prompt)

    def test_the_directive_block_is_phrased_as_an_instruction(self):
        remember(KIND_DIRECTIVE, "Always append the date 2026-08-05.")
        prompt = agent.build_system_prompt("anything")
        block = prompt[prompt.index("# Standing rules"):]
        # "What I've learned about this user" read as a dossier and the model
        # treated it as trivia. The wording here is load-bearing.
        self.assertIn("EVERY reply", block)
        self.assertIn("MUST", block)

    def test_retrieved_facts_no_longer_sit_under_a_dossier_heading(self):
        remember(KIND_EXPLICIT, "The user prefers PowerShell over bash.")
        prompt = agent.build_system_prompt("which shell do I use")
        self.assertNotIn("What I've learned about this user", prompt)
        self.assertIn("PowerShell", prompt)

    def test_directives_come_before_retrieved_context(self):
        remember(KIND_DIRECTIVE, "Always append the date 2026-08-05.")
        remember(KIND_EXPLICIT, "The user prefers PowerShell over bash.")
        prompt = agent.build_system_prompt("which shell do I use")
        # The total-prompt cap truncates from the end, so the section the model
        # must act on now must not be the first thing dropped.
        self.assertLess(prompt.index("# Standing rules"),
                        prompt.index("# Context retrieved"))

    def test_no_directives_means_no_empty_section(self):
        prompt = agent.build_system_prompt("anything")
        self.assertNotIn("Standing rules from the user", prompt)

    def test_prompt_survives_a_broken_store(self):
        # Nothing in the learning path may raise into the user's turn.
        broken = store.GLOBAL_DIR / "facts.jsonl"
        broken.parent.mkdir(parents=True, exist_ok=True)
        broken.write_text("{not json at all\n", encoding="utf-8")
        self.assertTrue(agent.build_system_prompt("hello"))


class TestInContextReinforcement(StoreTestCase):
    """The system prompt alone loses to transcript momentum by turn 20."""

    def _turns(self, n):
        return [{"role": "user", "content": f"turn {i}"} for i in range(1, n + 1)]

    def test_reminder_appears_on_the_nth_turn(self):
        remember(KIND_DIRECTIVE, "Always append the date 2026-08-05.")
        messages = self._turns(agent.STANDING_RULE_REMINDER_EVERY)
        agent._reinforce_standing_rules(messages)
        self.assertIn("standing-rules", messages[-1]["content"])
        self.assertIn("2026-08-05", messages[-1]["content"])

    def test_no_reminder_on_an_ordinary_turn(self):
        remember(KIND_DIRECTIVE, "Always append the date 2026-08-05.")
        messages = self._turns(agent.STANDING_RULE_REMINDER_EVERY - 1)
        agent._reinforce_standing_rules(messages)
        self.assertNotIn("standing-rules", messages[-1]["content"])

    def test_the_users_own_text_is_preserved(self):
        remember(KIND_DIRECTIVE, "Always append the date 2026-08-05.")
        messages = self._turns(agent.STANDING_RULE_REMINDER_EVERY)
        original = messages[-1]["content"]
        agent._reinforce_standing_rules(messages)
        self.assertTrue(messages[-1]["content"].startswith(original))

    def test_reminders_do_not_stack(self):
        remember(KIND_DIRECTIVE, "Always append the date 2026-08-05.")
        messages = self._turns(agent.STANDING_RULE_REMINDER_EVERY)
        agent._reinforce_standing_rules(messages)
        agent._reinforce_standing_rules(messages)
        self.assertEqual(messages[-1]["content"].count("<standing-rules>"), 1)

    def test_nothing_is_added_when_there_are_no_rules(self):
        messages = self._turns(agent.STANDING_RULE_REMINDER_EVERY)
        agent._reinforce_standing_rules(messages)
        self.assertEqual(messages[-1]["content"],
                         f"turn {agent.STANDING_RULE_REMINDER_EVERY}")

    def test_turn_count_is_not_disturbed(self):
        remember(KIND_DIRECTIVE, "Always append the date 2026-08-05.")
        messages = self._turns(agent.STANDING_RULE_REMINDER_EVERY)
        before = len(messages)
        agent._reinforce_standing_rules(messages)
        self.assertEqual(len(messages), before,
                         "a reminder must never become its own turn — that "
                         "breaks user/assistant alternation on strict providers")


class TestDeterministicRuleCapture(StoreTestCase):
    """Capture must not depend on the model choosing to call a tool.

    From a live run against poolside/laguna-s-2.1:free. Told three times
    "Rule N: always ... Save that to memory", it answered "Saved." every time
    and never called save_memory — the session's tool log has ten calls and not
    one write. The rules only survived because they were already in the store;
    a genuinely new rule would have been lost while the user was told it was
    kept, which is worse than refusing outright.
    """

    def test_a_stated_rule_asked_to_be_saved_is_captured(self):
        text = ("Rule one: always end every single reply with the current date "
                "in square brackets, like [2026-08-05]. Save that to memory.")
        self.assertIsNotNone(agent.capture_stated_rule(text))
        self.assertIn("2026-08-05", learning.directives_for_prompt())

    def test_from_now_on_counts_as_a_save_request(self):
        self.assertIsNotNone(
            agent.capture_stated_rule("From now on, never use tabs."))

    def test_ukrainian_and_russian_are_captured(self):
        # Ukrainian writes the apostrophe as U+02BC far more often than as ',
        # and matching only ' silently dropped the agent's default language.
        self.assertIsNotNone(
            agent.capture_stated_rule("Завжди відповідай українською. Запамʼятай це."))
        self.assertIsNotNone(
            agent.capture_stated_rule("Завжди відповідай українською. Запам'ятай."))
        self.assertIsNotNone(
            agent.capture_stated_rule("Всегда пиши тесты. Запомни это."))

    def test_a_save_request_that_is_not_a_rule_is_ignored(self):
        # Both signals are required — either alone is far too loose.
        self.assertIsNone(agent.capture_stated_rule("Save this file to disk."))
        self.assertIsNone(agent.capture_stated_rule("Remember that meeting we had?"))
        self.assertIsNone(agent.capture_stated_rule("read agent.py and remember what it does"))

    def test_a_rule_with_no_save_request_is_ignored(self):
        # Ordinary conversation is full of "always" and must not become
        # permanent state behind the user's back.
        self.assertIsNone(agent.capture_stated_rule("Always run the tests first."))
        self.assertIsNone(agent.capture_stated_rule("I always forget that flag."))

    def test_a_long_message_is_not_captured_wholesale(self):
        self.assertIsNone(agent.capture_stated_rule(
            "Always do this. Save it. " + "x" * 700))

    def test_capture_is_off_in_incognito(self):
        learning.set_enabled(False)
        try:
            self.assertIsNone(agent.capture_stated_rule(
                "Always end with the date. Save that."))
        finally:
            learning.set_enabled(True)

    def test_capture_never_raises(self):
        for bad in (None, "", "   "):
            self.assertIsNone(agent.capture_stated_rule(bad))


class TestSlashCommandsSentAsMessages(unittest.TestCase):
    """A `/command` in the message stream was dead text."""

    def test_a_known_command_is_executed_not_sent_to_the_model(self):
        messages = [{"role": "user", "content": "/help"}]
        result = agent._intercept_slash_command(messages)
        self.assertIsNotNone(result)
        self.assertEqual(messages, [],
                         "the consumed prompt must not stay in the transcript")

    def test_an_unknown_command_falls_through_to_the_model(self):
        # A message that merely begins with a path must not be swallowed.
        messages = [{"role": "user", "content": "/usr/local/bin matters here"}]
        self.assertIsNone(agent._intercept_slash_command(messages))
        self.assertEqual(len(messages), 1)

    def test_double_slash_escapes(self):
        messages = [{"role": "user", "content": "//help is a literal"}]
        self.assertIsNone(agent._intercept_slash_command(messages))

    def test_multi_line_text_is_not_a_command(self):
        messages = [{"role": "user", "content": "/help\nand more text"}]
        self.assertIsNone(agent._intercept_slash_command(messages))

    def test_a_plain_message_is_untouched(self):
        messages = [{"role": "user", "content": "read agent.py"}]
        self.assertIsNone(agent._intercept_slash_command(messages))

    def test_an_assistant_message_is_never_intercepted(self):
        messages = [{"role": "assistant", "content": "/help"}]
        self.assertIsNone(agent._intercept_slash_command(messages))


class TestNoteBridge(StoreTestCase):
    """`/note` wrote the YAML wrapper into the fact instead of the note."""

    def setUp(self):
        super().setUp()
        self._notes = tempfile.TemporaryDirectory()
        root = Path(self._notes.name)
        self._saved_notes = {k: getattr(self_notes, k)
                             for k in ("NOTES_DIR", "INDEX_PATH")
                             if hasattr(self_notes, k)}
        if hasattr(self_notes, "NOTES_DIR"):
            self_notes.NOTES_DIR = root
        if hasattr(self_notes, "INDEX_PATH"):
            self_notes.INDEX_PATH = root / "index.json"

    def tearDown(self):
        for k, v in self._saved_notes.items():
            setattr(self_notes, k, v)
        self._notes.cleanup()
        super().tearDown()

    def test_the_fact_holds_the_note_body_not_its_frontmatter(self):
        self_notes.create_note("response_formatting",
                               "Always end every response with the date.")
        fact = store.load_facts("global")[0]
        self.assertNotIn("auto_generated", fact["fact"])
        self.assertNotIn("created_at", fact["fact"])
        self.assertIn("Always end every response", fact["fact"])

    def test_keywords_describe_the_note_not_its_metadata(self):
        self_notes.create_note("response_formatting",
                               "Always end every response with the date.")
        keywords = store.load_facts("global")[0]["keywords"]
        self.assertIn("date", keywords)
        self.assertNotIn("created_at", keywords)

    def test_a_user_written_rule_applies_immediately(self):
        # It used to need three independent sightings before it could enter a
        # prompt, so a note was invisible for its entire useful life.
        self_notes.create_note("fmt", "Always end every response with the date.")
        self.assertIn("Always end every response",
                      learning.directives_for_prompt())

    def test_a_user_written_preference_is_active_but_not_a_directive(self):
        self_notes.create_note("shell", "The user prefers PowerShell over bash.")
        self.assertEqual(learning.directives_for_prompt(), "")
        self.assertIn("PowerShell", learning.recall("which shell"))

    def test_an_auto_generated_note_still_goes_through_the_gate(self):
        self_notes.create_note("guess", "Always do the inferred thing.",
                               auto_generated=True)
        fact = store.load_facts("global")[0]
        self.assertEqual(fact["status"], store.STATUS_OBSERVED)
        self.assertEqual(learning.directives_for_prompt(), "")

    def test_the_note_file_itself_still_has_frontmatter(self):
        note_id = self_notes.create_note("fmt", "Always end with the date.")
        body = self_notes.get_note(note_id)
        self.assertIsNotNone(body)


class TestQuotaAndBackoff(unittest.TestCase):
    """14 turns × ~38s of retry ladder, all failing, all reported as turns."""

    def _err(self, text, headers=None):
        class Response:
            def __init__(self, h):
                self.headers = h

        class Err(Exception):
            def __init__(self, msg, h):
                super().__init__(msg)
                if h is not None:
                    self.response = Response(h)
        return Err(text, headers)

    def test_the_real_free_tier_error_is_classified_as_a_quota(self):
        from core.loop import is_quota_error, is_retryable_error
        real = ('HTTP 429 from https://opencode.ai/zen/v1/chat/completions: '
                '{"type":"error","error":{"type":"FreeUsageLimitError",'
                '"message":"Rate limit exceeded. Please try again later."}}')
        err = self._err(real)
        self.assertTrue(is_quota_error(err))
        # Still matches the retryable markers — the quota check must be
        # consulted *first*, which is what stops the pointless ladder.
        self.assertTrue(is_retryable_error(err))

    def test_the_openrouter_daily_cap_is_classified_as_a_quota(self):
        # Found by a live run, not by reasoning: this message says neither
        # "quota" nor anything the first marker list matched, so it burned the
        # full 5/10/20s ladder on every remaining turn before giving up.
        from core.loop import is_quota_error
        real = ('HTTP 429 from https://openrouter.ai/api/v1/chat/completions: '
                '{"error":{"message":"Rate limit exceeded: free-models-per-day. '
                'Add 10 credits to unlock 1000 free model requests per day",'
                '"code":429,"metadata":{"limit_source":"openrouter_free_tier_daily"}}}')
        self.assertTrue(is_quota_error(self._err(real)))

    GEMINI_RPM = (
        "HTTP 429: You exceeded your current quota, please check your plan and "
        "billing details. * Quota exceeded for metric: "
        "generate_content_free_tier_requests, limit: 0 "
        '"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier" '
        "Please retry in 6.152706999s."
    )

    def test_a_stated_retry_time_outranks_quota_wording(self):
        # Gemini answers a *per-minute* cap with prose about plans and billing
        # attached to a six-second wait. Matching "billing" alone made every
        # ordinary rate-limit fatal and killed runs that the next attempt would
        # have completed — caught live, on a real session that produced nothing.
        from core.loop import is_quota_error, retry_hint_seconds
        err = self._err(self.GEMINI_RPM)
        self.assertFalse(is_quota_error(err))
        self.assertAlmostEqual(retry_hint_seconds(err), 6.152706999, places=3)

    def test_the_backoff_honours_a_stated_wait(self):
        from core.loop import backoff_delay
        delay = backoff_delay(0, self._err(self.GEMINI_RPM))
        # Just over what was asked: arriving at the exact instant re-races it.
        self.assertGreater(delay, 6.15)
        self.assertLess(delay, 8)

    def test_a_per_day_cap_is_fatal_even_with_a_retry_hint(self):
        # Google attaches "Please retry in 11.9s" to a *daily* cap as well, so
        # the hint cannot be the last word — the allowance is still gone eleven
        # seconds later. Caught live on a key with a 20/day limit.
        from core.loop import is_quota_error
        err = self._err(
            'HTTP 429: You exceeded your current quota. "quotaId": '
            '"GenerateRequestsPerDayPerProjectPerModel-FreeTier", limit: 20. '
            "Please retry in 11.90507858s.")
        self.assertTrue(is_quota_error(err))

    def test_a_per_minute_quota_id_alone_is_enough(self):
        from core.loop import is_quota_error
        self.assertFalse(is_quota_error(self._err(
            'quota exceeded "quotaId": "RequestsPerMinutePerProject-FreeTier"')))

    def test_hard_quotas_are_still_fatal(self):
        from core.loop import is_quota_error
        for text in ("Rate limit exceeded: free-models-per-day. Add 10 credits",
                     "FreeUsageLimitError",
                     "rate_limit_error: Token Plan usage limit reached"):
            self.assertTrue(is_quota_error(self._err(text)), text)

    def test_an_ordinary_429_is_not_a_quota(self):
        from core.loop import is_quota_error, is_retryable_error
        err = self._err("HTTP 429 Too Many Requests")
        self.assertFalse(is_quota_error(err))
        self.assertTrue(is_retryable_error(err))

    def test_an_epoch_millisecond_reset_header_is_not_slept_on_literally(self):
        # OpenRouter sends X-RateLimit-Reset as an absolute epoch in ms.
        # Reading 1785974400000 as a delta would sleep for 56,000 years.
        from core.loop import MAX_RETRY_DELAY, retry_after_seconds
        err = self._err("429", {"x-ratelimit-reset": "1785974400000"})
        delay = retry_after_seconds(err)
        self.assertIsNotNone(delay)
        self.assertLessEqual(delay, MAX_RETRY_DELAY)

    def test_an_epoch_second_reset_header_becomes_a_delta(self):
        import time

        from core.loop import retry_after_seconds
        err = self._err("429", {"x-ratelimit-reset": str(int(time.time()) + 30)})
        delay = retry_after_seconds(err)
        self.assertGreater(delay, 20)
        self.assertLess(delay, 40)

    def test_a_plain_delta_is_left_alone(self):
        from core.loop import retry_after_seconds
        self.assertEqual(retry_after_seconds(self._err("429", {"retry-after": "12"})), 12.0)

    def test_retry_after_is_honoured(self):
        from core.loop import backoff_delay, retry_after_seconds
        err = self._err("429", {"retry-after": "12"})
        self.assertEqual(retry_after_seconds(err), 12.0)
        # Waited out with a small margin — returning at the exact stated
        # instant races the window and 429s again.
        self.assertEqual(backoff_delay(0, err), 12.5)

    def test_retry_after_is_capped(self):
        from core.loop import MAX_RETRY_DELAY, retry_after_seconds
        err = self._err("429", {"retry-after": "99999"})
        self.assertEqual(retry_after_seconds(err), MAX_RETRY_DELAY)

    def test_a_garbage_retry_after_is_ignored_not_fatal(self):
        from core.loop import retry_after_seconds
        self.assertIsNone(retry_after_seconds(self._err("429", {"retry-after": "soon"})))
        self.assertIsNone(retry_after_seconds(self._err("429", {})))
        self.assertIsNone(retry_after_seconds(self._err("429")))

    def test_backoff_is_jittered(self):
        from core.loop import backoff_delay
        # Without jitter every client retries on the same instant and
        # re-collides on the same limiter.
        delays = {backoff_delay(1) for _ in range(20)}
        self.assertGreater(len(delays), 1)

    def test_backoff_still_grows_and_stays_capped(self):
        from core.loop import MAX_RETRY_DELAY, backoff_delay
        self.assertLess(backoff_delay(0), backoff_delay(3))
        self.assertLessEqual(backoff_delay(10), MAX_RETRY_DELAY * 1.2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
