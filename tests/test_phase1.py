#!/usr/bin/env python3
"""
Regression tests for Phase 1 — closing the learning loop (see docs/HISTORY.md).

Each test corresponds to something the self-improvement pipeline wrote but
never read back: generated skills, self-notes, and (separately) an honest
signal when memory truncation drops data instead of doing it silently.

Run: python -m unittest discover -s tests -p "test_*.py"
"""
import sys
import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent.resolve()
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import learning
import skills_manager
import self_notes


class TestGeneratedSkillsReachThePrompt(unittest.TestCase):
    """P1-1: a skill the agent wrote for itself must be discoverable."""

    def test_generated_skill_reaches_the_prompt(self):
        skill = skills_manager.LEARNED_SKILLS_DIR / "test-generated-skill.md"
        skill.parent.mkdir(parents=True, exist_ok=True)
        skill.write_text(
            "---\nname: test-generated-skill\n"
            "description: proves the loop is closed\n---\n\nbody\n",
            encoding="utf-8",
        )
        try:
            section = skills_manager.build_skills_section()
            self.assertIn("test-generated-skill", section)
            self.assertIn("learned from your past sessions", section)
        finally:
            skill.unlink()


class TestNotesReachThePrompt(unittest.TestCase):
    """P1-2: notes must be plain text, with body content, and no ANSI."""

    def setUp(self):
        self._notes_dir = self_notes.NOTES_DIR
        self._index = self_notes.NOTES_INDEX
        self._orig_notes_dir = self_notes.NOTES_DIR
        self._orig_index = self_notes.NOTES_INDEX
        self_notes.NOTES_DIR = PROJECT_DIR / "tests" / "_tmp_notes"
        self_notes.NOTES_INDEX = self_notes.NOTES_DIR / "index.json"

    def tearDown(self):
        import shutil
        shutil.rmtree(self_notes.NOTES_DIR, ignore_errors=True)
        self_notes.NOTES_DIR = self._orig_notes_dir
        self_notes.NOTES_INDEX = self._orig_index
        # The notes directory is sandboxed above, but create_note also bridges
        # into the learning store — which is not. Without this the suite leaves
        # "Use edit_file for existing files" in the user's real facts.jsonl,
        # where it spends retrieval budget in every real prompt forever.
        learning.purge_harness_probes()

    def test_empty_notes_return_empty_string(self):
        self.assertEqual(self_notes.get_notes_for_context(), "")

    def test_note_content_reaches_context_without_ansi(self):
        self_notes.create_note(
            title="Use edit_file for existing files",
            content="write_file overwrites silently; edit_file is safer for existing files.",
            note_type="lesson",
            evidence_tag=learning.HARNESS_EVIDENCE_TAG,
        )
        section = self_notes.get_notes_for_context()
        self.assertIn("Use edit_file for existing files", section)
        self.assertIn("write_file overwrites silently", section)
        self.assertNotIn("\x1b", section)

    def test_auto_generated_notes_are_excluded(self):
        self_notes.create_note(
            title="auto note", content="body", auto_generated=True,
            evidence_tag=learning.HARNESS_EVIDENCE_TAG,
        )
        self.assertEqual(self_notes.get_notes_for_context(), "")


if __name__ == "__main__":
    unittest.main()
