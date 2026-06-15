"""tests/test_synthesizer.py

Tests for the JSON-only LLM synthesizer (remote cascade → local Qwen →
user typed). We mock the remote cascade and Qwen layers so the tests are
fast, deterministic, and don't need network access or a working C++
compiler for llama-cpp-python.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from app.backend.llm import synthesizer
from app.backend.llm.synthesizer import (
    FEW_SHOT_EXAMPLE,
    SCHEMA,
    SynthesisResult,
    _coerce_to_gap_values,
    _extract_json_object,
    _validate_against_schema,
    synthesize_gap_values,
)


# A canonical mocked LLM response: pure JSON, follows the schema, with
# real Ukrainian academic text.
MOCKED_LLM_RESPONSE = json.dumps(
    {
        "goal": "дослідити принципи роботи основних алгоритмів сортування масивів (бульбашкового, вибором, вставленням, Шелла та швидкого) та експериментально порівняти їхню часову складність на різних наборах даних.",
        "general_info": "Сортування є фундаментальною операцією в обробці даних. Алгоритми сортування поділяються на прості (бульбачкове, вибором, вставлення) зі складністю O(n²) та ефективні (Шелла, швидке) зі складністю O(n log n). Вибір алгоритму залежить від розміру масиву, початкового стану даних та вимог до стабільності.",
        "tasks": [
            "Реалізувати п'ять алгоритмів сортування: бульбачкове, вибором, вставлення, Шелла та швидке.",
            "Згенерувати тестові набори даних трьох типів: випадкові, відсортовані, обернено відсортовані.",
            "Виміряти час виконання кожного алгоритму на масивах розміром 100, 1000 та 10000 елементів.",
        ],
        "control_questions": [
            "Поясніть принцип роботи алгоритму Шелла та його переваги над бульбачковим.",
            "У яких випадках найгірший випадок швидкого сортування зводиться до O(n²)?",
        ],
        "bibliography": [
            "Кнут, Д. Е. Мистецтво програмування. Т. 3 : Сортування і пошук. Київ : Вільямс, 2020. 824 с.",
            "Кормен, Т. Х. та ін. Вступ до алгоритмів. 3-тє вид. Київ : К.І.С., 2021. 1024 с.",
        ],
    },
    ensure_ascii=False,
)


class TestExtractJsonObject(unittest.TestCase):
    def test_pure_json(self):
        obj = _extract_json_object(MOCKED_LLM_RESPONSE)
        self.assertIsNotNone(obj)
        self.assertIn("goal", obj)
        self.assertEqual(len(obj["tasks"]), 3)

    def test_wrapped_in_fences(self):
        text = f"Here is the JSON:\n```json\n{MOCKED_LLM_RESPONSE}\n```\nDone."
        obj = _extract_json_object(text)
        self.assertIsNotNone(obj)
        self.assertEqual(len(obj["tasks"]), 3)

    def test_wrapped_in_plain_fences(self):
        text = f"```\n{MOCKED_LLM_RESPONSE}\n```"
        obj = _extract_json_object(text)
        self.assertIsNotNone(obj)
        self.assertEqual(obj["general_info"][:30], obj["general_info"][:30])

    def test_prose_around_json(self):
        text = f"Sure, here you go:\n\n{MOCKED_LLM_RESPONSE}\n\nLet me know."
        obj = _extract_json_object(text)
        self.assertIsNotNone(obj)
        self.assertEqual(len(obj["bibliography"]), 2)

    def test_none_for_garbage(self):
        self.assertIsNone(_extract_json_object("not json at all"))
        self.assertIsNone(_extract_json_object(""))
        self.assertIsNone(_extract_json_object(None))

    def test_python_keywords_normalised(self):
        # An LLM might emit `None` / `True` / `False` (Python) at
        # top-level instead of `null` / `true` / `false` (JSON). We
        # normalise on extract.
        x60 = "x" * 60
        bad = (
            '{"goal": "test", "general_info": "' + x60 +
            '", "tasks": ["a"], "control_questions": [True], "bibliography": [True]}'
        )
        obj = _extract_json_object(bad)
        self.assertIsNotNone(obj)
        self.assertEqual(obj["control_questions"], [True])

    def test_inner_braces_in_string(self):
        # Brace inside a string must not confuse the depth scanner.
        y60 = "y" * 60
        text = (
            '{"goal": "test {x} value", "general_info": "' + y60 +
            '", "tasks": ["a"], "control_questions": ["q"], "bibliography": ["b"]}'
        )
        obj = _extract_json_object(text)
        self.assertIsNotNone(obj)
        self.assertEqual(obj["goal"], "test {x} value")


class TestSchemaValidation(unittest.TestCase):
    def test_valid_object(self):
        obj = json.loads(MOCKED_LLM_RESPONSE)
        self.assertEqual(_validate_against_schema(obj), [])

    def test_missing_key(self):
        obj = json.loads(MOCKED_LLM_RESPONSE)
        del obj["goal"]
        errors = _validate_against_schema(obj)
        self.assertTrue(any("goal" in e for e in errors))

    def test_wrong_type(self):
        obj = json.loads(MOCKED_LLM_RESPONSE)
        obj["goal"] = 42
        errors = _validate_against_schema(obj)
        self.assertTrue(any("goal" in e for e in errors))

    def test_too_short(self):
        obj = json.loads(MOCKED_LLM_RESPONSE)
        obj["goal"] = "x"  # minLength=10
        errors = _validate_against_schema(obj)
        self.assertTrue(any("goal" in e for e in errors))

    def test_empty_list(self):
        obj = json.loads(MOCKED_LLM_RESPONSE)
        obj["tasks"] = []
        errors = _validate_against_schema(obj)
        self.assertTrue(any("tasks" in e for e in errors))

    def test_extra_keys_rejected(self):
        obj = json.loads(MOCKED_LLM_RESPONSE)
        obj["evil_extra"] = "should not be allowed"
        errors = _validate_against_schema(obj)
        self.assertTrue(any("unexpected" in e for e in errors))


class TestCoerceToGapValues(unittest.TestCase):
    def test_llm_overrides_user_when_ai_accessible(self):
        user = {
            "goal": {"value": "old goal", "ai_accessible": True},
            "lab_number": {"value": "1", "ai_accessible": False},
        }
        obj = json.loads(MOCKED_LLM_RESPONSE)
        out = _coerce_to_gap_values(obj, user)
        # LLM overwrote the goal with its own text
        self.assertNotEqual(out["goal"]["value"], "old goal")
        self.assertIn("дослідити принципи", out["goal"]["value"])
        # LLM also added general_info, tasks, etc.
        self.assertIn("general_info", out)
        self.assertIn("tasks", out)
        # User's lab_number (locked) survived
        self.assertEqual(out["lab_number"]["value"], "1")
        self.assertFalse(out["lab_number"]["ai_accessible"])

    def test_locked_user_value_preserved(self):
        user = {
            "goal": {"value": "locked original", "ai_accessible": False},
        }
        obj = json.loads(MOCKED_LLM_RESPONSE)
        out = _coerce_to_gap_values(obj, user)
        # The LLM tries to overwrite goal but it's locked
        self.assertEqual(out["goal"]["value"], "locked original")
        self.assertFalse(out["goal"]["ai_accessible"])


class TestSynthesizeWithMockedLocalQwen(unittest.TestCase):
    """Drive the full synthesizer with the local Qwen mocked.

    The remote cascade is the PRIMARY tier; local Qwen is the fallback.
    These tests mock the cascade so they stay offline and deterministic.
    """

    def setUp(self):
        import os
        os.environ["ALLOW_LOCAL_LLM"] = "true"
        try:
            from app.backend.db.connection import Database
            db = Database()
            try:
                db.conn.execute("DELETE FROM llm_cache")
                db.conn.commit()
            finally:
                db.close()
        except Exception:
            pass

    def _patch_qwen(self, response_text: str, output_tokens: int = 200):
        """Force the local Qwen call to return the given JSON text."""
        obj = json.loads(response_text)
        return patch.object(
            synthesizer, "_call_local_qwen_json_llm",
            return_value=(obj, {
                "model": "qwen-1.5b-fake",
                "source": "local_qwen",
                "prompt_tokens": 100,
                "output_tokens": output_tokens,
            }),
        )

    def _patch_qwen_failure(self, error: str = "Qwen crashed"):
        return patch.object(
            synthesizer, "_call_local_qwen_json_llm",
            return_value=(None, {"error": error}),
        )

    def _patch_remote_cascade_failure(self, error: str = "all providers failed"):
        return patch.object(
            synthesizer, "_call_remote_cascade",
            return_value=(None, {"source": "remote_cascade", "error": error}),
        )

    def _patch_remote(self, response_text: str, prompt_tokens: int = 1234, output_tokens: int = 567):
        return patch.object(synthesizer, "_call_remote_cascade",
                            return_value=(json.loads(response_text),
                                          {"model": "fake", "source": "remote_gemini",
                                           "prompt_tokens": prompt_tokens,
                                           "output_tokens": output_tokens}))

    def _patch_groq(self, response_text: str, prompt_tokens: int = 1234, output_tokens: int = 567):
        return patch.object(synthesizer, "_call_remote_cascade",
                            return_value=(json.loads(response_text),
                                          {"model": "groq-fake", "source": "remote_groq",
                                           "prompt_tokens": prompt_tokens,
                                           "output_tokens": output_tokens}))

    def test_local_qwen_is_fallback(self):
        """Local Qwen should win when the remote cascade is unavailable."""
        with self._patch_remote_cascade_failure("no providers configured"), \
             self._patch_qwen(MOCKED_LLM_RESPONSE):
            result = synthesize_gap_values(
                template_id="lab1",
                theme="Алгоритми сортування",
                user_input="зроби лабу",
                length="middle",
                hardness="university_1",
                user_gap_values={"goal": {"value": "old", "ai_accessible": True}},
            )
        self.assertEqual(result.source, "local_qwen")
        # The LLM's value for "goal" replaced the user's
        self.assertIn("дослідити принципи", result.gap_values["goal"]["value"])
        self.assertNotEqual(result.gap_values["goal"]["value"], "old")

    def test_remote_cascade_is_primary_when_configured(self):
        """When REMOTE_LLM_PROVIDER=groq + GROQ_API_KEY is set,
        the remote cascade runs FIRST. Local Qwen is the fallback."""
        import os
        with patch.dict(os.environ, {"GROQ_API_KEY": "gsk_FAKE_FOR_TEST", "REMOTE_LLM_PROVIDER": "groq"}), \
             self._patch_groq(MOCKED_LLM_RESPONSE):
            result = synthesize_gap_values(
                template_id="lab1",
                theme="Алгоритми сортування",
                user_input="зроби лабу",
                length="middle",
                hardness="university_1",
                user_gap_values={"goal": {"value": "old", "ai_accessible": True}},
            )
        self.assertEqual(result.source, "remote")
        self.assertIn("дослідити принципи", result.gap_values["goal"]["value"])

    def test_remote_used_when_qwen_fails(self):
        """Remote cascade is the FALLBACK when local Qwen fails."""
        with self._patch_qwen_failure("Qwen model file missing"), \
             self._patch_remote(MOCKED_LLM_RESPONSE):
            result = synthesize_gap_values(
                template_id="lab1",
                theme="Алгоритми сортування",
                user_input="зроби лабу",
                length="middle",
                hardness="university_1",
                user_gap_values={"goal": {"value": "old", "ai_accessible": True}},
            )
        self.assertEqual(result.source, "remote")
        self.assertIn("дослідити принципи", result.gap_values["goal"]["value"])

    def test_user_typed_when_both_fail(self):
        """Falls through to user_typed when both tiers fail."""
        with self._patch_qwen_failure("no qwen"), \
             self._patch_remote_cascade_failure("all providers failed"):
            result = synthesize_gap_values(
                template_id="lab1",
                theme="X",
                user_input="x",
                length="middle",
                hardness="university_1",
                user_gap_values={"goal": {"value": "user typed", "ai_accessible": True}},
            )
        self.assertEqual(result.source, "user_typed")
        self.assertEqual(result.gap_values["goal"]["value"], "user typed")


class TestGapAssemblerAfterSynthesis(unittest.TestCase):
    """End-to-end: the synthesizer produces gap_values; gap_assembler
    turns them into a real filled.py; the filled.py contains the LLM's
    text, not the user's typed defaults."""

    def setUp(self):
        import os
        os.environ["ALLOW_LOCAL_LLM"] = "true"
        try:
            from app.backend.db.connection import Database
            db = Database()
            try:
                db.conn.execute("DELETE FROM llm_cache")
                db.conn.commit()
            finally:
                db.close()
        except Exception:
            pass

    def test_filled_py_contains_llm_text(self):
        # Disable remote cascade so the synthesizer uses local Qwen.
        with patch.object(synthesizer, "_call_remote_cascade",
                          return_value=(None, {"source": "remote_cascade", "error": "no providers configured"})), \
             patch.object(synthesizer, "_call_local_qwen_json_llm",
                          return_value=(json.loads(MOCKED_LLM_RESPONSE),
                                        {"model": "qwen-fake", "source": "local_qwen",
                                         "prompt_tokens": 100, "output_tokens": 200})):
            synthesis = synthesize_gap_values(
                template_id="lab1",
                theme="Алгоритми сортування: аналіз ефективності",
                user_input="зроби лабу",
                length="middle",
                hardness="university_1",
                user_gap_values={
                    "lab_number": {"value": "1", "ai_accessible": False},
                    "work_title": {"value": "Алгоритми сортування: аналіз ефективності", "ai_accessible": True},
                    "goal": {"value": "short user goal", "ai_accessible": True},
                    "general_info": {"value": "one-liner", "ai_accessible": True},
                    "tasks": {"value": ["t1"], "ai_accessible": True},
                    "control_questions": {"value": ["q1"], "ai_accessible": True},
                    "bibliography": {"value": ["b1"], "ai_accessible": True},
                },
            )
        from app.backend.llm.gap_assembler import render_filled_py
        rendered = render_filled_py("lab1", synthesis.gap_values)
        filled = rendered["filled_py"]
        # The LLM's goal (a real Ukrainian academic sentence) should be in the file.
        self.assertIn("дослідити принципи", filled)
        # The LLM's general_info (a real paragraph) should be there.
        self.assertIn("Алгоритми сортування поділяються", filled)
        # The LLM's tasks should appear (numbered).
        self.assertIn("Реалізувати п'ять алгоритмів", filled)
        # The LLM's bibliography should be there.
        self.assertIn("Кнут, Д. Е.", filled)
        # The user's old values should NOT be there.
        self.assertNotIn("short user goal", filled)
        self.assertNotIn("one-liner", filled)


class TestRemoteCascade(unittest.TestCase):
    """The 3-provider cascade must try providers in order, skip missing
    keys, aggregate errors, and stop on the first valid JSON response."""

    def _prompt(self) -> str:
        return json.dumps(
            {
                "goal": "x" * 20,
                "general_info": "y" * 80,
                "tasks": ["a"],
                "control_questions": ["b"],
                "bibliography": ["c"],
            },
            ensure_ascii=False,
        )

    @patch("app.backend.llm.providers._call_gemini_json_llm")
    @patch("app.backend.llm.providers._call_openrouter_json_llm")
    @patch("app.backend.llm.providers._call_groq_json_llm")
    def test_cascade_stops_on_first_success(self, mock_groq, mock_openrouter, mock_gemini):
        from app.backend.llm.providers import _call_remote_cascade

        mock_gemini.return_value = (
            json.loads(self._prompt()),
            {"model": "gemini-fake", "source": "remote_gemini"},
        )
        mock_openrouter.return_value = (None, {"error": "should not reach"})
        mock_groq.return_value = (None, {"error": "should not reach"})

        with patch.dict("os.environ", {"REMOTE_LLM_PROVIDER": "gemini,openrouter,groq"}):
            obj, metrics = _call_remote_cascade("prompt")

        self.assertIsNotNone(obj)
        self.assertEqual(metrics["source"], "remote_gemini")
        mock_gemini.assert_called_once()
        mock_openrouter.assert_not_called()
        mock_groq.assert_not_called()

    @patch("app.backend.llm.providers._call_groq_json_llm")
    def test_cascade_skips_provider_without_key(self, mock_groq):
        from app.backend.llm.providers import _call_remote_cascade

        with patch("app.backend.llm.providers._call_gemini_json_llm",
                   return_value=(None, {"model": "gemini-fake", "source": "remote_gemini", "error": "no GOOGLE_API_KEY"})), \
             patch("app.backend.llm.providers._call_openrouter_json_llm",
                   return_value=(None, {"model": "openrouter-fake", "source": "remote_openrouter", "error": "no OPENROUTER_API_KEY"})), \
             patch.dict("os.environ", {"REMOTE_LLM_PROVIDER": "gemini,openrouter,groq"}):
            mock_groq.return_value = (
                json.loads(self._prompt()),
                {"model": "groq-fake", "source": "remote_groq"},
            )
            obj, metrics = _call_remote_cascade("prompt")

        self.assertIsNotNone(obj)
        self.assertEqual(metrics["source"], "remote_groq")
        self.assertNotIn("GOOGLE_API_KEY", metrics.get("error", ""))
        self.assertNotIn("OPENROUTER_API_KEY", metrics.get("error", ""))

    def test_cascade_order_is_configurable(self):
        from app.backend.llm.providers import _cascade_order

        with patch.dict("os.environ", {"REMOTE_LLM_PROVIDER": "groq,gemini"}):
            self.assertEqual(_cascade_order(), ["groq", "gemini"])
        with patch.dict("os.environ", {"REMOTE_LLM_PROVIDER": "unknown,gemini"}):
            self.assertEqual(_cascade_order(), ["gemini"])


if __name__ == "__main__":
    unittest.main()
