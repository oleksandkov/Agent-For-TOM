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


def shorten(text: str, width: int, ellipsis: str = "…") -> str:
    """Truncate to `width` columns, always on a character boundary.

    The old `json.dumps(...)[:120]` cut inside a `\\uXXXX` escape and emitted
    fragments like `3f\\u0440\\u0438`. Slicing by column, on whole
    characters, cannot produce that.
    """
    text = text or ""
    if width <= 0:
        return ""
    if display_width(text) <= width:
        return text
    budget = max(0, width - display_width(ellipsis))
    out, used = [], 0
    for ch in text:
        w = char_width(ch)
        if used + w > budget:
            break
        out.append(ch)
        used += w
    return "".join(out) + ellipsis


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


def pad(text: str, width: int, align: str = "left") -> str:
    """Pad to `width` display columns (not len), for aligned columns."""
    gap = max(0, width - display_width(text))
    if align == "right":
        return " " * gap + text
    if align == "center":
        left = gap // 2
        return " " * left + text + " " * (gap - left)
    return text + " " * gap
