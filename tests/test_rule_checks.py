#!/usr/bin/env python3
"""
The rule scorer, tested against text that looks like real output.

A scorer is measurement equipment. If it is wrong, every conclusion drawn with
it is wrong in a way that looks authoritative — which is exactly how the
original analysis produced "16/28" for a session with sixteen answered turns.
So the scorer gets tested harder than the thing it measures.

Two failure modes matter equally here:

  * scoring a rule that did not apply to the turn (a structural rule checked
    against a one-line answer) reports failures that never happened, and
  * scoring loosely (substring matching a banned word so that "identifier"
    trips a ban on "IDE") reports passes that never happened.

Run: python -m unittest discover -s tests -p "test_*.py"
"""
import sys
import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent.resolve()
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from tests.rule_checks import evaluate, has_cyrillic, parse_rule, script_ratio

# A plausible methodichka, in the structure AGENT.md actually specifies.
LAB = """ЛАБОРАТОРНА РОБОТА № 2
Тема: Умовні оператори та цикли

Мета роботи
Навчитися використовувати умовні оператори та цикли в мові Python.

Загальні відомості
Умовний оператор if дозволяє виконувати різні гілки коду залежно від умови.
Цикл for призначений для перебору послідовностей.

Контрольні запитання
1. Що таке умовний оператор?
2. Чим відрізняється цикл for від while?
3. Для чого використовується оператор break?
4. Що робить оператор continue?
5. Як організувати вкладений цикл?

Завдання
Розробити програму, яка обчислює суму парних чисел.

Література
1. Лутц М. Вивчаємо Python. — 2021.
"""

SHORT = "Цикл for перебирає елементи послідовності."
ENGLISH = "A for loop iterates over a sequence of elements."


class TestParsing(unittest.TestCase):

    def test_bare_needle_stays_a_contains_rule(self):
        # The original --expect date=2026-08-05 form must keep working.
        rule = parse_rule("date=2026-08-05")
        self.assertEqual(rule["kind"], "contains")
        self.assertEqual(rule["arg"], "2026-08-05")

    def test_typed_rule(self):
        rule = parse_rule("no_ide=absent:IDE")
        self.assertEqual((rule["kind"], rule["arg"]), ("absent", "IDE"))

    def test_gate_is_parsed(self):
        rule = parse_rule("s=order:A>B@reply_contains:Мета")
        self.assertEqual(rule["gate"], "reply_contains")
        self.assertEqual(rule["gate_arg"], "Мета")

    def test_malformed_specs_raise(self):
        for bad in ("nonsense", "=value", "name="):
            with self.assertRaises(ValueError):
                parse_rule(bad)

    def test_unknown_gate_raises(self):
        with self.assertRaises(ValueError):
            parse_rule("s=contains:x@no_such_gate:y")


class TestGating(unittest.TestCase):
    """A rule that did not apply is neither a pass nor a failure."""

    def test_structural_rule_is_skipped_on_a_short_answer(self):
        rule = parse_rule(
            "sections=order:Мета роботи>Література@reply_contains:Мета роботи")
        applicable, passed, _ = evaluate(rule, "що таке цикл?", SHORT)
        self.assertFalse(applicable)
        self.assertFalse(passed)

    def test_structural_rule_applies_to_a_real_document(self):
        rule = parse_rule(
            "sections=order:Мета роботи>Література@reply_contains:Мета роботи")
        applicable, passed, _ = evaluate(rule, "зроби лабораторну", LAB)
        self.assertTrue(applicable)
        self.assertTrue(passed)

    def test_language_rule_only_applies_to_cyrillic_prompts(self):
        rule = parse_rule("ua=lang:uk@prompt_is_cyrillic")
        applicable, _, _ = evaluate(rule, "answer in english please", ENGLISH)
        self.assertFalse(applicable)
        applicable, passed, _ = evaluate(rule, "що таке цикл?", SHORT)
        self.assertTrue(applicable)
        self.assertTrue(passed)


class TestOrder(unittest.TestCase):

    def test_correct_order_passes(self):
        rule = parse_rule("s=order:Мета роботи>Загальні відомості>"
                          "Контрольні запитання>Завдання>Література")
        _, passed, detail = evaluate(rule, "", LAB)
        self.assertTrue(passed, detail)

    def test_wrong_order_fails_and_says_so(self):
        swapped = LAB.replace("Завдання", "ZZZ").replace("Література", "Завдання")
        rule = parse_rule("s=order:Завдання>ZZZ")
        _, passed, detail = evaluate(rule, "", swapped)
        self.assertFalse(passed)
        self.assertIn("order was", detail)

    def test_a_missing_section_is_named(self):
        rule = parse_rule("s=order:Мета роботи>Додаток")
        _, passed, detail = evaluate(rule, "", LAB)
        self.assertFalse(passed)
        self.assertIn("Додаток", detail)


class TestCount(unittest.TestCase):
    """'Exactly five questions' is a rule substring matching cannot express."""

    QUESTIONS = r"^\s*\d+[.)]\s+.*\?\s*$"

    def test_exactly_five_passes(self):
        rule = parse_rule(f"q=count:5:{self.QUESTIONS}")
        _, passed, detail = evaluate(rule, "", LAB)
        self.assertTrue(passed, detail)

    def test_four_fails_with_the_actual_number(self):
        four = LAB.replace("5. Як організувати вкладений цикл?\n", "")
        rule = parse_rule(f"q=count:5:{self.QUESTIONS}")
        _, passed, detail = evaluate(rule, "", four)
        self.assertFalse(passed)
        self.assertIn("found 4", detail)

    def test_six_also_fails(self):
        six = LAB.replace("Завдання\n", "6. Ще одне питання?\n\nЗавдання\n")
        rule = parse_rule(f"q=count:5:{self.QUESTIONS}")
        _, passed, detail = evaluate(rule, "", six)
        self.assertFalse(passed)
        self.assertIn("found 6", detail)

    def test_a_malformed_count_spec_fails_loudly(self):
        rule = parse_rule("q=count:notanumber")
        _, passed, detail = evaluate(rule, "", LAB)
        self.assertFalse(passed)
        self.assertIn("malformed", detail)


class TestAbsent(unittest.TestCase):
    """Negative rules need word boundaries or they are useless."""

    def test_a_banned_word_is_caught(self):
        rule = parse_rule("x=absent:IDE")
        _, passed, detail = evaluate(rule, "", "Рекомендую IDE PyCharm.")
        self.assertFalse(passed)
        self.assertIn("banned", detail)

    def test_the_ban_does_not_trip_on_a_longer_word(self):
        # "identifier" and "ідентифікатор" must not count as "IDE".
        rule = parse_rule("x=absent:IDE")
        for text in ("Use a clear identifier name.",
                     "Задайте ідентифікатор змінної.",
                     "Середовище програмування PyCharm."):
            _, passed, _ = evaluate(rule, "", text)
            self.assertTrue(passed, text)

    def test_the_ban_is_case_insensitive(self):
        rule = parse_rule("x=absent:IDE")
        _, passed, _ = evaluate(rule, "", "выбери ide по вкусу")
        self.assertFalse(passed)


class TestLanguage(unittest.TestCase):

    def test_ukrainian_reply_passes_a_ukrainian_rule(self):
        rule = parse_rule("l=lang:uk")
        _, passed, detail = evaluate(rule, "", SHORT)
        self.assertTrue(passed, detail)

    def test_english_reply_fails_a_ukrainian_rule(self):
        rule = parse_rule("l=lang:uk")
        _, passed, _ = evaluate(rule, "", ENGLISH)
        self.assertFalse(passed)

    def test_ukrainian_text_with_a_code_sample_still_passes(self):
        # The rule must not break on the first identifier, or it is untestable
        # on a coding agent.
        mixed = ("Функція обчислює факторіал:\n\n"
                 "```python\ndef factorial(n: int) -> int:\n"
                 "    return 1 if n <= 1 else n * factorial(n - 1)\n```\n\n"
                 "Ця функція використовує рекурсію для обчислення результату "
                 "і працює для невідʼємних цілих чисел.")
        rule = parse_rule("l=lang:uk")
        _, passed, detail = evaluate(rule, "", mixed)
        self.assertTrue(passed, detail)

    def test_script_detection(self):
        self.assertTrue(has_cyrillic("що таке цикл"))
        self.assertFalse(has_cyrillic("what is a loop"))
        cyr, lat = script_ratio("абв abc")
        self.assertAlmostEqual(cyr, 0.5)
        self.assertAlmostEqual(lat, 0.5)

    def test_empty_text_does_not_divide_by_zero(self):
        self.assertEqual(script_ratio("12345 !!!"), (0.0, 0.0))


class TestRegexAndAll(unittest.TestCase):

    def test_lab_heading_pattern(self):
        rule = parse_rule(r"h=regex:ЛАБОРАТОРНА РОБОТА\s*№\s*\d+")
        _, passed, _ = evaluate(rule, "", LAB)
        self.assertTrue(passed)

    def test_numbering_continues_rule(self):
        # "must not restart at 1" — matches № 2 and above.
        rule = parse_rule(r"n=regex:№\s*(?:[2-9]|\d\d)")
        self.assertTrue(evaluate(rule, "", LAB)[1])
        self.assertFalse(evaluate(rule, "", LAB.replace("№ 2", "№ 1"))[1])

    def test_all_names_every_missing_item(self):
        rule = parse_rule("a=all:Мета роботи,Завдання,Додаток,Глосарій")
        _, passed, detail = evaluate(rule, "", LAB)
        self.assertFalse(passed)
        self.assertIn("Додаток", detail)
        self.assertIn("Глосарій", detail)

    def test_a_bad_regex_fails_instead_of_raising(self):
        rule = parse_rule("h=regex:[unclosed")
        _, passed, detail = evaluate(rule, "", LAB)
        self.assertFalse(passed)
        self.assertIn("bad pattern", detail)


class TestCorpusSpecsAreValid(unittest.TestCase):
    """Every rule the corpus declares must parse and behave."""

    def test_all_declared_specs_parse(self):
        from tests.session_prompts import SESSION_RULES
        for session, specs in SESSION_RULES.items():
            for spec in specs:
                try:
                    parse_rule(spec)
                except ValueError as e:
                    self.fail(f"{session}: {spec!r} -> {e}")

    def test_the_declared_specs_pass_on_a_correct_document(self):
        # Guards the guard: if the corpus rules could never pass, a live run
        # would report failures that say nothing about the agent.
        from tests.rule_checks import evaluate as ev
        from tests.session_prompts import rules_for
        for spec in rules_for("teacher-1-setup"):
            rule = parse_rule(spec)
            applicable, passed, detail = ev(rule, "зроби лабораторну роботу", LAB)
            if applicable:
                self.assertTrue(passed, f"{spec} -> {detail}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
