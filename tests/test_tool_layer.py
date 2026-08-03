#!/usr/bin/env python3
"""
Regression tests for the tool layer (Phase 6).

Every test here corresponds to a defect observed in a real session under
~/.tomas/sessions/ and catalogued in docs/plan/PHASE-6-hardening-from-simulation.md.
The bug numbers refer to the three TOMAS_SIMULATION_REPORT files.

Run: python -m unittest tests.test_tool_layer -v
"""
import os
import sys
import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent.resolve()
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import agent

WINDOWS = sys.platform == "win32"


class ToolLayerTestCase(unittest.TestCase):
    """Scratch file inside the project, cleaned up per test."""

    def setUp(self):
        self.probe = PROJECT_DIR / f"_test_probe_{id(self)}.txt"

    def tearDown(self):
        self.probe.unlink(missing_ok=True)

    def write(self, content: str) -> str:
        self.probe.write_text(content, encoding="utf-8")
        return str(self.probe)


# ══════════════════════════════════════════════════════════════════════
#  P6-1 — the sandbox (bug 2)
# ══════════════════════════════════════════════════════════════════════

class TestSandbox(unittest.TestCase):

    def test_read_tomas_home_allowed(self):
        """The agent can read its own state.

        Session 20260803_102520 got `Error: path outside project` for a
        self-note and fell back to `del ... && type ...` — a high-risk shell
        command standing in for a read.
        """
        target = Path.home() / ".tomas" / "sessions"
        target.mkdir(parents=True, exist_ok=True)
        probe = target / "_test_sandbox_probe.json"
        probe.write_text('{"ok": true}', encoding="utf-8")
        try:
            out = agent.handle_read_file({"file_path": str(probe)})
            self.assertNotIn("Error:", out)
            self.assertIn("ok", out)
        finally:
            probe.unlink(missing_ok=True)

    def test_write_tomas_home_refused(self):
        """~/.tomas is readable but not writable by path tools."""
        target = Path.home() / ".tomas" / "_test_should_not_exist.md"
        out = agent.handle_write_file({"file_path": str(target), "content": "x"})
        self.assertTrue(out.startswith("Error:"), out)
        self.assertFalse(target.exists())

    def test_write_refusal_names_the_alternative(self):
        """A refusal the model can act on, not just a denial."""
        target = Path.home() / ".tomas" / "memory" / "_test.md"
        out = agent.handle_write_file({"file_path": str(target), "content": "x"})
        self.assertIn("save_memory", out)

    def test_edit_tomas_home_refused(self):
        sessions = list((Path.home() / ".tomas" / "sessions").glob("*.json"))
        if not sessions:
            self.skipTest("no session files on disk")
        out = agent.handle_edit_file({
            "file_path": str(sessions[0]), "old_string": "{", "new_string": "["})
        self.assertTrue(out.startswith("Error:"), out)

    def test_outside_both_roots_refused(self):
        outside = "C:/Windows/System32/drivers/etc/hosts" if WINDOWS else "/etc/hosts"
        out = agent.handle_read_file({"file_path": outside})
        self.assertTrue(out.startswith("Error:"), out)

    def test_safe_predicate(self):
        self.assertTrue(agent._safe(PROJECT_DIR / "agent.py"))
        self.assertTrue(agent._safe(PROJECT_DIR / "agent.py", write=True))
        self.assertTrue(agent._safe(agent.TOMAS_HOME / "sessions" / "x.json"))
        self.assertFalse(agent._safe(agent.TOMAS_HOME / "sessions" / "x.json", write=True))
        self.assertFalse(agent._safe(Path("C:/" if WINDOWS else "/") / "nowhere"))


# ══════════════════════════════════════════════════════════════════════
#  P6-2 / P6-14 / P6-3 — run_command (bugs 1, 6, 8, 9)
# ══════════════════════════════════════════════════════════════════════

class TestRunCommand(unittest.TestCase):

    def test_exit_code_surfaced_with_stdout(self):
        """A command that fails while printing must not look like success.

        The old handler only mentioned the exit code when output was empty,
        so `python -c "print(m.dot(...))"` raising AttributeError read as an
        ordinary result. Session 20260803_105901 burned 957s on this.
        """
        out = agent.handle_run_command(
            {"command": 'python -c "import sys; print(\'out\'); sys.exit(3)"'})
        self.assertIn("out", out)
        self.assertIn("exit 3", out)
        self.assertIn("FAILED", out)

    def test_exit_zero_reported_as_ok(self):
        out = agent.handle_run_command({"command": 'python -c "print(1)"'})
        self.assertIn("exit 0", out)
        self.assertIn("ok", out)

    def test_stderr_is_labelled(self):
        out = agent.handle_run_command(
            {"command": 'python -c "import sys; sys.stderr.write(\'boom\')"'})
        self.assertIn("[stderr]", out)
        self.assertIn("boom", out)

    def test_run_command_utf8_roundtrip(self):
        """Non-ASCII output must survive.

        Session 20260803_121648 read back a UTF-8 self-note through the shell
        and got `MCP Subsystem Integrity вЂ" Verified` — cp1251 decoding — then
        summarised the note from the mangled text.
        """
        out = agent.handle_run_command(
            {"command": 'python -c "print(\'\\u2014\\u2192\\u00e9\')"'})
        self.assertIn("\u2014\u2192\u00e9", out)

    @unittest.skipUnless(WINDOWS, "cmd.exe quoting behaviour")
    def test_multiline_python_c_via_tempfile(self):
        """cmd.exe cannot carry newlines through a -c payload (bug 1)."""
        out = agent.handle_run_command(
            {"command": 'python -c "import sys\nprint(\'first\')\nprint(\'second\')"'})
        self.assertIn("first", out)
        self.assertIn("second", out)
        self.assertIn("exit 0", out)

    @unittest.skipUnless(WINDOWS, "cmd.exe quoting behaviour")
    def test_nested_quotes_via_tempfile(self):
        """Nested single quotes inside a double-quoted payload (bug 9)."""
        out = agent.handle_run_command(
            {"command": 'python -c "d = {\'k\': \'nested ok\'}\nprint(d[\'k\'])"'})
        self.assertIn("nested ok", out)

    @unittest.skipUnless(WINDOWS, "unbuffering only applied on win32")
    def test_windows_python_c_unbuffered(self):
        """`-u` is injected so cmd.exe does not swallow stdout (bug 6)."""
        cmd, tmp = agent._normalise_windows_command('python -c "print(1)"')
        self.assertIn("-u -c", cmd)
        self.assertIsNone(tmp)

    def test_no_temp_files_left_in_project(self):
        """Scratch files go to a temp dir, never the source tree (bug 8).

        Four turned up in the repo root across the corpus
        (_tmp_create_note.py, _debug_pdf.py, _create_note.py, _verify_cap.py),
        and one named test_*.py would be collected by `unittest discover`.
        """
        before = {p.name for p in PROJECT_DIR.iterdir()}
        agent.handle_run_command(
            {"command": 'python -c "import sys\nprint(\'x\')"'})
        after = {p.name for p in PROJECT_DIR.iterdir()}
        self.assertEqual(before, after, f"leaked: {sorted(after - before)}")

    def test_blocked_pattern_still_blocked(self):
        out = agent.handle_run_command({"command": "rm -rf / --no-preserve-root"})
        self.assertIn("blocked", out)

    def test_timeout_reported(self):
        out = agent.handle_run_command(
            {"command": 'python -c "import time; time.sleep(5)"', "timeout": 1})
        self.assertIn("timed out", out)


# ══════════════════════════════════════════════════════════════════════
#  P6-12 — search_code
# ══════════════════════════════════════════════════════════════════════

class TestSearchCode(ToolLayerTestCase):

    def test_search_code_accepts_file_path(self):
        """Pointing at a file must search it, not silently find nothing.

        Session 20260803_121648 asked for `mcp_` in agent.py, got
        "No matches", and escalated to a shell `findstr` — three calls for
        one grep, and a confident false negative in between.
        """
        path = self.write("alpha\nbeta\ngamma\n")
        out = agent.handle_search_code({"pattern": "beta", "path": path})
        self.assertNotIn("No matches", out)
        self.assertIn("beta", out)

    def test_search_code_file_matches_directory_search(self):
        path = self.write("needle\nhay\nneedle\n")
        direct = agent.handle_search_code({"pattern": "needle", "path": path})
        self.assertIn("2 matches", direct)

    def test_search_code_missing_path_errors(self):
        """An unsearchable path is an error, not a clean negative."""
        out = agent.handle_search_code(
            {"pattern": "x", "path": "no_such_file_anywhere.py"})
        self.assertTrue(out.startswith("Error:"), out)
        self.assertNotIn("No matches", out)

    def test_search_code_reports_true_total(self):
        self.write("\n".join(f"line {i} match" for i in range(120)))
        out = agent.handle_search_code({"pattern": "match", "path": str(self.probe)})
        self.assertIn("120 matches", out)
        self.assertIn("offset=", out)

    def test_search_code_offset_pages(self):
        self.write("\n".join(f"row{i} hit" for i in range(120)))
        page2 = agent.handle_search_code(
            {"pattern": "hit", "path": str(self.probe), "offset": 50})
        self.assertIn("row50", page2)
        self.assertNotIn("row0 hit", page2)

    def test_search_code_genuine_negative(self):
        self.write("nothing here\n")
        out = agent.handle_search_code(
            {"pattern": "absent", "path": str(self.probe)})
        self.assertIn("No matches", out)

    def test_search_code_invalid_regex_errors(self):
        out = agent.handle_search_code({"pattern": "[unclosed", "path": "agent.py"})
        self.assertTrue(out.startswith("Error:"), out)


# ══════════════════════════════════════════════════════════════════════
#  P6-13 — edit_file
# ══════════════════════════════════════════════════════════════════════

class TestEditFile(ToolLayerTestCase):

    def test_edit_file_replace_all(self):
        """One call for a mechanical substitution.

        Session 20260803_114827 turn 3 spent nine sequential edit_file calls
        replacing print() with log() — ~11 minutes at the measured mean.
        """
        path = self.write("print(1)\nprint(2)\nprint(3)\n")
        out = agent.handle_edit_file({
            "file_path": path, "old_string": "print(",
            "new_string": "log(", "replace_all": True})
        self.assertIn("3 replacements", out)
        self.assertEqual(self.probe.read_text(encoding="utf-8"),
                         "log(1)\nlog(2)\nlog(3)\n")

    def test_ambiguous_match_still_errors_by_default(self):
        """Ambiguity must keep failing loudly when replace_all is not set."""
        path = self.write("a\na\n")
        out = agent.handle_edit_file(
            {"file_path": path, "old_string": "a", "new_string": "b"})
        self.assertTrue(out.startswith("Error:"), out)
        self.assertIn("2 locations", out)
        self.assertEqual(self.probe.read_text(encoding="utf-8"), "a\na\n")

    def test_error_mentions_replace_all(self):
        path = self.write("z\nz\n")
        out = agent.handle_edit_file(
            {"file_path": path, "old_string": "z", "new_string": "y"})
        self.assertIn("replace_all", out)

    def test_unique_edit_unaffected(self):
        path = self.write("only once\n")
        out = agent.handle_edit_file(
            {"file_path": path, "old_string": "once", "new_string": "twice"})
        self.assertIn("Successfully edited", out)
        self.assertNotIn("replacements", out)

    def test_missing_string_errors(self):
        path = self.write("abc\n")
        out = agent.handle_edit_file(
            {"file_path": path, "old_string": "xyz", "new_string": "q",
             "replace_all": True})
        self.assertIn("not found", out)

    def test_schema_exposes_replace_all(self):
        """The parameter is unusable if the model cannot see it."""
        schema = next(t for t in agent.TOOLS if t["name"] == "edit_file")
        self.assertIn("replace_all", schema["input_schema"]["properties"])


# ══════════════════════════════════════════════════════════════════════
#  P6-4 — per-command risk (bug 3)
# ══════════════════════════════════════════════════════════════════════

class TestRiskClassifier(unittest.TestCase):

    def risk(self, cmd: str) -> str:
        return agent.risk_for("run_command", {"command": cmd})

    def test_readonly_command_is_low_risk(self):
        for cmd in ("git status", "git log --oneline", "dir",
                    "python -m unittest discover -s tests",
                    ".venv\\Scripts\\python.exe -m unittest discover",
                    "pytest", "type note.md", "pip list",
                    'findstr /n /c:"mcp_" agent.py'):
            self.assertEqual(self.risk(cmd), "low", cmd)

    def test_chained_command_stays_high_risk(self):
        for cmd in ("git status && rm -rf build",
                    "python _create_note.py 2>&1 && del _create_note.py",
                    "echo hi > file.txt", "cat a | tee b"):
            self.assertEqual(self.risk(cmd), "high", cmd)

    def test_single_ampersand_is_high_risk(self):
        """cmd.exe treats a lone & as a separator; the corpus contains
        `dir /b note-* & echo --- & type note-...`."""
        self.assertEqual(self.risk("dir /b note-* & echo --- & type note.md"), "high")

    def test_mutating_commands_are_high_risk(self):
        for cmd in ("del temp_lifecycle_test.py", "rm probe.py",
                    "move a b", "curl http://example.com",
                    "python setup.py install"):
            self.assertEqual(self.risk(cmd), "high", cmd)

    def test_other_tools_use_the_table(self):
        self.assertEqual(agent.risk_for("read_file", {}), "low")
        self.assertEqual(agent.risk_for("edit_file", {}), "medium")
        self.assertEqual(agent.risk_for("unknown_tool", {}), "high")

    def test_missing_params_is_safe(self):
        self.assertEqual(agent.risk_for("run_command", None), "high")
        self.assertEqual(agent.risk_for("run_command", {}), "high")


# ══════════════════════════════════════════════════════════════════════
#  P6-5 — context budget
# ══════════════════════════════════════════════════════════════════════

class TestContextBudget(ToolLayerTestCase):

    def test_read_file_char_cap(self):
        """A line limit is not a size limit: one 2000-line read put 45 KB
        into the context in a single tool result."""
        self.write(("x" * 200 + "\n") * 500)   # ~100 KB, 500 lines
        out = agent.handle_read_file({"file_path": str(self.probe)})
        self.assertLessEqual(len(out), agent.MAX_READ_FILE_CHARS + 300)

    def test_read_file_truncation_is_resumable(self):
        self.write(("y" * 200 + "\n") * 500)
        out = agent.handle_read_file({"file_path": str(self.probe)})
        self.assertIn("re-read with offset=", out)

    def test_small_file_not_truncated(self):
        self.write("one\ntwo\n")
        out = agent.handle_read_file({"file_path": str(self.probe)})
        self.assertNotIn("truncated", out)
        self.assertIn("two", out)

    def test_skills_section_within_budget(self):
        import skills_manager
        section = skills_manager.build_skills_section(max_chars=agent.MAX_SKILLS_CHARS)
        self.assertLessEqual(len(section), agent.MAX_SKILLS_CHARS)

    def test_skills_section_never_cut_mid_entry(self):
        import skills_manager
        section = skills_manager.build_skills_section(max_chars=600)
        for line in section.strip().splitlines():
            if line.startswith("- **"):
                self.assertIn("**:", line, f"entry cut in half: {line!r}")

    def test_legacy_generated_skills_not_discovered(self):
        """The 28 template skills crowded real ones out of the budget."""
        import skills_manager
        legacy = [s for s in skills_manager.discover_skills()
                  if "self-improve" in str(s.get("file", ""))]
        self.assertEqual(legacy, [])


# ══════════════════════════════════════════════════════════════════════
#  bug 4 — fpdf2 bullet overflow (fixed in session 105901, never tested)
# ══════════════════════════════════════════════════════════════════════

class TestPdfBulletWrap(unittest.TestCase):

    def test_bullet_wrap_at_right_margin(self):
        """A bullet long enough to wrap must not push x past the right margin.

        The agent found and fixed this itself (multi_cell left x at 208
        against r_margin 10). The fix is live and had no test, so the next
        fpdf2 upgrade would silently reintroduce it.
        """
        try:
            import pdf_report_skill
        except Exception as e:
            self.skipTest(f"pdf_report_skill unavailable: {e}")
        if not getattr(pdf_report_skill, "FPDF", None):
            self.skipTest("fpdf2 not installed")

        src = PROJECT_DIR / "latest_ai_news_report.txt"
        out = PROJECT_DIR / "_test_bullet_wrap.pdf"
        backup = src.read_text(encoding="utf-8") if src.exists() else None
        long_bullet = ("- " + "Anthropic ships a model with a longer context "
                       "window and stronger coding benchmarks. " * 3)
        try:
            src.write_text(f"AI News\n\n{long_bullet}\n", encoding="utf-8")
            pdf_report_skill.generate_ai_news_pdf(str(out))
            self.assertTrue(out.exists())
            self.assertGreater(out.stat().st_size, 0)
        finally:
            out.unlink(missing_ok=True)
            if backup is not None:
                src.write_text(backup, encoding="utf-8")
            else:
                src.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
