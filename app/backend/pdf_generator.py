"""
DSTU 3008:2015-compliant PDF generator using ReportLab.

Layout requirements followed:
- A4 portrait
- Margins: top 2.0cm, bottom 2.0cm, left 3.0cm (binding), right 1.0cm
- Body: Times New Roman 14pt, line spacing 1.5, first-line indent 1.25cm, justify
- Headings: level 1 — 16pt bold centered; level 2 — 14pt bold left
- Page numbers: top-right corner, suppressed on the title page
- References: numbered list (ДСТУ 8302:2015)
- Per-lab structure mirrors LMV_LabRob.pdf:
    topic -> 1 Мета роботи -> 2 Методичні відомості (theory)
          -> 3 Хід роботи (procedure) -> 4 Контрольні запитання
          -> Література (per-lab references)
"""

from __future__ import annotations

import os
from typing import List

from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
)

from .models import DocumentMetadata, LabGuidelinesContent


# ---------------------------------------------------------------------------
# Small text utilities
# ---------------------------------------------------------------------------

import re

# Matches step prefixes the AI sometimes prepends and that the generator
# would duplicate ("1. Крок 1. Підготувати…").
# Covers:  "Крок 1.",  "Крок 1 -",  "Step 1.",  "1.1.",  "1)"  and so on.
_STEP_PREFIX_RE = re.compile(
    r"^\s*(?:крок|step|крок\s*№)\s*\d+\s*[\.\-:)]?\s*",
    flags=re.IGNORECASE,
)
_NUMERIC_PREFIX_RE = re.compile(r"^\s*\d+\s*[\.\-)]\s*")


def _strip_step_prefix(text: str) -> str:
    """Strip leading "Крок N." / "Step N." / "N." prefixes from a step.

    Used as a defensive normaliser: the AI sometimes returns
    "Крок 1. Підготувати…", and the generator also prepends "1. ",
    which gives "1. Крок 1. Підготувати…". Removing the duplicated prefix
    here keeps the rendered output clean.
    """
    s = text.strip()
    changed = True
    while changed:
        changed = False
        if _STEP_PREFIX_RE.match(s):
            s = _STEP_PREFIX_RE.sub("", s, count=1).lstrip()
            changed = True
        elif _NUMERIC_PREFIX_RE.match(s):
            # Only strip a leading numeric prefix if it duplicates what
            # the generator will produce, i.e. when followed by text that
            # also has a "Крок" or "Step" pattern right after it.
            stripped = _NUMERIC_PREFIX_RE.sub("", s, count=1)
            if stripped != s and _STEP_PREFIX_RE.match(stripped):
                s = stripped.lstrip()
                changed = True
    return s


# ---------------------------------------------------------------------------
# Font registration (Times New Roman + Cyrillic)
# ---------------------------------------------------------------------------

_FONTS_REGISTERED = False


def _register_fonts() -> None:
    """Register Times New Roman Regular/Bold/Italic/BoldItalic as the TNRoman family.

    Raises a clear error if the OS is missing the Windows TTF files.
    """
    global _FONTS_REGISTERED
    if _FONTS_REGISTERED:
        return

    fonts_dir = r"C:\Windows\Fonts"
    candidates = {
        "TNRoman": "times.ttf",
        "TNRoman-Bold": "timesbd.ttf",
        "TNRoman-Italic": "timesi.ttf",
        "TNRoman-BoldItalic": "timesbi.ttf",
    }
    try:
        registered = {}
        for face, fname in candidates.items():
            path = os.path.join(fonts_dir, fname)
            if not os.path.exists(path):
                raise FileNotFoundError(path)
            pdfmetrics.registerFont(TTFont(face, path))
            registered[face] = face
        pdfmetrics.registerFontFamily(
            "TNRoman",
            normal=registered["TNRoman"],
            bold=registered["TNRoman-Bold"],
            italic=registered["TNRoman-Italic"],
            boldItalic=registered["TNRoman-BoldItalic"],
        )
        _FONTS_REGISTERED = True
    except FileNotFoundError as exc:
        raise RuntimeError(
            "Не знайдено шрифт Times New Roman у C:\\Windows\\Fonts. "
            "Генератор PDF вимагає Windows з встановленим TNR (з підтримкою кирилиці). "
            f"Відсутній файл: {exc.filename}"
        ) from exc


# ---------------------------------------------------------------------------
# Layout constants (ДСТУ 3008:2015)
# ---------------------------------------------------------------------------

PAGE_W, PAGE_H = A4
LEFT_MARGIN = 3.0 * cm
RIGHT_MARGIN = 1.0 * cm
TOP_MARGIN = 2.0 * cm
BOTTOM_MARGIN = 2.0 * cm
CONTENT_W = PAGE_W - LEFT_MARGIN - RIGHT_MARGIN
CONTENT_H = PAGE_H - TOP_MARGIN - BOTTOM_MARGIN


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------

class DSTUPdfGenerator:
    """Renders a LabGuidelinesContent into a ДСТУ-3008:2015-compliant PDF."""

    def __init__(self) -> None:
        _register_fonts()
        self._current_meta: DocumentMetadata | None = None
        self.styles = self._make_styles()
        self.doc = self._make_document()

    # ----- styles & frame setup -------------------------------------------

    def _make_styles(self) -> dict:
        return {
            "body": ParagraphStyle(
                "body",
                fontName="TNRoman",
                fontSize=14,
                leading=21,  # 1.5 * 14pt
                alignment=TA_JUSTIFY,
                firstLineIndent=1.25 * cm,
                spaceBefore=0,
                spaceAfter=0,
            ),
            "body_no_indent": ParagraphStyle(
                "body_no_indent",
                fontName="TNRoman",
                fontSize=14,
                leading=21,
                alignment=TA_JUSTIFY,
                firstLineIndent=0,
            ),
            "h1": ParagraphStyle(
                "h1",
                fontName="TNRoman-Bold",
                fontSize=16,
                leading=20,
                alignment=TA_CENTER,
                spaceBefore=12,
                spaceAfter=12,
                keepWithNext=1,
            ),
            "h2": ParagraphStyle(
                "h2",
                fontName="TNRoman-Bold",
                fontSize=14,
                leading=18,
                alignment=TA_LEFT,
                spaceBefore=10,
                spaceAfter=6,
                keepWithNext=1,
            ),
            "list_item": ParagraphStyle(
                "list_item",
                fontName="TNRoman",
                fontSize=14,
                leading=21,
                alignment=TA_JUSTIFY,
                firstLineIndent=1.25 * cm,
                leftIndent=0,
            ),
            "italic_tip": ParagraphStyle(
                "italic_tip",
                fontName="TNRoman-Italic",
                fontSize=11,
                leading=14,
                alignment=TA_CENTER,
            ),
            "tp_centered": ParagraphStyle(
                "tp_centered",
                fontName="TNRoman",
                fontSize=12,
                leading=14,
                alignment=TA_CENTER,
            ),
            "tp_centered_bold": ParagraphStyle(
                "tp_centered_bold",
                fontName="TNRoman-Bold",
                fontSize=12,
                leading=14,
                alignment=TA_CENTER,
            ),
            "tp_right": ParagraphStyle(
                "tp_right",
                fontName="TNRoman",
                fontSize=12,
                leading=14,
                alignment=TA_CENTER,  # overridden in onPage to right-align
            ),
        }

    def _make_document(self) -> BaseDocTemplate:
        title_frame = Frame(
            LEFT_MARGIN, BOTTOM_MARGIN, CONTENT_W, CONTENT_H,
            id="title", showBoundary=0, leftPadding=0, rightPadding=0,
            topPadding=0, bottomPadding=0,
        )
        content_frame = Frame(
            LEFT_MARGIN, BOTTOM_MARGIN, CONTENT_W, CONTENT_H,
            id="content", showBoundary=0, leftPadding=0, rightPadding=0,
            topPadding=0, bottomPadding=0,
        )
        doc = BaseDocTemplate(
            "buffer.pdf",
            pagesize=A4,
            leftMargin=LEFT_MARGIN,
            rightMargin=RIGHT_MARGIN,
            topMargin=TOP_MARGIN,
            bottomMargin=BOTTOM_MARGIN,
            title="Методичні вказівки до лабораторних робіт",
        )
        doc.addPageTemplates(
            [
                PageTemplate(id="title", frames=[title_frame], onPage=self._draw_title_page),
                PageTemplate(id="content", frames=[content_frame], onPage=self._draw_page_number),
            ]
        )
        return doc

    # ----- canvas hooks ---------------------------------------------------

    def _draw_page_number(self, canvas, doc) -> None:
        """Top-right page number, skipped on the first page (title)."""
        if doc.page <= 1:
            return
        canvas.saveState()
        canvas.setFont("TNRoman", 11)
        canvas.drawRightString(PAGE_W - RIGHT_MARGIN, PAGE_H - 1.0 * cm, str(doc.page))
        canvas.restoreState()

    def _draw_title_page(self, canvas, doc) -> None:
        """Render the entire title page directly with the canvas (precise positioning).

        Layout (ДСТУ 3008:2015 style, LMV_LabRob.pdf sample):
            * Top header (ministry / university / department) starts ~2 cm from top.
            * Main title block sits a bit below.
            * Authors on the right side, just under the title.
            * City and year are placed at the BOTTOM of the page (centred,
              ~2.5 cm from the bottom edge). They are not part of the
              flowing top-down block; they are pinned to the bottom.

        The university line is allowed to auto-shrink: long names like
        "Національний технічний університет України «Київський
        політехнічний інститут імені Ігоря Сікорського»" no longer
        overflow the right margin.
        """
        meta = self._current_meta
        if meta is None:
            return
        canvas.saveState()

        # Width available for centred text on the title page.
        # A4 portrait, 3 cm left margin, 1 cm right margin.
        usable_w = PAGE_W - LEFT_MARGIN - RIGHT_MARGIN

        def write(text: str, x: float, y: float, size: int = 12, bold: bool = False,
                  italic: bool = False, align: str = "center",
                  max_width: float | None = None) -> float:
            """Write text, auto-shrinking the font if `max_width` is given
            and the text would otherwise overflow.
            """
            face = "TNRoman-Bold" if bold else ("TNRoman-Italic" if italic else "TNRoman")
            current_size = size
            if max_width is not None:
                # Shrink until the text fits, down to 7 pt minimum.
                while current_size > 7 and canvas.stringWidth(text, face, current_size) > max_width:
                    current_size -= 0.5
            canvas.setFont(face, current_size)
            if align == "center":
                canvas.drawCentredString(x, y, text)
            elif align == "right":
                canvas.drawRightString(x, y, text)
            else:
                canvas.drawString(x, y, text)
            return y - (current_size * 1.35)

        cx = PAGE_W / 2
        rx = PAGE_W - RIGHT_MARGIN

        # Top header block (ministry / university / department).
        # University and department are auto-shrunk so long names fit.
        y = PAGE_H - 2.0 * cm
        y = write("МІНІСТЕРСТВО ОСВІТИ І НАУКИ УКРАЇНИ", cx, y, 12, bold=True)
        y = write(meta.university.upper(), cx, y, 12, bold=True, max_width=usable_w)
        y = write(f"Кафедра {meta.department}", cx, y, 12, max_width=usable_w)
        y -= 1.6 * cm  # gap before main title

        # Main title block
        y = write("МЕТОДИЧНІ ВКАЗІВКИ", cx, y, 18, bold=True)
        y = write("до виконання лабораторних робіт з дисципліни", cx, y, 14)
        y = write(f"«{meta.discipline}»", cx, y, 16, bold=True, max_width=usable_w)
        y -= 2.0 * cm  # gap before authors

        # Authors (right-aligned, auto-shrunk for long author lists)
        y_after_label = write("Розробники:", rx, y, 12, italic=True, align="right",
                              max_width=usable_w)
        y = y_after_label
        y -= 0.1 * cm
        y = write(", ".join(meta.authors), rx, y, 12, bold=True, align="right",
                  max_width=usable_w)

        # City and year — pinned to the BOTTOM of the page, centred.
        # Canvas Y-axis grows upward, so small numbers are near the
        # bottom. We want ~2.5 cm of margin from the bottom edge.
        bottom_y = 2.5 * cm
        write(meta.city, cx, bottom_y + 0.55 * cm, 12, max_width=usable_w)
        write(str(meta.year), cx, bottom_y, 12, max_width=usable_w)

        canvas.restoreState()

    # ----- content builders -----------------------------------------------

    def _title_flowables(self) -> list:
        # Title page is fully drawn by canvas, but Platypus still needs at
        # least one flowable on the title template so the frame advances.
        return [Spacer(1, 0.1)]

    def _body_flowables(self, content: LabGuidelinesContent) -> list:
        """Build the per-lab body in the LMV_LabRob.pdf style:

            ЛАБОРАТОРНА РОБОТА №N  (centered, h1)
            Тема: …               (centered, h2)
            Мета роботи - …       (bold lead, body)
            Загальні відомості    (centered, h1)
            <theory paragraphs>
            Хід роботи            (centered, h1)
            1. …                  (no «Крок N.» prefix duplication)
            Контрольні запитання  (centered, h1)
            1. …
            Завдання              (centered, h1)
            1. …
            Варіанти              (centered, h1)
            1. …
            Зміст звіту           (centered, h1)
            — …
            Література            (centered, h1)
            1. …

        The optional ВСТУП block is kept for multi-lab work, but the
        ЗМІСТ placeholder was removed: the LMV sample has no auto-TOC.
        """
        st = self.styles
        flow: list = []

        # Optional ВСТУП (skipped silently if empty)
        if content.introduction and content.introduction.strip():
            flow.append(Paragraph("ВСТУП", st["h1"]))
            for para in content.introduction.split("\n"):
                if para.strip():
                    flow.append(Paragraph(para.strip(), st["body"]))
            flow.append(PageBreak())

        for idx, lab in enumerate(content.lab_works, 1):
            # 1. ЛАБОРАТОРНА РОБОТА №N (centered, h1)
            flow.append(Paragraph(f"ЛАБОРАТОРНА РОБОТА №{idx}", st["h1"]))
            # 2. Тема: ... (centered, h2)
            flow.append(Paragraph(f"Тема: {lab.topic}", st["h2"]))

            # 3. Мета роботи - ...
            flow.append(
                Paragraph(
                    f"<b>Мета роботи</b> &mdash; {lab.objective}",
                    st["body_no_indent"],
                )
            )

            # 4. Загальні відомості
            flow.append(Paragraph("Загальні відомості", st["h1"]))
            for para in (lab.theory or "").split("\n"):
                if para.strip():
                    flow.append(Paragraph(para.strip(), st["body"]))

            # 5. Хід роботи (only if we actually have steps)
            if lab.procedure:
                flow.append(Paragraph("Хід роботи", st["h1"]))
                for i, step in enumerate(lab.procedure, 1):
                    clean = _strip_step_prefix(step)
                    flow.append(Paragraph(f"<b>{i}.</b> {clean}", st["list_item"]))

            # 6. Контрольні запитання
            if lab.questions:
                flow.append(Paragraph("Контрольні запитання", st["h1"]))
                for i, q in enumerate(lab.questions, 1):
                    flow.append(Paragraph(f"{i}. {q}", st["list_item"]))

            # 7. Завдання
            if lab.tasks:
                flow.append(Paragraph("Завдання", st["h1"]))
                for i, t in enumerate(lab.tasks, 1):
                    flow.append(Paragraph(f"{i}. {t}", st["list_item"]))

            # 8. Варіанти
            if lab.variants:
                flow.append(Paragraph("Варіанти", st["h1"]))
                for i, v in enumerate(lab.variants, 1):
                    flow.append(Paragraph(f"{i}. {v}", st["list_item"]))

            # 9. Зміст звіту (bulleted, not numbered)
            if lab.report_sections:
                flow.append(Paragraph("Зміст звіту", st["h1"]))
                for s in lab.report_sections:
                    flow.append(Paragraph(f"&mdash; {s}", st["list_item"]))

            # 10. Література
            if lab.references:
                flow.append(Paragraph("Література", st["h1"]))
                for i, ref in enumerate(lab.references, 1):
                    flow.append(Paragraph(f"{i}. {ref}", st["list_item"]))

            flow.append(PageBreak())

        # Global references (optional)
        if content.references:
            flow.append(Paragraph("СПИСОК РЕКОМЕНДОВАНИХ ДЖЕРЕЛ", st["h1"]))
            for i, ref in enumerate(content.references, 1):
                flow.append(Paragraph(f"{i}. {ref}", st["list_item"]))

        return flow

    # ----- public entrypoint ----------------------------------------------

    def generate(self, meta: DocumentMetadata, content: LabGuidelinesContent, filepath: str) -> None:
        self._current_meta = meta
        self.doc.filename = filepath
        story: list = []
        story += self._title_flowables()
        story.append(NextPageTemplate("content"))
        story.append(PageBreak())
        story += self._body_flowables(content)
        self.doc.build(story)
        print(f"Generated PDF: {filepath}")
