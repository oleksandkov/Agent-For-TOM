"""
Terminal text measurement and layout.

Every renderer in the codebase used a bare `print()` and a hard-coded
`'─' * 46`, so nothing wrapped, nothing aligned, and long output ran off the
right edge. Worse, `len()` is not width: CJK and emoji occupy two columns,
and combining marks occupy none — so any padding computed from `len()` is
wrong for exactly the users this phase is for.

One module, used by the REPL redraw and by the event renderer, so the two
cannot disagree about how wide a string is.
"""

from __future__ import annotations

import re
import shutil
import unicodedata

MIN_WIDTH = 60
MAX_WIDTH = 120
DEFAULT_WIDTH = 100

# Matches an ANSI SGR escape. Colour codes occupy no columns, so they must be
# stripped before measuring or every coloured string measures too wide.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

# Kept here rather than imported from adapters.ansi: this module is the one
# every renderer already depends on, and it must not depend on any of them.
RESET = "\x1b[0m"


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text or "")


def char_width(ch: str) -> int:
    """Columns one character occupies.

    Cyrillic, Greek and accented Latin are single-width — measured, not
    assumed — so Ukrainian and Russian need no special handling here. The
    two-column cases are CJK and emoji ('W'ide and 'F'ullwidth), and
    combining marks attach to the previous character and occupy nothing.
    """
    if unicodedata.combining(ch):
        return 0
    if ch == "\t":
        return 4
    if unicodedata.east_asian_width(ch) in ("W", "F"):
        return 2
    # C0/C1 controls render as nothing useful; treat them as zero-width.
    if unicodedata.category(ch) in ("Cc", "Cf"):
        return 0
    return 1


def display_width(text: str) -> int:
    """Columns a string occupies once ANSI colour is removed."""
    return sum(char_width(c) for c in strip_ansi(text or ""))


def term_width(default: int = DEFAULT_WIDTH) -> int:
    """Usable width, clamped so output stays readable on any terminal."""
    try:
        cols = shutil.get_terminal_size((default, 24)).columns
    except Exception:
        cols = default
    return max(MIN_WIDTH, min(cols, MAX_WIDTH))


def term_columns(default: int = DEFAULT_WIDTH) -> int:
    """The terminal's real column count, unclamped.

    `term_width` is a *reading* width — capped at 120 so prose does not sprawl.
    Cursor arithmetic needs the real number instead: a menu redraw that moves
    the cursor up by N rows has to know when the terminal will soft-wrap a
    line into two rows, and that happens at the true edge, not at 120.
    """
    try:
        return max(20, shutil.get_terminal_size((default, 24)).columns)
    except Exception:
        return default


def term_lines(default: int = 24) -> int:
    """The terminal's real row count. Used to size a menu viewport."""
    try:
        return max(6, shutil.get_terminal_size((80, default)).lines)
    except Exception:
        return default


def shorten(text: str, width: int, ellipsis: str = "…") -> str:
    """Truncate to `width` columns, always on a character boundary.

    The old `json.dumps(...)[:120]` cut inside a `\\uXXXX` escape and emitted
    fragments like `3f\\u0440\\u0438`. Slicing by column, on whole
    characters, cannot produce that.

    Colour is carried, not counted. `display_width` strips ANSI before
    measuring, so the early-exit below is right — but the truncation loop used
    to walk the raw string and charge a column for each byte of `\\x1b[92m`.
    A coloured row therefore got cut several columns early and could be cut
    *inside* an escape, leaving `[9` on screen and the colour stuck on. Escapes
    are now stepped over at zero width and re-emitted intact, and a reset is
    appended when one was seen, so a truncated row cannot bleed its colour into
    the rest of the line.
    """
    text = text or ""
    if width <= 0:
        return ""
    if display_width(text) <= width:
        return text
    budget = max(0, width - display_width(ellipsis))
    out: list[str] = []
    used = 0
    saw_escape = False
    i = 0
    while i < len(text):
        match = _ANSI_RE.match(text, i)
        if match:
            out.append(match.group())
            saw_escape = True
            i = match.end()
            continue
        w = char_width(text[i])
        if used + w > budget:
            break
        out.append(text[i])
        used += w
        i += 1
    return "".join(out) + ellipsis + (RESET if saw_escape else "")


def rule(char: str = "─", width: int | None = None, indent: int = 2) -> str:
    """A horizontal rule that matches the terminal instead of a fixed 46."""
    return char * max(1, (width or term_width()) - indent)


def wrap(text: str, indent: str = "  ", width: int | None = None,
         subsequent_indent: str | None = None) -> str:
    """Wrap to the terminal, preserving blank lines and not breaking words.

    Deliberately not `textwrap.wrap`: that measures with `len()`, which is
    wrong for CJK and emoji, and it collapses the blank lines that separate
    paragraphs in the model's markdown.
    """
    limit = (width or term_width()) - display_width(indent)
    following = subsequent_indent if subsequent_indent is not None else indent
    out: list[str] = []

    for para in (text or "").split("\n"):
        if not para.strip():
            out.append("")
            continue
        # Never reflow code or table rows — the alignment is the content.
        if para.lstrip().startswith(("```", "|", "    ")):
            out.append(indent + para)
            continue
        line, line_w, first = [], 0, True
        for word in para.split(" "):
            w = display_width(word)
            if line and line_w + 1 + w > limit:
                out.append((indent if first else following) + " ".join(line))
                first, line, line_w = False, [word], w
            else:
                if line:
                    line_w += 1
                line.append(word)
                line_w += w
        if line:
            out.append((indent if first else following) + " ".join(line))
    return "\n".join(out)


class StreamWrap:
    """Wraps model output to the terminal *as it streams*.

    The non-streaming path has always run its reply through `wrap`. The
    streaming path wrote deltas straight to stdout, so the same reply was
    laid out two different ways depending on whether the provider supported
    streaming: wrapped and indented when it did not, running off the right
    edge when it did.

    Wrapping a stream means never looking ahead, so the rule is: hold the word
    currently being typed, and commit it only once its width is known. A code
    fence switches the wrapper off, because reflowing code destroys it.
    """

    def __init__(self, indent: str = "  ", width: int | None = None):
        self.indent = indent
        self.limit = max(20, (width or term_width()) - display_width(indent))
        self._word: list[str] = []
        self._col = 0
        self._in_fence = False
        self._line_start = True
        self._recent: list[str] = []   # chars of the current line, for fence detection

    def feed(self, chunk: str) -> str:
        """Consume a delta, return the text that should be written now."""
        out: list[str] = []
        for ch in chunk or "":
            if ch == "\n":
                out.append(self._commit_word())
                # A fence toggles on the line that contains it.
                if "".join(self._recent).strip().startswith("```"):
                    self._in_fence = not self._in_fence
                out.append("\n" + self.indent)
                self._col = 0
                self._line_start = True
                self._recent = []
                continue

            self._recent.append(ch)
            if self._in_fence:
                out.append(ch)
                self._col += char_width(ch)
                continue

            if ch == " ":
                out.append(self._commit_word())
                # A space that would sit past the edge becomes the wrap point.
                if self._col + 1 > self.limit:
                    out.append("\n" + self.indent)
                    self._col = 0
                elif not self._line_start:
                    out.append(" ")
                    self._col += 1
                continue

            self._word.append(ch)
        return "".join(out)

    def _commit_word(self) -> str:
        if not self._word:
            return ""
        word = "".join(self._word)
        self._word = []
        w = display_width(word)
        prefix = ""
        if self._col and self._col + w > self.limit:
            prefix = "\n" + self.indent
            self._col = 0
        self._col += w
        self._line_start = False
        return prefix + word

    def flush(self) -> str:
        """Emit whatever is still held. Call once the stream ends."""
        tail = self._commit_word()
        self._recent = []
        return tail


def pad(text: str, width: int, align: str = "left") -> str:
    """Pad to `width` display columns (not len), for aligned columns."""
    gap = max(0, width - display_width(text))
    if align == "right":
        return " " * gap + text
    if align == "center":
        left = gap // 2
        return " " * left + text + " " * (gap - left)
    return text + " " * gap
