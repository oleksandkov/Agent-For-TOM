#!/usr/bin/env python3
"""
One skill format across bundled, user-installed and learned skills (P4-10).

With the format uniform, three things are the same code path: the user
installs a skill, the agent generates one, and the agent improves one it
already has.

Run: python -m unittest tests.test_skills_format -v
"""
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent.resolve()
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import skills_manager as sm


class SkillDirTestCase(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self._saved = sm.SKILL_DIRS
        sm.SKILL_DIRS = [self.dir]

    def tearDown(self):
        sm.SKILL_DIRS = self._saved
        self._tmp.cleanup()

    def write(self, name: str, text: str) -> Path:
        path = self.dir / name
        path.write_text(text, encoding="utf-8")
        return path


class TestFrontmatterContract(unittest.TestCase):

    def test_valid_frontmatter(self):
        meta, problems = sm.validate_frontmatter({
            "name": "ps-file-ops",
            "description": "How this user prefers file operations on Windows",
            "triggers": '["file", "directory", "powershell"]',
            "source": "learned", "version": "2"}, "fallback")
        self.assertEqual(problems, [])
        self.assertEqual(meta["name"], "ps-file-ops")
        self.assertEqual(meta["triggers"], ["file", "directory", "powershell"])
        self.assertEqual(meta["source"], "learned")
        self.assertEqual(meta["version"], 2)

    def test_bare_list_form(self):
        meta, _ = sm.validate_frontmatter({"triggers": "file, directory"}, "n")
        self.assertEqual(meta["triggers"], ["file", "directory"])

    def test_problems_are_reported_not_raised(self):
        meta, problems = sm.validate_frontmatter(
            {"source": "wat", "version": "abc"}, "n")
        self.assertIn("missing description", problems)
        self.assertTrue(any("wat" in p for p in problems))
        self.assertTrue(any("abc" in p for p in problems))
        self.assertEqual(meta["version"], 1)      # degraded to a usable default
        self.assertEqual(meta["source"], "")

    def test_render_round_trips(self):
        meta = {"name": "x", "description": "d", "triggers": ["a", "b"],
                "source": "user", "version": 3}
        rendered = sm.render_frontmatter(meta)
        body, parsed = sm.strip_yaml_frontmatter(rendered + "\nbody\n")
        back, problems = sm.validate_frontmatter(parsed, "x")
        self.assertEqual(problems, [])
        self.assertEqual(back["triggers"], ["a", "b"])
        self.assertEqual(back["version"], 3)
        self.assertEqual(body.strip(), "body")


class TestDiscovery(SkillDirTestCase):

    def test_malformed_frontmatter_does_not_crash_discovery(self):
        """A hand-written skill with a broken block must be reported, not
        fatal, and not silently dropped."""
        self.write("broken.md",
                   "---\nname: broken\nversion: not-a-number\nsource: wat\n---\n\nbody\n")
        self.write("good.md",
                   "---\nname: good\ndescription: fine\nversion: 1\n---\n\nbody\n")
        skills = sm.discover_skills()
        names = {s["name"] for s in skills}
        self.assertEqual(names, {"broken", "good"})
        broken = next(s for s in skills if s["name"] == "broken")
        self.assertTrue(broken["problems"])
        good = next(s for s in skills if s["name"] == "good")
        self.assertEqual(good["problems"], [])

    def test_skill_without_frontmatter_is_still_usable(self):
        self.write("bare.md", "just a body\n")
        skill = sm.find_skill("bare")
        self.assertIsNotNone(skill)
        self.assertEqual(skill["content"].strip(), "just a body")

    def test_bodies_are_not_read_at_discovery(self):
        """`load_skills_content` used to put every body in the prompt at
        once. Bodies load on demand now."""
        self.write("a.md", "---\nname: a\ndescription: d\n---\n\nBODY-A\n")
        skill = sm.discover_skills()[0]
        self.assertNotIn("content", dict.keys(skill))
        self.assertIn("BODY-A", skill["content"])
        self.assertIn("content", dict.keys(skill))    # cached after access

    def test_name_has_no_file_extension(self):
        self.write("my-skill.md", "---\ndescription: d\n---\n\nbody\n")
        self.assertEqual(sm.discover_skills()[0]["name"], "my-skill")

    def test_declared_name_wins_over_filename(self):
        self.write("file-name.md", "---\nname: declared-name\ndescription: d\n---\n\nb\n")
        self.assertEqual(sm.discover_skills()[0]["name"], "declared-name")

    def test_source_defaults_by_directory(self):
        self.write("u.md", "---\nname: u\ndescription: d\n---\n\nb\n")
        self.assertEqual(sm.discover_skills()[0]["source"], "user")

    def test_find_skill_by_stem_or_declared_name(self):
        self.write("thing.md", "---\nname: the-thing\ndescription: d\n---\n\nb\n")
        self.assertIsNotNone(sm.find_skill("thing"))
        self.assertIsNotNone(sm.find_skill("the-thing"))
        self.assertIsNone(sm.find_skill("absent"))


class TestImprove(SkillDirTestCase):
    """The agent improving a skill it already has — the third use of the
    same code path."""

    def setUp(self):
        super().setUp()
        sm.write_skill(self.dir / "ps-ops.md",
                       {"name": "ps-ops", "description": "windows file ops",
                        "triggers": ["file", "powershell"],
                        "source": "learned", "version": 1},
                       "Use forward slashes.")

    def test_version_bumps(self):
        sm.improve_skill("ps-ops", "Use -LiteralPath for bracketed names.")
        self.assertEqual(sm.find_skill("ps-ops")["version"], 2)

    def test_body_is_appended_not_replaced(self):
        sm.improve_skill("ps-ops", "SECOND LESSON")
        body = sm.find_skill("ps-ops")["content"]
        self.assertIn("Use forward slashes.", body)
        self.assertIn("SECOND LESSON", body)

    def test_provenance_is_kept(self):
        sm.improve_skill("ps-ops", "more")
        skill = sm.find_skill("ps-ops")
        self.assertEqual(skill["source"], "learned")
        self.assertEqual(skill["triggers"], ["file", "powershell"])

    def test_repeated_improvement_keeps_bumping(self):
        for i in range(3):
            sm.improve_skill("ps-ops", f"lesson {i}")
        self.assertEqual(sm.find_skill("ps-ops")["version"], 4)

    def test_improving_a_missing_skill_returns_none(self):
        self.assertIsNone(sm.improve_skill("does-not-exist", "x"))

    def test_learned_write_uses_the_same_format(self):
        """learning/reflect.py must not invent a second format."""
        from learning import reflect
        saved = reflect.SKILLS_DIR
        reflect.SKILLS_DIR = self.dir
        try:
            path = reflect.write_learned_skill(
                "new-lesson", "when editing powershell files", "prefer -LiteralPath")
            self.assertIsNotNone(path)
            skill = sm.find_skill("new-lesson")
            self.assertEqual(skill["source"], "learned")
            self.assertEqual(skill["version"], 1)
            self.assertTrue(skill["triggers"])

            # A second lesson for the same skill improves it rather than
            # overwriting what was already learned.
            reflect.write_learned_skill(
                "new-lesson", "when editing powershell files", "SECOND LESSON")
            improved = sm.find_skill("new-lesson")
            self.assertEqual(improved["version"], 2)
            self.assertIn("prefer -LiteralPath", improved["content"])
            self.assertIn("SECOND LESSON", improved["content"])
        finally:
            reflect.SKILLS_DIR = saved


class TestPromptBudget(SkillDirTestCase):

    def test_section_lists_names_not_bodies(self):
        for i in range(5):
            sm.write_skill(self.dir / f"s{i}.md",
                           {"name": f"s{i}", "description": f"desc {i}",
                            "source": "user", "version": 1},
                           "X" * 5000)
        section = sm.build_skills_section()
        self.assertIn("desc 0", section)
        self.assertNotIn("XXXXX", section)

    def test_budget_never_cuts_mid_entry(self):
        for i in range(40):
            sm.write_skill(self.dir / f"s{i}.md",
                           {"name": f"skill-number-{i}",
                            "description": "a fairly long description " * 3,
                            "source": "user", "version": 1}, "body")
        section = sm.build_skills_section(max_chars=800)
        self.assertLessEqual(len(section), 800)
        for line in section.strip().splitlines():
            if line.startswith("- **"):
                self.assertIn("**:", line)


if __name__ == "__main__":
    unittest.main(verbosity=2)
