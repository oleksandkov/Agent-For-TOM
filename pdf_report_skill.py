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
    """Minimal PDF with header / footer."""

    def header(self):
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(100, 100, 100)
        self.cell(0, 8, "AI News Report", align="L")
        self.ln(12)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(140, 140, 140)
        self.cell(0, 10, f"Page {self.page_no()}/{{nb}}", align="C")


def _fetch_news_text() -> str:
    """Return the AI news text, either from the local file or a fallback."""
    if NEWS_SOURCE.exists():
        return NEWS_SOURCE.read_text(encoding="utf-8", errors="replace")
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

    # ── Title ──
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(30, 30, 30)
    pdf.cell(0, 14, "Latest AI News", align="C")
    pdf.ln(12)

    # ── Date ──
    pdf.set_font("Helvetica", "", 9)
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
            pdf.set_font("Helvetica", "B", 12)
            pdf.multi_cell(0, 7, stripped)
            pdf.ln(2)
        elif stripped.startswith("- ") or stripped.startswith("* "):
            # Bullet point
            pdf.set_font("Helvetica", "", 10)
            x = pdf.get_x()
            pdf.set_x(x + 8)
            pdf.multi_cell(0, 6, stripped)
        else:
            pdf.set_font("Helvetica", "", 10)
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
