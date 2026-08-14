"""Verify a converted PDF's own rendering, not just the DOCX it came from.

Usage: python verify_pdf.py <contract.json> <output.pdf>

A DOCX can be provably correct (every `w:sz` value exactly right) and still
convert to a PDF where the title renders at a fraction of a point — nothing
about the DOCX shows this, because the defect is introduced during
conversion. Measured live: a title/heading/teacher-line block rendered at
~0.5pt in the PDF from a DOCX whose own XML was checked and correct — a
one-off DOCX-to-PDF conversion artifact, not a generation bug, but real
until checked. This script flags any text block on page 1 whose width is
implausibly small for what should be a full-width centered line.
"""
import sys
import json

import fitz

# A centered title/heading block should span a large fraction of the page
# width; anything narrower than this is either a short label (fine) or a
# collapsed-to-near-zero-size line (not fine). Tuned loose on purpose: this
# only needs to catch "obviously wrong," not police every short line.
MIN_PLAUSIBLE_WIDTH_PT = 15.0
TOLERANCE_CM = 0.05


def verify(contract_path: str, pdf_path: str) -> list[str]:
    contract = json.load(open(contract_path, encoding="utf-8"))
    doc = fitz.open(pdf_path)
    page = doc[0]

    problems = []

    width_cm = round(page.rect.width / 72 * 2.54, 2)
    height_cm = round(page.rect.height / 72 * 2.54, 2)
    if contract.get("page_width_cm") is not None and \
            abs(width_cm - contract["page_width_cm"]) > TOLERANCE_CM:
        problems.append(f"page_width_cm: got {width_cm}, contract {contract['page_width_cm']}")
    if contract.get("page_height_cm") is not None and \
            abs(height_cm - contract["page_height_cm"]) > TOLERANCE_CM:
        problems.append(f"page_height_cm: got {height_cm}, contract {contract['page_height_cm']}")

    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            text = "".join(s["text"] for s in line.get("spans", []))
            if not text.strip():
                continue
            width = line["bbox"][2] - line["bbox"][0]
            # A short line (a label, a page number) is legitimately narrow.
            # Only flag lines with enough characters that they *should* be
            # visibly wide if rendered at a normal size.
            if len(text.strip()) >= 15 and width < MIN_PLAUSIBLE_WIDTH_PT:
                problems.append(
                    f"line {text.strip()[:40]!r} is only {width:.1f}pt wide "
                    f"for {len(text.strip())} characters — likely rendered "
                    f"at a near-zero font size")
    return problems


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python verify_pdf.py <contract.json> <output.pdf>")
        raise SystemExit(2)
    problems = verify(sys.argv[1], sys.argv[2])
    if problems:
        print("PDF LAYOUT ANOMALY:")
        for p in problems:
            print(" -", p)
        raise SystemExit(1)
    print("PDF layout OK.")
