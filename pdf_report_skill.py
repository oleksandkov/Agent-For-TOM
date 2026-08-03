"""
PDF Report Skill — generates a PDF document with the latest AI news.

Usage (from TOMAS chat):
    /pdf-report

This module reads `latest_ai_news_report.txt` in the project directory,
formats it as a clean PDF, and saves it as `latest_ai_news_report.pdf`.

Requires: fpdf2 (pip install fpdf2)
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

try:
    from fpdf import FPDF
except ImportError:
    FPDF = None  # type: ignore


# ── Paths ─────────────────────────────────────────────────────────────

PROJECT_DIR = Path(os.environ.get("AGENT_PROJECT_DIR", os.getcwd())).resolve()
NEWS_SOURCE = PROJECT_DIR / "latest_ai_news_report.txt"
OUTPUT_PDF = PROJECT_DIR / "latest_ai_news_report.pdf"


# ── PDF generation ───────────────────────────────────────────────────


class _PDF(FPDF):
    """Minimal PDF with header / footer and Unicode support."""

    def __init__(self):
        super().__init__()
        # Register a Unicode font (DejaVuSans) for proper character support
        self._unicode_font = self._try_add_unicode_font()

    # (regular, bold, italic) candidates, best first. DejaVu is the portable
    # choice; Arial ships with Windows and covers Cyrillic; Calibri is a
    # further fallback. The previous version looked only for DejaVu — absent
    # on a stock Windows box — and, more importantly, never called add_font,
    # so even a hit would have failed at set_font.
    _FONT_FAMILIES = [
        ("DejaVuSans", "DejaVuSans.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans-Oblique.ttf"),
        ("Arial", "arial.ttf", "arialbd.ttf", "ariali.ttf"),
        ("Calibri", "calibri.ttf", "calibrib.ttf", "calibrii.ttf"),
        ("Verdana", "verdana.ttf", "verdanab.ttf", "verdanai.ttf"),
    ]

    _FONT_DIRS = [
        Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts",
        Path.home() / "AppData/Local/Microsoft/Windows/Fonts",
        Path("/usr/share/fonts/truetype/dejavu"),
        Path("/usr/share/fonts/dejavu"),
        Path("/usr/share/fonts"),
        Path("/Library/Fonts"),
        Path("/System/Library/Fonts"),
    ]

    def _try_add_unicode_font(self) -> str:
        """Register a Unicode TTF so Cyrillic renders. Returns the family
        name, or "" to fall back to latin-1 core Helvetica."""
        for family, regular, bold, italic in self._FONT_FAMILIES:
            for d in self._FONT_DIRS:
                reg = d / regular
                if not reg.exists():
                    continue
                try:
                    self.add_font(family, "", str(reg))
                    for style, fname in (("B", bold), ("I", italic)):
                        f = d / fname
                        # Re-use the regular face when the styled file is
                        # missing: a missing bold must not lose Cyrillic.
                        self.add_font(family, style, str(f if f.exists() else reg))
                    return family
                except Exception:
                    continue
        return ""

    def _use_font(self, style: str = "", size: int = 10):
        """Set font: the registered Unicode family if we have one, else the
        latin-1 core font."""
        if self._unicode_font:
            self.set_font(self._unicode_font, style, size)
        else:
            self.set_font("Helvetica", style, size)

    def header(self):
        self._use_font("B", 10)
        self.set_text_color(100, 100, 100)
        self.cell(0, 8, "AI News Report", align="L")
        self.ln(12)

    def footer(self):
        self.set_y(-15)
        self._use_font("I", 8)
        self.set_text_color(140, 140, 140)
        self.cell(0, 10, f"Page {self.page_no()}/{{nb}}", align="C")


def _sanitize_text(text: str) -> str:
    """Replace Unicode characters not in latin-1 with safe ASCII equivalents."""
    replacements = {
        "\u2014": "---",  # em dash
        "\u2013": "--",   # en dash
        "\u2018": "'",    # left single quote
        "\u2019": "'",    # right single quote
        "\u201c": '"',    # left double quote
        "\u201d": '"',    # right double quote
        "\u2026": "...",  # ellipsis
        "\u00a0": " ",    # non-breaking space
        "\u2022": "*",    # bullet
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


def _latin1_safe(text: str) -> str:
    """Last resort when no Unicode font could be registered.

    fpdf2's core fonts are latin-1 only and *raise* on anything outside it,
    so a Cyrillic report used to fail outright. Transliterating is worse than
    the real font and far better than an exception.
    """
    table = str.maketrans({
        "а": "a", "б": "b", "в": "v", "г": "h", "ґ": "g", "д": "d", "е": "e",
        "є": "ie", "ж": "zh", "з": "z", "и": "y", "і": "i", "ї": "i",
        "й": "i", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p",
        "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh",
        "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch", "ь": "", "ю": "iu",
        "я": "ia", "ы": "y", "э": "e", "ё": "e", "ъ": "",
    })
    out = text.lower().translate(table) if any(
        "Ѐ" <= c <= "ӿ" for c in text) else text
    return out.encode("latin-1", "replace").decode("latin-1")


def _fetch_news_text() -> str:
    """Return the AI news text, either from the local file or a fallback."""
    if NEWS_SOURCE.exists():
        return _sanitize_text(NEWS_SOURCE.read_text(encoding="utf-8", errors="replace"))
    return (
        "No local AI news file found.\n\n"
        f"Expected at: {NEWS_SOURCE}\n\n"
        "To generate a news report, ask the AI agent to research and "
        "write the latest AI news to `latest_ai_news_report.txt` first."
    )


def generate_ai_news_pdf(output_path: str | Path = OUTPUT_PDF) -> str:
    """
    Generate a PDF report of the latest AI news.

    Reads *latest_ai_news_report.txt*, formats it, and writes a PDF.

    Returns the absolute path to the generated PDF file.

    Raises RuntimeError if *fpdf2* is not installed.
    """
    if FPDF is None:
        raise RuntimeError(
            "fpdf2 is required but not installed.\n"
            "  pip install fpdf2"
        )

    output_path = Path(output_path)
    raw = _fetch_news_text()
    lines = raw.splitlines()

    pdf = _PDF()
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()

    if not pdf._unicode_font:
        # No Unicode TTF on this machine: degrade to transliteration rather
        # than raising FPDFUnicodeEncodingException on the first Cyrillic
        # character.
        lines = [_latin1_safe(line) for line in lines]

    # ── Title ──
    pdf._use_font("B", 18)
    pdf.set_text_color(30, 30, 30)
    pdf.cell(0, 14, "Latest AI News", align="C")
    pdf.ln(12)

    # ── Date ──
    pdf._use_font("", 9)
    pdf.set_text_color(120, 120, 120)
    pdf.cell(0, 8, f"Generated: {datetime.now():%d %B %Y at %H:%M}", align="C")
    pdf.ln(14)

    # ── Separator ──
    pdf.set_draw_color(200, 200, 200)
    pdf.line(20, pdf.get_y(), pdf.w - 20, pdf.get_y())
    pdf.ln(8)

    # ── Body ──
    pdf.set_text_color(50, 50, 50)

    for line in lines:
        stripped = line.strip()

        if not stripped:
            pdf.ln(4)
            continue

        # Headline detection (lines ending with ':' or all-caps short lines)
        if stripped.endswith(":") or (
            len(stripped) < 60 and stripped.isupper()
        ):
            pdf._use_font("B", 12)
            pdf.multi_cell(0, 7, stripped)
            pdf.ln(2)
        elif stripped.startswith("- ") or stripped.startswith("* "):
            # Bullet point — use explicit width and reset x afterwards,
            # because multi_cell (fpdf2 >= 2.8) leaves x at the right margin.
            pdf._use_font("", 10)
            x = pdf.get_x()
            pdf.set_x(x + 8)
            pdf.multi_cell(pdf.w - pdf.r_margin - pdf.get_x(), 6, stripped)
            pdf.set_x(pdf.l_margin)
        else:
            pdf._use_font("", 10)
            pdf.multi_cell(0, 6, stripped)
            pdf.ln(1)

    pdf.output(str(output_path))
    return str(output_path.resolve())


# ── CLI entry point ───────────────────────────────────────────────────

if __name__ == "__main__":
    try:
        path = generate_ai_news_pdf()
        print(f"PDF report saved to: {path}")
    except RuntimeError as e:
        print(e, file=sys.stderr)
        sys.exit(1)
