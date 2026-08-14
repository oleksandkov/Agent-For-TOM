"""Measure a DOCX sample's style contract: page size, margins, fonts, spacing.

Usage: python analyze_docx.py <sample.docx> <contract.json>

Unlike a PDF sample, every number here is read directly from the section,
style and paragraph properties python-docx exposes — no bounding-box
guesswork needed. This matters for spacing specifically: measuring heading
space-before/after from a *PDF's* rendered line positions was tried and
does not generalize (Word merges adjacent paragraphs' before/after values
by taking the max, not the sum, and which one wins depends on what precedes
each heading — not recoverable from glyph positions alone, confirmed by
measuring three headings in the same real document and getting three
different apparent rules). A DOCX sample has no such ambiguity: these are
the paragraph's own stored properties.

`body_indent_cm` and `line_spacing` are the *mode* among justified
paragraphs (the common body-paragraph style), not the first one seen — a
single differently-formatted paragraph must not skew the contract.
`heading_space_before_pt`/`heading_space_after_pt` are the mode among
centered+bold paragraphs (headings), which can legitimately differ from
body spacing.
"""
import sys
import json
from collections import Counter

import docx
from docx.enum.text import WD_ALIGN_PARAGRAPH as ALIGN

EMU_PER_CM = 360000
MARGIN_KEYS = ("top_margin", "bottom_margin", "left_margin", "right_margin")


def _mode(counter: Counter, default=None):
    return counter.most_common(1)[0][0] if counter else default


def analyze(docx_path: str) -> dict:
    d = docx.Document(docx_path)
    section = d.sections[0]

    def emu_to_cm(emu: int) -> float:
        return round(emu / EMU_PER_CM, 2)

    contract = {
        "page_width_cm": emu_to_cm(section.page_width),
        "page_height_cm": emu_to_cm(section.page_height),
        "margins_cm": {k: emu_to_cm(getattr(section, k)) for k in MARGIN_KEYS},
        "body_font": d.styles["Normal"].font.name,
        "body_size_pt": d.styles["Normal"].font.size.pt,
    }

    run_sizes = sorted(
        {r.font.size.pt for p in d.paragraphs for r in p.runs if r.font.size},
        reverse=True,
    )
    contract["title_size_pt"] = run_sizes[0] if run_sizes else contract["body_size_pt"]

    indents: Counter = Counter()
    line_spacings: Counter = Counter()
    heading_sb: Counter = Counter()
    heading_sa: Counter = Counter()

    for p in d.paragraphs:
        if not p.text.strip():
            continue
        is_heading = (p.alignment == ALIGN.CENTER
                      and any(r.bold for r in p.runs if r.text.strip()))
        pf = p.paragraph_format
        if is_heading:
            if pf.space_before is not None:
                heading_sb[round(pf.space_before.pt, 1)] += 1
            if pf.space_after is not None:
                heading_sa[round(pf.space_after.pt, 1)] += 1
        elif p.alignment == ALIGN.JUSTIFY:
            # Hanging-indent list items (dash/numbered) use a *negative*
            # first_line_indent and commonly outnumber plain body paragraphs
            # in a document full of questions and lists — including them
            # made the mode pick -0.63cm (a hanging indent) over 1.92cm (the
            # real body indent), confirmed against a real generated file
            # where they outnumbered true body paragraphs 25 to 7. A body
            # paragraph's first-line indent is always positive in this
            # convention, so excluding non-positive values removes them.
            if pf.first_line_indent is not None and pf.first_line_indent > 0:
                indents[round(pf.first_line_indent / EMU_PER_CM, 2)] += 1
            if pf.line_spacing is not None:
                line_spacings[round(pf.line_spacing, 2)] += 1

    contract["body_indent_cm"] = _mode(indents, 1.25)
    contract["line_spacing"] = _mode(line_spacings, 1.15)
    contract["heading_space_before_pt"] = _mode(heading_sb, 4.0)
    contract["heading_space_after_pt"] = _mode(heading_sa, 4.0)
    return contract


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python analyze_docx.py <sample.docx> <contract.json>")
        raise SystemExit(2)
    contract = analyze(sys.argv[1])
    with open(sys.argv[2], "w", encoding="utf-8") as f:
        json.dump(contract, f, indent=2)
    print(json.dumps(contract, indent=2))
