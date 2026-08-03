#!/usr/bin/env python3
"""Menu rendering, navigation and responsiveness.

The reported bug: opening the session browser and pressing ↓ redrew the list
*below* itself instead of over itself, so the same session appeared dozens of
times with several stale `▶` cursors. It happened because `arrow_menu` rewound
the cursor by one row per item, while a session entry is two rows.

Asserting on raw escape sequences would only re-state the implementation, so
these tests replay the output through a small virtual terminal and assert on
what the user would actually see.
"""

import io
import contextlib
import os
import re
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent_cli
import net_probe
from text_display import StreamWrap, display_width, term_columns


# ── A terminal you can assert against ────────────────────────────────────

class VirtualTerminal:
    """Applies the escape sequences the TUI emits and keeps the visible text.

    Supports exactly the subset the menus use: newline, `\\033[2K` (erase
    line), `\\033[{n}A` (cursor up), `\\033[J` (erase to end of screen) and
    the clear-screen sequence.
    """

    _ESCAPE = re.compile(r'\x1b\[([0-9;]*)([A-Za-z])')

    def __init__(self):
        self.rows: list[str] = ['']
        self.cur = 0
        # Widest row ever drawn, not merely the widest still on screen. The
        # prompt erases its hint line before returning, so anything that
        # asserts on the final screen cannot see the row that overflowed.
        self.widest = 0

    def _ensure(self, idx: int) -> None:
        while len(self.rows) <= idx:
            self.rows.append('')

    def write(self, data: str) -> None:
        i = 0
        while i < len(data):
            m = self._ESCAPE.match(data, i)
            if m:
                arg, code = m.group(1), m.group(2)
                n = int(arg) if arg.isdigit() else 0
                if code == 'A':
                    self.cur = max(0, self.cur - max(1, n))
                elif code == 'B':
                    # The prompt writes its hint on the line below and comes
                    # back up; without 'B' the two cancel out and the test
                    # would not see where the hint actually landed.
                    self.cur += max(1, n)
                    self._ensure(self.cur)
                elif code == 'H':
                    self.cur = 0
                elif code == 'K' and n == 2:
                    self._ensure(self.cur)
                    self.rows[self.cur] = ''
                elif code == 'J':
                    if n in (2, 3):          # whole screen
                        self.rows, self.cur = [''], 0
                    else:                     # cursor to end of screen
                        self._ensure(self.cur)
                        del self.rows[self.cur + 1:]
                        self.rows[self.cur] = ''
                i = m.end()
                continue
            ch = data[i]
            if ch == '\n':
                self.cur += 1
                self._ensure(self.cur)
            elif ch == '\r':
                self._ensure(self.cur)
                self.rows[self.cur] = ''
            else:
                self._ensure(self.cur)
                self.rows[self.cur] += ch
                from text_display import display_width
                self.widest = max(self.widest, display_width(self.rows[self.cur]))
            i += 1

    def flush(self):
        pass

    def isatty(self):
        return False

    @property
    def text(self) -> str:
        from text_display import strip_ansi
        return '\n'.join(strip_ansi(r) for r in self.rows)


def drive_menu(items, keys, **kwargs):
    """Run arrow_menu against a scripted key sequence. Returns (index, screen)."""
    screen = VirtualTerminal()
    supplied = iter(keys)
    original_get_key, original_stdout = agent_cli.get_key, sys.stdout
    agent_cli.get_key = lambda: next(supplied)
    sys.stdout = screen
    try:
        idx = agent_cli.arrow_menu('T', items, **kwargs)
    finally:
        agent_cli.get_key = original_get_key
        sys.stdout = original_stdout
    return idx, screen


class TestRedrawDuplication(unittest.TestCase):
    """The reported bug, and the general case behind it."""

    def test_multiline_items_do_not_duplicate(self):
        # Shaped like the session browser: every entry is label + summary.
        items = [f'  {n}. session-{n}\n      Started: hello' for n in range(1, 6)]
        _, screen = drive_menu(items, ['DOWN', 'DOWN', 'DOWN', 'ENTER'])
        for n in range(1, 6):
            self.assertEqual(
                screen.text.count(f'{n}. session-{n}'), 1,
                f'session-{n} was drawn more than once:\n{screen.text}')

    def test_only_one_cursor_is_visible(self):
        items = [f'  {n}. session-{n}\n      Started: hello' for n in range(1, 6)]
        _, screen = drive_menu(items, ['DOWN', 'DOWN', 'ENTER'])
        self.assertEqual(screen.text.count('▶'), 1, screen.text)

    def test_wrapped_long_labels_do_not_duplicate(self):
        # The other way an item exceeds one row: a label wider than the window.
        wide = 'x' * (term_columns() + 40)
        items = [f'  {n}. {wide}' for n in range(1, 5)]
        _, screen = drive_menu(items, ['DOWN', 'DOWN', 'ENTER'])
        for n in range(1, 5):
            self.assertEqual(screen.text.count(f'{n}. xxx'), 1, screen.text)

    def test_no_line_exceeds_the_terminal(self):
        """Nothing may soft-wrap, or the row arithmetic stops being knowable."""
        wide = 'y' * (term_columns() + 80)
        _, screen = drive_menu([f'  {wide}', '  second'], ['DOWN', 'ENTER'])
        for row in screen.rows:
            self.assertLessEqual(display_width(row), term_columns())

    def test_selection_survives_many_moves(self):
        items = [f'  item-{n}\n      detail' for n in range(1, 9)]
        idx, screen = drive_menu(items, ['DOWN'] * 20 + ['UP'] * 7 + ['ENTER'])
        self.assertTrue(0 <= idx < len(items))
        self.assertEqual(screen.text.count('▶'), 1)
        for n in range(1, 9):
            self.assertLessEqual(screen.text.count(f'item-{n}\n'), 1)


class TestRowCounting(unittest.TestCase):

    def test_plain_item_is_one_row(self):
        self.assertEqual(agent_cli.menu_row_count('hello', 80), 1)

    def test_embedded_newline_adds_a_row(self):
        self.assertEqual(agent_cli.menu_row_count('a\nb', 80), 2)

    def test_overlong_item_counts_its_wrapped_rows(self):
        self.assertEqual(agent_cli.menu_row_count('z' * 100, 50), 2)
        self.assertEqual(agent_cli.menu_row_count('z' * 101, 50), 3)

    def test_ansi_colour_is_not_counted_as_width(self):
        coloured = f'{agent_cli.GREEN}{"z" * 40}{agent_cli.RESET}'
        self.assertEqual(agent_cli.menu_row_count(coloured, 50), 1)

    def test_empty_item_still_occupies_a_row(self):
        self.assertEqual(agent_cli.menu_row_count('', 80), 1)


class TestNavigation(unittest.TestCase):

    def test_blank_separators_are_never_selected(self):
        items = ['  first', '', '  second', '', '  third']
        for presses in range(1, 6):
            idx, _ = drive_menu(items, ['DOWN'] * presses + ['ENTER'])
            self.assertNotEqual(items[idx].strip(), '',
                                f'landed on a blank after {presses} moves')

    def test_escape_cancels(self):
        idx, _ = drive_menu(['  a', '  b'], ['ESC'])
        self.assertEqual(idx, -1)

    def test_left_arrow_also_cancels(self):
        idx, _ = drive_menu(['  a', '  b'], ['LEFT'])
        self.assertEqual(idx, -1)

    def test_home_and_end(self):
        items = [f'  item-{n}' for n in range(10)]
        idx, _ = drive_menu(items, ['DOWN', 'DOWN', 'HOME', 'ENTER'])
        self.assertEqual(idx, 0)
        idx, _ = drive_menu(items, ['END', 'ENTER'])
        self.assertEqual(idx, len(items) - 1)

    def test_digit_jumps_to_that_row(self):
        items = [f'  item-{n}' for n in range(9)]
        idx, _ = drive_menu(items, ['3', 'ENTER'])
        self.assertEqual(idx, 2)

    def test_page_down_moves_further_than_one(self):
        items = [f'  item-{n}' for n in range(40)]
        one, _ = drive_menu(items, ['DOWN', 'ENTER'])
        page, _ = drive_menu(items, ['PGDN', 'ENTER'])
        self.assertGreater(page, one)

    def test_filter_narrows_and_returns_the_original_index(self):
        items = ['  alpha', '  beta', '  gamma']
        idx, _ = drive_menu(items, ['/', 'g', 'a', 'ENTER', 'ENTER'])
        self.assertEqual(items[idx].strip(), 'gamma')

    def test_filter_accepts_cyrillic(self):
        items = ['  Файли', '  Налаштування', '  Вихід']
        idx, _ = drive_menu(items, ['/', 'в', 'и', 'ENTER', 'ENTER'])
        self.assertEqual(items[idx].strip(), 'Вихід')

    def test_empty_menu_returns_cancelled(self):
        self.assertEqual(agent_cli.arrow_menu('T', []), -1)

    def test_wrapping_moves_between_ends(self):
        items = ['  a', '  b', '  c']
        idx, _ = drive_menu(items, ['UP', 'ENTER'])
        self.assertEqual(idx, 2)


class TestClearScreen(unittest.TestCase):

    def test_uses_ansi_and_never_spawns_a_process(self):
        calls = []
        original_system, original_stdout = os.system, sys.stdout
        os.system = lambda cmd: calls.append(cmd)
        screen = VirtualTerminal()
        sys.stdout = screen
        try:
            agent_cli.clear_screen()
        finally:
            os.system, sys.stdout = original_system, original_stdout
        self.assertEqual(calls, [], 'clear_screen shelled out')
        self.assertEqual(screen.rows, [''])

    def test_is_fast(self):
        buf = io.StringIO()
        start = time.perf_counter()
        with contextlib.redirect_stdout(buf):
            for _ in range(50):
                agent_cli.clear_screen()
        self.assertLess(time.perf_counter() - start, 0.5)


class TestKeyDecoding(unittest.TestCase):

    def _key(self, chars):
        import msvcrt
        supplied = iter(chars)
        original = getattr(msvcrt, 'getwch', None)
        msvcrt.getwch = lambda: next(supplied)
        try:
            return agent_cli.get_key()
        finally:
            if original:
                msvcrt.getwch = original

    def test_arrow_keys(self):
        self.assertEqual(self._key(['\xe0', 'H']), 'UP')
        self.assertEqual(self._key(['\xe0', 'P']), 'DOWN')
        self.assertEqual(self._key(['\xe0', 'I']), 'PGUP')
        self.assertEqual(self._key(['\xe0', 'Q']), 'PGDN')

    def test_named_keys(self):
        self.assertEqual(self._key(['\r']), 'ENTER')
        self.assertEqual(self._key(['\x1b']), 'ESC')
        self.assertEqual(self._key(['\x08']), 'BACKSPACE')

    def test_cyrillic_character_survives(self):
        """`getch` returned one byte, so this used to arrive as '?'."""
        self.assertEqual(self._key(['п']), 'п')


class TestProbeBounds(unittest.TestCase):

    def test_dead_port_answers_within_its_budget(self):
        net_probe.invalidate()
        start = time.perf_counter()
        self.assertFalse(net_probe.port_open('127.0.0.1', 9))
        self.assertLess(time.perf_counter() - start, 1.0)

    def test_answer_is_cached(self):
        net_probe.invalidate()
        net_probe.port_open('127.0.0.1', 9)
        start = time.perf_counter()
        net_probe.port_open('127.0.0.1', 9)
        self.assertLess(time.perf_counter() - start, 0.01)

    def test_remote_hosts_are_not_probed(self):
        """Only local endpoints are gated; a remote URL must pass through."""
        start = time.perf_counter()
        self.assertTrue(net_probe.url_port_open('https://api.anthropic.com'))
        self.assertLess(time.perf_counter() - start, 0.05)

    def test_invalidate_clears(self):
        net_probe.put('k', 123)
        self.assertEqual(net_probe.peek('k', 60)[1], 123)
        net_probe.invalidate('k')
        self.assertFalse(net_probe.peek('k', 60)[0])

    def test_zen_status_is_bounded(self):
        """The single most expensive call in the menus, before this change."""
        from zen_proxy import check_status
        net_probe.invalidate()
        start = time.perf_counter()
        check_status(9)
        self.assertLess(time.perf_counter() - start, 1.0)


class TestStreamWrap(unittest.TestCase):

    def _render(self, text, width=40):
        w = StreamWrap(indent='  ', width=width)
        out = ''.join(w.feed(ch) for ch in text) + w.flush()
        return out

    def test_output_stays_within_the_width(self):
        text = 'the quick brown fox jumps over the lazy dog ' * 4
        for line in self._render(text, 40).split('\n'):
            self.assertLessEqual(display_width(line), 40)

    def test_no_word_is_broken(self):
        text = 'alpha beta gamma delta epsilon zeta eta theta iota kappa'
        rendered = self._render(text, 24)
        for word in text.split():
            self.assertIn(word, rendered)

    def test_chunk_boundaries_do_not_change_the_result(self):
        """A word split across two deltas must render as one word."""
        whole = StreamWrap(indent='  ', width=40)
        a = whole.feed('hello wor') + whole.feed('ld again') + whole.flush()
        self.assertIn('world', a)

    def test_explicit_newlines_are_preserved(self):
        self.assertEqual(self._render('one\ntwo', 40).count('\n'), 1)

    def test_code_fences_are_not_reflowed(self):
        code = '```\n' + 'x' * 80 + '\n```'
        rendered = self._render(code, 40)
        self.assertIn('x' * 80, rendered)


class TestShortenCarriesColour(unittest.TestCase):
    """`shorten` measures with ANSI stripped; it must truncate that way too.

    The truncation loop used to walk the raw string, charging a column for
    each byte of `\\x1b[92m`. A coloured row was cut several columns early and
    could be cut inside an escape — leaving `[9` on screen with the colour
    stuck on for the rest of the line.
    """

    GREEN, RESET = '\x1b[92m', '\x1b[0m'

    def test_visible_width_is_respected(self):
        from text_display import display_width, shorten
        coloured = f'{self.GREEN}{"x" * 60}{self.RESET}'
        self.assertLessEqual(display_width(shorten(coloured, 20)), 20)

    def test_colour_is_not_charged_as_width(self):
        """Colouring a string must not change how much of it survives."""
        from text_display import shorten, strip_ansi
        plain = 'y' * 60
        coloured = f'{self.GREEN}{plain}{self.RESET}'
        self.assertEqual(strip_ansi(shorten(coloured, 20)), shorten(plain, 20))

    def test_never_cuts_inside_an_escape(self):
        from text_display import shorten
        out = shorten(''.join(f'{self.GREEN}{c}{self.RESET}' for c in 'abcdefghij'), 4)
        for fragment in re.findall(r'\x1b\[[0-9;]*[A-Za-z]?', out):
            self.assertRegex(fragment, r'^\x1b\[[0-9;]*[A-Za-z]$')

    def test_truncated_colour_is_closed(self):
        """A cut row must not bleed its colour into the rest of the line."""
        from text_display import shorten
        self.assertTrue(shorten(f'{self.GREEN}{"z" * 60}', 10).endswith(self.RESET))

    def test_plain_text_is_unchanged(self):
        from text_display import shorten
        self.assertEqual(shorten('short', 40), 'short')
        self.assertFalse(shorten('a' * 60, 10).endswith(self.RESET))


class TestPromptHintFitsOnOneRow(unittest.TestCase):
    """The chat prompt's hint line must occupy exactly one row.

    `_show` writes the hint on the line below and returns with `\\033[1A`. With
    no matches the hint lists every slash command, which is far wider than any
    terminal: it soft-wrapped onto a second row while the rewind moved up only
    one, so the next redraw drew under the previous one — the menu duplication
    bug, in the chat.
    """

    def _type(self, text, columns=80):
        """Type `text` then Enter at the real prompt. Returns the screen."""
        import msvcrt
        import shutil
        import agent
        screen = VirtualTerminal()
        keys = iter(list(text) + ['\r'])
        saved = (msvcrt.getwch, sys.stdout, shutil.get_terminal_size)
        msvcrt.getwch = lambda: next(keys)
        sys.stdout = screen
        # term_columns reads this at call time, so the width under test is the
        # one the prompt will actually see.
        shutil.get_terminal_size = lambda *a, **k: os.terminal_size((columns, 24))
        try:
            agent.read_input_with_suggestions('> ')
        finally:
            msvcrt.getwch, sys.stdout, shutil.get_terminal_size = saved
        return screen

    def test_hint_with_no_matches_stays_within_the_terminal(self):
        # '/zzz' matches nothing, which is the branch that lists every command.
        self.assertLessEqual(self._type('/zzz', columns=80).widest, 80)

    def test_hint_with_matches_stays_within_the_terminal(self):
        self.assertLessEqual(self._type('/s', columns=60).widest, 60)

    def test_a_narrow_terminal_is_still_one_row(self):
        self.assertLessEqual(self._type('/', columns=40).widest, 40)

    def test_the_hint_is_actually_drawn(self):
        """Guards the three assertions above: a prompt that drew no hint at
        all would satisfy them trivially."""
        self.assertGreater(self._type('/', columns=80).widest, 20)


class TestMenuKeyOverlay(unittest.TestCase):
    """`?` opens the key list and returns to an intact menu."""

    ITEMS = ['alpha', 'beta', 'gamma']

    def test_overlay_lists_the_keys(self):
        screen = VirtualTerminal()
        saved_stdout, saved_get_key = sys.stdout, agent_cli.get_key
        sys.stdout = screen
        agent_cli.get_key = lambda: 'ENTER'
        try:
            agent_cli._show_menu_keys()
        finally:
            sys.stdout, agent_cli.get_key = saved_stdout, saved_get_key
        self.assertIn('Menu keys', screen.text)
        self.assertIn('filter the list', screen.text)

    # The overlay waits for a keypress of its own, so every script below
    # spends one key dismissing it.
    def test_menu_is_intact_after_the_overlay(self):
        """Each item appears once — the overlay must not leave a second copy."""
        idx, screen = drive_menu(self.ITEMS, ['?', 'ENTER', 'DOWN', 'ENTER'])
        self.assertEqual(idx, 1)
        for item in self.ITEMS:
            self.assertEqual(screen.text.count(item), 1, screen.text)

    def test_overlay_does_not_change_the_selection(self):
        idx, _ = drive_menu(self.ITEMS, ['DOWN', '?', 'ENTER', 'ENTER'])
        self.assertEqual(idx, 1)



class TestMultiLineHeader(unittest.TestCase):
    """A header entry can be several physical lines in one string --
    TOMAS_ART is the ASCII banner, built that way. shorten() measures a
    newline as zero-width and keeps consuming its column budget past it,
    so passing an unsplit multi-line block through one shorten() call
    truncated the whole banner down to a single line's worth of columns
    and cut it off mid-render with a trailing ellipsis -- draw_header now
    shortens each physical line on its own budget instead.
    """

    def test_a_multiline_header_is_not_truncated_as_one_line(self):
        # Ten lines of 60 columns is 600 visible columns total -- comfortably
        # past any real terminal width, which is what a shorten() call that
        # does not know about the embedded newlines would truncate against.
        banner = chr(10).join(['#' * 60 for _ in range(10)])
        _, screen = drive_menu(['item'], ['ENTER'],
                               header_lines=[banner], max_visible=5)
        for row in banner.split(chr(10)):
            self.assertIn(row, screen.text)
        self.assertNotIn(chr(8230), screen.text.split('item')[0])

    def test_each_physical_line_is_still_truncated_to_the_terminal(self):
        wide = '#' * 500
        banner = chr(10).join([wide, wide])
        _, screen = drive_menu(['item'], ['ENTER'],
                               header_lines=[banner], max_visible=5)
        for row in screen.rows:
            self.assertLessEqual(display_width(row), term_columns())

if __name__ == '__main__':
    unittest.main()
