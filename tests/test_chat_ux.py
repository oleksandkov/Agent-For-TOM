#!/usr/bin/env python3
"""
Chat experience (Phase 7, Part B).

Run: python -m unittest tests.test_chat_ux -v
"""
import sys
import time
import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent.resolve()
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import agent
import mcp_manager
import text_display as td
from core.events import PermissionNeeded
from core.state import AgentState


class _Denier:
    """A responder that always says no, and counts how often it is asked."""

    def __init__(self):
        self.asks = 0

    def ask(self, event) -> str:
        self.asks += 1
        return "deny"

    def ask_continue(self, event) -> bool:
        return False


# ══════════════════════════════════════════════════════════════════════
#  P7-7 — a denial must stop the retry loop
# ══════════════════════════════════════════════════════════════════════

class TestDenialSemantics(unittest.TestCase):

    def test_denial_says_a_retry_will_fail(self):
        """Regression: 'user denied this tool call' read as transient, so the
        model reissued the same command six times in one observed turn."""
        source = (PROJECT_DIR / "core" / "loop.py").read_text(encoding="utf-8")
        self.assertIn("will be denied", source)
        self.assertIn("Do not re-issue", source)

    def test_second_denial_escalates(self):
        source = (PROJECT_DIR / "core" / "loop.py").read_text(encoding="utf-8")
        self.assertIn("denials >= 2", source)

    def test_text_protocol_denial_also_says_so(self):
        source = (PROJECT_DIR / "agent.py").read_text(encoding="utf-8")
        self.assertIn("denied again — do not re-issue", source)

    def test_non_interactive_adapter_denies_without_prompting(self):
        from adapters.terminal import TerminalAdapter
        adapter = TerminalAdapter(interactive=False)
        event = PermissionNeeded("id", "run_command", {"command": "del x"}, "high")
        self.assertEqual(adapter.ask(event), "deny")

    def test_non_interactive_notice_is_shown_once(self):
        import io
        from adapters.terminal import TerminalAdapter
        adapter = TerminalAdapter(interactive=False)
        event = PermissionNeeded("id", "run_command", {"command": "del x"}, "high")
        saved, buf = sys.stdout, io.StringIO()
        sys.stdout = buf
        try:
            for _ in range(5):
                adapter.ask(event)
        finally:
            sys.stdout = saved
        self.assertEqual(buf.getvalue().count("non-interactive"), 1)


# ══════════════════════════════════════════════════════════════════════
#  P7-7 — the model is told which shell it has
# ══════════════════════════════════════════════════════════════════════

class TestEnvironmentAwareness(unittest.TestCase):

    def test_prompt_names_the_shell(self):
        """It reached for rm / ls / test -f on cmd.exe and burned tool calls
        discovering they do not exist."""
        prompt = agent.build_system_prompt("anything")
        self.assertIn("# Environment", prompt)
        if sys.platform == "win32":
            self.assertIn("cmd.exe", prompt)
            self.assertIn("findstr", prompt)

    def test_prompt_names_the_interpreter(self):
        self.assertIn(sys.executable, agent.build_system_prompt(""))

    def test_prompt_asks_for_the_user_s_language(self):
        self.assertIn("language the user wrote in",
                      agent.build_system_prompt(""))


# ══════════════════════════════════════════════════════════════════════
#  P7-3 — startup
# ══════════════════════════════════════════════════════════════════════

class _SlowServer:
    """A stub that takes `delay` seconds to connect."""

    def __init__(self, name, delay=0.4, ok=True, tools=None):
        self.name = name
        self.delay = delay
        self._ok = ok
        self.tools = tools if tools is not None else [
            {"name": f"{name}_tool", "inputSchema": {"type": "object"}}]
        self.resources, self.prompts = [], []
        self._last_error = None if ok else "stub failure"

    def connect(self):
        time.sleep(self.delay)
        return self._ok


class TestParallelConnect(unittest.TestCase):

    def _run(self, servers, parallel):
        by_name = {s.name: s for s in servers}
        original = mcp_manager.MCPServer
        mcp_manager.MCPServer = lambda name, cfg: by_name[name]
        try:
            mgr = mcp_manager.MCPManager()
            t0 = time.perf_counter()
            mgr.discover_and_connect(config={s.name: {} for s in servers},
                                     parallel=parallel)
            return mgr, time.perf_counter() - t0
        finally:
            mcp_manager.MCPServer = original

    def test_parallel_is_faster_than_serial(self):
        servers = [_SlowServer(f"s{i}", delay=0.3) for i in range(8)]
        _, t_par = self._run(servers, parallel=True)
        _, t_ser = self._run(servers, parallel=False)
        self.assertLess(t_par, t_ser / 2,
                        f"parallel {t_par:.2f}s vs serial {t_ser:.2f}s")

    def test_exposed_names_are_identical_either_way(self):
        """Determinism: which server wins an uncontested name must not depend
        on which thread finished first."""
        def servers():
            return [_SlowServer("a", 0.30, tools=[{"name": "dup", "inputSchema": {}}]),
                    _SlowServer("b", 0.05, tools=[{"name": "dup", "inputSchema": {}}]),
                    _SlowServer("c", 0.01, tools=[{"name": "dup", "inputSchema": {}}])]
        par, _ = self._run(servers(), parallel=True)
        ser, _ = self._run(servers(), parallel=False)
        self.assertEqual([t["name"] for t in par.tools],
                         [t["name"] for t in ser.tools])
        # The slowest server is first in config order, so it keeps the name.
        self.assertEqual(par.tools[0]["name"], "dup")
        self.assertEqual(par.get_server_for_tool("dup"), "a")

    def test_failures_are_recorded_not_raised(self):
        mgr, _ = self._run([_SlowServer("ok", 0.01),
                            _SlowServer("bad", 0.01, ok=False)], parallel=True)
        self.assertIn("bad", mgr.failed_servers)
        self.assertIn("ok", mgr.servers)

    def test_a_raising_server_does_not_kill_startup(self):
        class Exploding(_SlowServer):
            def connect(self):
                raise RuntimeError("boom")
        mgr, _ = self._run([_SlowServer("fine", 0.01), Exploding("boom", 0.01)],
                           parallel=True)
        self.assertIn("fine", mgr.servers)
        self.assertIn("boom", mgr.failed_servers)

    def test_no_duplicate_names_under_concurrency(self):
        servers = [_SlowServer(f"s{i}", 0.05,
                               tools=[{"name": "shared", "inputSchema": {}}])
                   for i in range(6)]
        mgr, _ = self._run(servers, parallel=True)
        names = [t["name"] for t in mgr.tools]
        self.assertEqual(len(names), len(set(names)))


# ══════════════════════════════════════════════════════════════════════
#  P7-8 — startup noise
# ══════════════════════════════════════════════════════════════════════

class TestFailureClassification(unittest.TestCase):

    def test_auth_failures_are_not_errors(self):
        auth, broken = agent._classify_mcp_failures({
            "github": "HTTP 401: Unauthorized",
            "supabase": "HTTP 403: Forbidden",
            "vercel": "authentication required",
            "toolbox": "initialize failed: None",
            "linear": "Expecting value: line 1 column 1",
        })
        self.assertEqual(sorted(auth), ["github", "supabase", "vercel"])
        self.assertEqual(sorted(broken), ["linear", "toolbox"])

    def test_empty_input(self):
        self.assertEqual(agent._classify_mcp_failures({}), ([], []))


# ══════════════════════════════════════════════════════════════════════
#  P7-6 — width awareness in the renderer
# ══════════════════════════════════════════════════════════════════════

class TestRendererWidth(unittest.TestCase):

    def test_rule_matches_the_terminal(self):
        self.assertEqual(td.display_width(td.rule(width=50, indent=2)), 48)

    def test_term_width_is_clamped(self):
        w = td.term_width()
        self.assertGreaterEqual(w, td.MIN_WIDTH)
        self.assertLessEqual(w, td.MAX_WIDTH)

    def test_renderer_imports_the_display_helpers(self):
        """One implementation of width, shared by the REPL and the renderer."""
        source = (PROJECT_DIR / "adapters" / "terminal.py").read_text(encoding="utf-8")
        self.assertIn("from text_display import", source)

    def test_assistant_text_is_wrapped(self):
        source = (PROJECT_DIR / "adapters" / "terminal.py").read_text(encoding="utf-8")
        self.assertIn("print(wrap(event.text))", source)


# ══════════════════════════════════════════════════════════════════════
#  The input line: multi-row, Shift+Enter, paste
# ══════════════════════════════════════════════════════════════════════

class FakeKeyboard:
    """Stands in for msvcrt.

    Each queued key records whether more input is waiting behind it, which is
    the signal the prompt uses to tell a paste from typing: characters the
    user types arrive one at a time, a paste is already sitting in the queue.
    """

    def __init__(self):
        self.keys: list[list] = []          # [char, more_queued, shift_held]
        self._last: list | None = None

    def type(self, text):
        for c in text:
            self.keys.append([c, False, False])
        return self

    def paste(self, text):
        chars = list(text)
        for i, c in enumerate(chars):
            self.keys.append([c, i < len(chars) - 1, False])
        return self

    def enter(self):
        self.keys.append(['\r', False, False])
        return self

    def key(self, ch):
        self.keys.append([ch, False, False])
        return self

    def shift_enter(self):
        self.keys.append(['\r', False, True])
        return self

    # ── the msvcrt surface ────────────────────────────────────────────
    def getwch(self):
        if not self.keys:
            raise AssertionError("prompt read past the scripted input")
        self._last = self.keys.pop(0)
        return self._last[0]

    def kbhit(self):
        return bool(self._last and self._last[1])

    @property
    def shift(self) -> bool:
        return bool(self._last and self._last[2])


def drive_prompt(keyboard):
    """Run the real prompt against scripted keys. Returns (result, screen).

    `screen.written` keeps every byte emitted, not just what survives on the
    final screen — a transient hint is erased by the next redraw, which is
    correct behaviour and invisible to `screen.text`.
    """
    from tests.test_menu_ui import VirtualTerminal

    class Recording(VirtualTerminal):
        def __init__(self):
            super().__init__()
            self.written = ""

        def write(self, data):
            self.written += data
            super().write(data)

    screen = Recording()
    real_stdout, real_shift = sys.stdout, agent._shift_down
    real_msvcrt = sys.modules.get("msvcrt")
    sys.modules["msvcrt"] = keyboard
    sys.stdout = screen
    agent._shift_down = lambda: keyboard.shift
    try:
        result = agent.read_input_with_suggestions("  TOMAS » ")
    finally:
        sys.stdout = real_stdout
        agent._shift_down = real_shift
        if real_msvcrt is not None:
            sys.modules["msvcrt"] = real_msvcrt
        else:                                   # pragma: no cover - non-Windows
            sys.modules.pop("msvcrt", None)
    return result, screen


class TestPromptSubmitsAndEdits(unittest.TestCase):

    def test_plain_enter_submits(self):
        result, _ = drive_prompt(FakeKeyboard().type("hello").enter())
        self.assertEqual(result, "hello")

    def test_backspace_deletes(self):
        result, _ = drive_prompt(FakeKeyboard().type("abc\x08").enter())
        self.assertEqual(result, "ab")

    def test_cyrillic_still_types(self):
        result, _ = drive_prompt(FakeKeyboard().type("Привіт").enter())
        self.assertEqual(result, "Привіт")


class TestQuitKeys(unittest.TestCase):
    """Ctrl+C is copy; leaving takes a deliberate Esc Esc.

    The terminal only forwards \\x03 when there is no selection to copy, so
    quitting on it turned the ordinary copy reflex into a coin flip: miss the
    selection and the session ended.
    """

    ESC, CTRL_C = '\x1b', '\x03'

    def test_ctrl_c_does_not_quit(self):
        kb = FakeKeyboard().type("kept").key(self.CTRL_C).type("typing on").enter()
        result, _ = drive_prompt(kb)
        self.assertEqual(result, "typing on")

    def test_ctrl_c_clears_the_line(self):
        kb = FakeKeyboard().type("scratch").key(self.CTRL_C).enter()
        result, _ = drive_prompt(kb)
        self.assertEqual(result, "")

    def test_two_escapes_on_an_empty_line_quit(self):
        kb = FakeKeyboard().key(self.ESC).key(self.ESC)
        with self.assertRaises(KeyboardInterrupt):
            drive_prompt(kb)

    def test_one_escape_does_not_quit(self):
        kb = FakeKeyboard().key(self.ESC).type("still here").enter()
        result, _ = drive_prompt(kb)
        self.assertEqual(result, "still here")

    def test_the_first_escape_clears_instead_of_arming(self):
        """With text on the line the first Esc has something to do, so it
        does that and leaves the exit unarmed."""
        kb = FakeKeyboard().type("draft").key(self.ESC).key(self.ESC).type("ok").enter()
        result, _ = drive_prompt(kb)
        self.assertEqual(result, "ok")

    def test_typing_between_escapes_disarms(self):
        kb = (FakeKeyboard().key(self.ESC).type("x").key(self.ESC)
              .key(self.ESC).key(self.ESC))
        with self.assertRaises(KeyboardInterrupt):
            drive_prompt(kb)          # only the final adjacent pair quits

    def test_the_second_escape_is_announced(self):
        kb = FakeKeyboard().key(self.ESC).type("no").enter()
        _, screen = drive_prompt(kb)
        self.assertIn("Esc again", screen.written)


class TestShiftEnterMakesANewline(unittest.TestCase):
    """`getwch` reports a decoded character and nothing about modifiers, so
    Shift+Enter and Enter were the same `\\r` and both submitted."""

    def test_shift_enter_does_not_submit(self):
        kb = FakeKeyboard().type("line one").shift_enter().type("line two").enter()
        result, _ = drive_prompt(kb)
        self.assertEqual(result, "line one\nline two")

    def test_several_newlines(self):
        kb = (FakeKeyboard().type("a").shift_enter().shift_enter()
              .type("b").enter())
        result, _ = drive_prompt(kb)
        self.assertEqual(result, "a\n\nb")

    def test_the_whole_buffer_is_on_screen(self):
        """A multi-row buffer must be readable — and so selectable — in full,
        not scrolled behind an ellipsis the way one row forced."""
        kb = FakeKeyboard().type("first").shift_enter().type("second").enter()
        _, screen = drive_prompt(kb)
        self.assertIn("first", screen.text)
        self.assertIn("second", screen.text)


class TestPasteIsTakenWhole(unittest.TestCase):
    """A paste replays as ordinary keystrokes, so every newline inside it hit
    the submit branch: pasting three paragraphs sent three half messages."""

    def test_newlines_inside_a_paste_do_not_submit(self):
        kb = FakeKeyboard().paste("alpha\nbeta\ngamma").enter()
        result, _ = drive_prompt(kb)
        self.assertEqual(result, "alpha\nbeta\ngamma")

    def test_crlf_is_one_break(self):
        kb = FakeKeyboard().paste("a\r\nb").enter()
        result, _ = drive_prompt(kb)
        self.assertEqual(result, "a\nb")

    def test_a_blank_line_survives(self):
        kb = FakeKeyboard().paste("a\r\n\r\nb").enter()
        result, _ = drive_prompt(kb)
        self.assertEqual(result, "a\n\nb")

    def test_paste_then_keep_typing(self):
        kb = FakeKeyboard().paste("pasted\ntext").type("!").enter()
        result, _ = drive_prompt(kb)
        self.assertEqual(result, "pasted\ntext!")

    def test_a_large_paste_collapses_on_screen_but_sends_in_full(self):
        big = "x" * (agent.PASTE_COLLAPSE_CHARS + 50)
        result, screen = drive_prompt(FakeKeyboard().paste(big).enter())
        self.assertEqual(result, big, "the model must still receive all of it")
        self.assertNotIn("x" * 80, screen.text, "the raw paste was drawn")
        self.assertIn("pasted", screen.text)

    def test_a_small_paste_is_shown_as_itself(self):
        result, screen = drive_prompt(FakeKeyboard().paste("short note").enter())
        self.assertEqual(result, "short note")
        self.assertIn("short note", screen.text)


class TestInputRowLayout(unittest.TestCase):
    """`hard_wrap` is the row arithmetic the redraw depends on."""

    def test_short_text_is_one_row(self):
        self.assertEqual(td.hard_wrap("hello", 80), ["hello"])

    def test_empty_still_occupies_a_row(self):
        self.assertEqual(td.hard_wrap("", 80), [""])

    def test_newlines_start_rows(self):
        self.assertEqual(td.hard_wrap("a\nb", 80), ["a", "b"])

    def test_overlong_text_wraps_by_column(self):
        self.assertEqual(td.hard_wrap("abcdef", 2), ["ab", "cd", "ef"])

    def test_no_row_exceeds_the_width(self):
        rows = td.hard_wrap("z" * 500 + "\n" + "y" * 90, 40)
        for row in rows:
            self.assertLessEqual(td.display_width(row), 40)

    def test_colour_is_not_charged_as_width(self):
        rows = td.hard_wrap(f"{td.RESET}abc", 3)
        self.assertEqual(len(rows), 1)

    def test_wide_characters_are_measured(self):
        rows = td.hard_wrap("日本語", 4)
        self.assertEqual(len(rows), 2)     # two columns each


class TestImageAttachment(unittest.TestCase):
    """Images named in a message become image blocks — but only where the
    provider's probe says they can be read. `Capabilities.vision` used to be
    a hardcoded False nothing ever set."""

    PIXEL = bytes.fromhex(
        "89504e470d0a1a0a0000000d494844520000000100000001080600000"
        "01f15c4890000000d4944415478da63fccf00000302010133c4d9d100000000"
        "49454e44ae426082")

    def setUp(self):
        self.img = PROJECT_DIR / f"_test_img_{id(self)}.png"
        self.img.write_bytes(self.PIXEL)
        self._caps = agent._active_capabilities

    def tearDown(self):
        self.img.unlink(missing_ok=True)
        agent._active_capabilities = self._caps

    def vision(self, supported: bool):
        from types import SimpleNamespace
        agent._active_capabilities = lambda: SimpleNamespace(vision=supported)

    def test_plain_text_is_unchanged(self):
        self.vision(True)
        self.assertEqual(agent.build_user_content("no pictures here"),
                         "no pictures here")

    def test_a_named_image_becomes_a_block(self):
        self.vision(True)
        content = agent.build_user_content(f"look at {self.img}")
        self.assertIsInstance(content, list)
        self.assertEqual(content[0]["type"], "image")
        self.assertEqual(content[0]["source"]["media_type"], "image/png")
        self.assertEqual(content[-1]["type"], "text")

    def test_the_text_still_travels_with_it(self):
        self.vision(True)
        content = agent.build_user_content(f"describe {self.img} please")
        self.assertIn("please", content[-1]["text"])

    def test_a_quoted_path_with_spaces_is_found(self):
        self.vision(True)
        spaced = PROJECT_DIR / f"_test img {id(self)}.png"
        spaced.write_bytes(self.PIXEL)
        try:
            content = agent.build_user_content(f'see "{spaced}"')
            self.assertIsInstance(content, list)
        finally:
            spaced.unlink(missing_ok=True)

    def test_a_text_only_model_gets_text(self):
        """Sending an image to a model without vision is a 400, not a
        degraded reply — so it must not be sent at all."""
        self.vision(False)
        content = agent.build_user_content(f"look at {self.img}")
        self.assertIsInstance(content, str)

    def test_a_missing_file_is_not_attached(self):
        self.vision(True)
        content = agent.build_user_content("see C:/nope/missing.png")
        self.assertIsInstance(content, str)

    def test_an_oversized_image_is_refused_not_sent(self):
        self.vision(True)
        big = PROJECT_DIR / f"_test_big_{id(self)}.png"
        big.write_bytes(b"\x89PNG" + b"\0" * (agent.MAX_IMAGE_BYTES + 1))
        try:
            content = agent.build_user_content(f"see {big}")
            self.assertIsInstance(content, str)
        finally:
            big.unlink(missing_ok=True)

    def test_non_image_paths_are_ignored(self):
        self.vision(True)
        content = agent.build_user_content("read agent.py and README.md")
        self.assertIsInstance(content, str)


CTRL_U, CTRL_W, CTRL_Y, CTRL_Z, CTRL_C = "\x15", "\x17", "\x19", "\x1a", "\x03"


class TestUndo(unittest.TestCase):
    """Ctrl+U and Ctrl+W each destroy a whole prompt in one keystroke.

    Before Ctrl+Z there was no way back from either: a long prompt cleared by
    a mistyped Ctrl+U had to be typed again from scratch.
    """

    def test_undo_restores_a_cleared_line(self):
        result, _ = drive_prompt(
            FakeKeyboard().type("привіт світ").key(CTRL_U).key(CTRL_Z).enter())
        self.assertEqual(result, "привіт світ")

    def test_undo_restores_a_deleted_word(self):
        result, _ = drive_prompt(
            FakeKeyboard().type("hello world").key(CTRL_W).key(CTRL_Z).enter())
        self.assertEqual(result, "hello world")

    def test_undo_restores_a_cancelled_line(self):
        result, _ = drive_prompt(
            FakeKeyboard().type("draft text").key(CTRL_C).key(CTRL_Z).enter())
        self.assertEqual(result, "draft text")

    def test_undo_steps_back_through_several_edits(self):
        result, _ = drive_prompt(
            FakeKeyboard().type("one").key(CTRL_U)
            .type("two").key(CTRL_U).key(CTRL_Z).key(CTRL_Z).enter())
        self.assertEqual(result, "one")

    def test_undo_on_a_fresh_line_says_so_and_does_not_crash(self):
        result, screen = drive_prompt(
            FakeKeyboard().type("xy").key(CTRL_Z).enter())
        self.assertEqual(result, "xy")
        self.assertIn("nothing to undo", screen.written)

    def test_typing_is_not_snapshotted_per_character(self):
        # Undoing one letter at a time is useless; the keystroke worth taking
        # back is the one that wiped the line.
        result, _ = drive_prompt(FakeKeyboard().type("abc").key(CTRL_Z).enter())
        self.assertEqual(result, "abc")

    def test_a_paste_can_be_undone(self):
        result, _ = drive_prompt(
            FakeKeyboard().type("keep ").paste("unwanted paste")
            .key(CTRL_Z).enter())
        self.assertEqual(result, "keep ")


class TestCopyTheLine(unittest.TestCase):
    """Ctrl+Y copies what is typed — including any earlier prompt via ↑."""

    def setUp(self):
        self.copied = []
        self._real = agent.put_clipboard_text
        agent.put_clipboard_text = lambda t: (self.copied.append(t), True)[1]

    def tearDown(self):
        agent.put_clipboard_text = self._real

    def test_it_copies_the_current_line(self):
        drive_prompt(FakeKeyboard().type("скопіюй це").key(CTRL_Y).enter())
        self.assertEqual(self.copied, ["скопіюй це"])

    def test_the_line_survives_being_copied(self):
        result, _ = drive_prompt(
            FakeKeyboard().type("still here").key(CTRL_Y).enter())
        self.assertEqual(result, "still here")

    def test_it_confirms_on_screen(self):
        _, screen = drive_prompt(
            FakeKeyboard().type("abc").key(CTRL_Y).enter())
        self.assertIn("copied", screen.written)

    def test_an_empty_line_copies_nothing(self):
        _, screen = drive_prompt(FakeKeyboard().key(CTRL_Y).enter())
        self.assertEqual(self.copied, [])
        self.assertIn("nothing to copy", screen.written)

    def test_a_collapsed_paste_is_copied_in_full(self):
        # The buffer shows "[#1 pasted N chars]"; the clipboard must get the
        # text, not the marker — same expansion the model receives on send.
        big = "x" * (agent.PASTE_COLLAPSE_CHARS + 20)
        drive_prompt(FakeKeyboard().paste(big).key(CTRL_Y).enter())
        self.assertEqual(self.copied, [big])

    def test_a_clipboard_failure_is_reported_not_swallowed(self):
        agent.put_clipboard_text = lambda t: False
        _, screen = drive_prompt(FakeKeyboard().type("abc").key(CTRL_Y).enter())
        self.assertIn("could not reach the clipboard", screen.written)


class TestKeyHelpIsComplete(unittest.TestCase):
    """A binding users cannot discover may as well not exist."""

    def test_the_new_keys_are_documented(self):
        listed = {name for name, _ in agent.KEY_HELP}
        self.assertIn("Ctrl+Z", listed)
        self.assertIn("Ctrl+Y", listed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
