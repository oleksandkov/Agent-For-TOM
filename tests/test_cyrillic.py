#!/usr/bin/env python3
"""
Ukrainian and Russian support (Phase 7, Part A).

Before this phase a Cyrillic keystroke never reached the input buffer, the
tokeniser returned [] for every Cyrillic message, and PDF export raised on
the first Cyrillic character. Each test here corresponds to one of those.

Run: python -m unittest tests.test_cyrillic -v
"""
import io
import sys
import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent.resolve()
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import agent
import text_display as td
from learning import text as ltext

UA = "Привіт! Це тест українською мовою."
RU = "Привет! Это тест на русском языке."
UA_ONLY = "їєґіІЇЄҐ"


def _script(seq):
    """A getwch stand-in that replays a keystroke sequence."""
    it = iter(seq)
    return lambda: next(it)


def _drive_input(seq: list[str]) -> str:
    """Run the real input loop against scripted keystrokes."""
    import msvcrt
    original = getattr(msvcrt, "getwch", None)
    msvcrt.getwch = _script(seq)
    saved_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        return agent.read_input_with_suggestions("> ")
    finally:
        sys.stdout = saved_stdout
        if original is not None:
            msvcrt.getwch = original


# ══════════════════════════════════════════════════════════════════════
#  P7-1 — the prompt accepts every script
# ══════════════════════════════════════════════════════════════════════

@unittest.skipUnless(sys.platform == "win32", "msvcrt input loop is Windows-only")
class TestInputAcceptsEveryScript(unittest.TestCase):

    def test_ascii_still_works(self):
        self.assertEqual(_drive_input(list("hello") + ["\r"]), "hello")

    def test_ukrainian(self):
        """The headline bug: `32 <= ch[0] < 127` against a byte discarded
        every Cyrillic keystroke, silently."""
        self.assertEqual(_drive_input(list("Привіт") + ["\r"]), "Привіт")

    def test_russian(self):
        self.assertEqual(_drive_input(list("Привет, мир") + ["\r"]), "Привет, мир")

    def test_ukrainian_only_letters(self):
        self.assertEqual(_drive_input(list(UA_ONLY) + ["\r"]), UA_ONLY)

    def test_accented_latin(self):
        self.assertEqual(_drive_input(list("café naïve") + ["\r"]), "café naïve")

    def test_cjk_and_emoji(self):
        self.assertEqual(_drive_input(list("日本 🚀") + ["\r"]), "日本 🚀")

    def test_mixed_scripts(self):
        self.assertEqual(_drive_input(list("файл README.md") + ["\r"]),
                         "файл README.md")

    def test_backspace_deletes_one_character(self):
        self.assertEqual(_drive_input(list("Привіт") + ["\x08", "\x08", "\r"]),
                         "Прив")

    def test_escape_clears_the_buffer(self):
        self.assertEqual(
            _drive_input(list("abc") + ["\x1b"] + list("Привіт") + ["\r"]),
            "Привіт")

    def test_slash_command_still_works(self):
        self.assertEqual(_drive_input(list("/help") + ["\r"]), "/help")

    def test_function_key_then_cyrillic(self):
        """F5-F8 moved into the extended-key branch; they must still fire and
        must not eat the following text."""
        self.assertEqual(_drive_input(["\x00", "\x42"] + list("тест") + ["\r"]),
                         "тест")

    def test_arrow_key_is_consumed_not_inserted(self):
        self.assertEqual(_drive_input(["\xe0", "K"] + list("ок") + ["\r"]), "ок")


# ══════════════════════════════════════════════════════════════════════
#  P7-2 / P7-9 — the tokeniser
# ══════════════════════════════════════════════════════════════════════

class TestTokeniser(unittest.TestCase):

    def test_ukrainian_produces_keywords(self):
        kws = ltext.extract_keywords("Прочитай файл конфігурації та виправ помилку")
        self.assertTrue(kws)
        self.assertIn("файл", kws)

    def test_russian_produces_keywords(self):
        kws = ltext.extract_keywords("Прочитай файл конфигурации и исправь ошибку")
        self.assertTrue(kws)
        self.assertIn("файл", kws)

    def test_english_is_unchanged(self):
        """The fix must not move English results."""
        self.assertEqual(
            ltext.extract_keywords("Read the configuration file and fix the error",
                                   aliases=False),
            ["read", "configuration", "file", "fix", "error"])

    def test_mixed_script_keeps_both(self):
        kws = ltext.extract_keywords("Файл README.md містить документацію")
        self.assertIn("readme", kws)
        self.assertIn("файл", kws)

    def test_similarity_works_on_cyrillic(self):
        self.assertGreater(
            ltext.similarity("Прочитай файл конфігурації", "файл конфігурації потрібен"),
            0.0)

    def test_similarity_separates_topics(self):
        self.assertEqual(
            ltext.similarity("зроби скріншот браузера", "прочитай файл коду"), 0.0)

    def test_stop_words_do_not_dominate(self):
        """Without Cyrillic stop words, 'що/це/як' would top every result."""
        kws = ltext.extract_keywords("Що це таке і як це працює? Це дуже важливо.")
        for noise in ("що", "це", "як", "дуже"):
            self.assertNotIn(noise, kws)

    def test_digits_and_underscores_are_not_words(self):
        self.assertNotIn("123", ltext.extract_keywords("test 123 _ value"))

    def test_aliases_bridge_to_english_tool_names(self):
        kws = ltext.extract_keywords("зроби скріншот браузера")
        self.assertIn("screenshot", kws)
        self.assertIn("browser", kws)

    def test_aliases_can_be_disabled(self):
        kws = ltext.extract_keywords("зроби скріншот браузера", aliases=False)
        self.assertNotIn("screenshot", kws)
        self.assertIn("скріншот", kws)

    def test_alias_never_displaces_a_real_word(self):
        kws = ltext.extract_keywords("файл")
        self.assertEqual(kws[0], "файл")


class TestToolSelectionAcrossLanguages(unittest.TestCase):
    """Tool names and descriptions are English; a Ukrainian request must
    still reach the right tool."""

    def setUp(self):
        mk = lambda n, d: {"name": n, "description": d,
                           "input_schema": {"type": "object", "properties": {}}}
        # sql_query FIRST: with list-order fallback this is what gets picked,
        # so a passing result here cannot be luck.
        self.pool = agent.TOOLS + [
            mk("sql_query", "run a sql query on a postgres database"),
            mk("take_screenshot", "screenshot the browser page"),
        ]
        self.budget = len(agent.TOOLS) + 1
        self.builtin = {t["name"] for t in agent.TOOLS}

    def pick(self, ctx):
        return [t["name"] for t in agent.select_tools(self.pool, ctx, self.budget)[0]
                if t["name"] not in self.builtin]

    def test_english_screenshot(self):
        self.assertEqual(self.pick("take a browser screenshot"), ["take_screenshot"])

    def test_ukrainian_screenshot(self):
        self.assertEqual(self.pick("зроби скріншот браузера"), ["take_screenshot"])

    def test_russian_screenshot(self):
        self.assertEqual(self.pick("сделай скриншот браузера"), ["take_screenshot"])

    def test_ukrainian_database(self):
        self.assertEqual(self.pick("виконай запит до бази даних"), ["sql_query"])

    def test_russian_database(self):
        self.assertEqual(self.pick("выполни запрос к базе данных"), ["sql_query"])

    def test_cyrillic_is_not_just_list_order(self):
        self.assertNotEqual(self.pick("зроби скріншот браузера"), self.pick(""))


# ══════════════════════════════════════════════════════════════════════
#  P7-6 — display width
# ══════════════════════════════════════════════════════════════════════

class TestDisplayWidth(unittest.TestCase):

    def test_cyrillic_is_single_width(self):
        self.assertEqual(td.display_width(UA_ONLY), len(UA_ONLY))

    def test_cjk_is_double_width(self):
        self.assertEqual(td.display_width("日本"), 4)

    def test_emoji_is_double_width(self):
        self.assertEqual(td.display_width("🚀"), 2)

    def test_combining_marks_are_zero_width(self):
        self.assertEqual(td.display_width("é"), 1)   # decomposed é

    def test_ansi_is_not_counted(self):
        self.assertEqual(td.display_width("\x1b[92mок\x1b[0m"), 2)

    def test_shorten_respects_columns(self):
        self.assertLessEqual(td.display_width(td.shorten(UA * 5, 20)), 20)

    def test_shorten_never_splits_a_character(self):
        out = td.shorten("日本語のテキスト", 7)
        self.assertLessEqual(td.display_width(out), 7)
        self.assertTrue(all(ord(c) for c in out))

    def test_shorten_leaves_short_text_alone(self):
        self.assertEqual(td.shorten("короткий", 40), "короткий")

    def test_wrap_respects_width(self):
        wrapped = td.wrap(UA * 6, width=40)
        for line in wrapped.splitlines():
            self.assertLessEqual(td.display_width(line), 40)

    def test_wrap_preserves_blank_lines(self):
        self.assertIn("", td.wrap("перший\n\nдругий", width=40).splitlines())

    def test_pad_uses_columns_not_len(self):
        self.assertEqual(td.display_width(td.pad("日本", 10)), 10)


# ══════════════════════════════════════════════════════════════════════
#  P7-5 — rendering
# ══════════════════════════════════════════════════════════════════════

class TestRendering(unittest.TestCase):

    def test_tool_args_are_not_escaped(self):
        """Cyrillic used to render as \\u043f\\u0440… in the tool line."""
        from adapters.terminal import summarise_args
        out = summarise_args("write_file", {"file_path": "привіт_світ.txt",
                                            "content": "Привіт"})
        self.assertIn("привіт_світ.txt", out)
        self.assertNotIn("\\u04", out)

    def test_long_args_cut_on_a_character_boundary(self):
        from adapters.terminal import summarise_args
        out = summarise_args("run_command", {"command": "rm " + "привіт " * 60})
        self.assertNotIn("\\u", out)

    def test_unknown_tool_falls_back_to_json_unescaped(self):
        from adapters.terminal import summarise_args
        out = summarise_args("some_mcp_tool", {"запит": "значення"})
        self.assertIn("запит", out)


# ══════════════════════════════════════════════════════════════════════
#  P7-4 — PDF
# ══════════════════════════════════════════════════════════════════════

class TestPdfCyrillic(unittest.TestCase):

    def setUp(self):
        try:
            import pdf_report_skill
        except Exception as e:
            self.skipTest(f"pdf_report_skill unavailable: {e}")
        if not getattr(pdf_report_skill, "FPDF", None):
            self.skipTest("fpdf2 not installed")
        self.ps = pdf_report_skill
        self.src = PROJECT_DIR / "latest_ai_news_report.txt"
        self.backup = (self.src.read_text(encoding="utf-8")
                       if self.src.exists() else None)
        self.out = PROJECT_DIR / "_test_cyr.pdf"
        self.src.write_text(f"Звіт українською\n\n- {UA}\n- {RU}\n- English too\n",
                            encoding="utf-8")

    def tearDown(self):
        self.out.unlink(missing_ok=True)
        if self.backup is not None:
            self.src.write_text(self.backup, encoding="utf-8")
        else:
            self.src.unlink(missing_ok=True)

    def test_cyrillic_pdf_generates(self):
        """Regression: FPDFUnicodeEncodingException on the first Cyrillic
        character, because the core Helvetica font is latin-1 only."""
        self.ps.generate_ai_news_pdf(str(self.out))
        self.assertTrue(self.out.exists())
        self.assertGreater(self.out.stat().st_size, 0)

    def test_a_unicode_font_is_actually_registered(self):
        pdf = self.ps._PDF()
        self.assertTrue(pdf._unicode_font,
                        "no Unicode TTF found; add_font was never called")

    def test_degrades_instead_of_raising_without_a_font(self):
        original = self.ps._PDF._try_add_unicode_font
        self.ps._PDF._try_add_unicode_font = lambda self: ""
        try:
            self.ps.generate_ai_news_pdf(str(self.out))
            self.assertTrue(self.out.exists())
        finally:
            self.ps._PDF._try_add_unicode_font = original


# ══════════════════════════════════════════════════════════════════════
#  State round-trips
# ══════════════════════════════════════════════════════════════════════

class TestStateRoundTrip(unittest.TestCase):

    def test_file_tools_preserve_cyrillic(self):
        probe = PROJECT_DIR / "_test_cyr_probe.txt"
        try:
            agent.handle_write_file({"file_path": str(probe),
                                     "content": f"{UA}\n{RU}\n"})
            out = agent.handle_read_file({"file_path": str(probe)})
            self.assertIn(UA, out)
            self.assertIn(RU, out)
            found = agent.handle_search_code({"pattern": "українською",
                                              "path": str(probe)})
            self.assertIn("українською", found)
        finally:
            probe.unlink(missing_ok=True)

    def test_cyrillic_filename(self):
        probe = PROJECT_DIR / "_тест_файл.txt"
        try:
            agent.handle_write_file({"file_path": str(probe), "content": "вміст"})
            self.assertIn("вміст", agent.handle_read_file({"file_path": str(probe)}))
        finally:
            probe.unlink(missing_ok=True)

    def test_shell_returns_cyrillic(self):
        out = agent.handle_run_command(
            {"command": 'python -c "print(\'Привіт світ\')"'})
        self.assertIn("Привіт світ", out)

    def test_session_stores_real_utf8(self):
        import json
        import session_manager
        sid = session_manager.save_session(
            [{"role": "user", "content": UA}, {"role": "assistant", "content": RU}],
            summary="Тест кирилиці", model="test",
            token_usage={"input": 1, "output": 1, "calls": 1},
            telemetry={"turn_metrics": {}, "tool_log": [], "failed_turns": []})
        path = session_manager.get_session_dir() / f"{sid}.json"
        try:
            raw = path.read_text(encoding="utf-8")
            self.assertIn("Привіт", raw)          # not Пр…
            self.assertEqual(json.loads(raw)["summary"], "Тест кирилиці")
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
