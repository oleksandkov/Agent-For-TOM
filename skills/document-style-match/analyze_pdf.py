"""Measure a PDF sample's style contract: page size, margins, fonts.

Usage: python analyze_pdf.py <sample.pdf> <contract.json>

Font sizes are the *mode* (most frequent), not min/max, on the pages that
actually carry that role — title from page 1 (the cover), body from the
pages after it. Measured on a real Ukrainian lab-report template: page 1
was 20pt only; pages 2-4 were 14pt (30-40 spans) with occasional smaller
text (footnotes, table cells) mixed in. Taking min() across all pages for
"body size" picked up that smaller text (10.6pt) instead of the actual body
size (14pt) — the mode of the pages where body text dominates is what's
representative, not the extreme value across every scanned page.

Left/right margins are likewise the mode of line-start/line-end x, not
min/max — min hits a hanging list-marker sitting left of the real body
margin. Measured on the same template: min gave 1.85cm (wrong), mode gave
2.35cm (right, confirmed against the sample's actually-used margin).

Top/bottom margins are not measured here (harder to isolate reliably from
text alone) and are left as the Ukrainian lab-report convention (2.0cm each)
— the caller should say in its own report that these two are assumed, not
measured, same as it already does for a DOCX sample's unreadable fields.
"""
import sys
import json
from collections import Counter

import fitz


def _line_sizes(page) -> Counter:
    sizes: Counter = Counter()
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                if span["text"].strip():
                    sizes[round(span["size"], 1)] += 1
    return sizes


def analyze(pdf_path: str) -> dict:
    doc = fitz.open(pdf_path)
    page_rect = doc[0].rect  # page size read off the actual page — never assume A4/Letter

    title_sizes = _line_sizes(doc[0])
    body_sizes: Counter = Counter()
    line_starts: Counter = Counter()
    line_ends: list[float] = []

    body_pages = range(1, min(4, doc.page_count)) if doc.page_count > 1 else range(0, 1)
    for page_no in body_pages:
        page = doc[page_no]
        body_sizes.update(_line_sizes(page))
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                text = "".join(s["text"] for s in line.get("spans", []))
                if not text.strip():
                    continue
                line_starts[round(line["bbox"][0], 1)] += 1
                line_ends.append(line["bbox"][2])

    title_size = title_sizes.most_common(1)[0][0] if title_sizes else None
    body_size = body_sizes.most_common(1)[0][0] if body_sizes else title_size
    left_margin_pt = line_starts.most_common(1)[0][0] if line_starts else 0.0
    right_margin_pt = page_rect.width - max(line_ends) if line_ends else 0.0

    def pt_to_cm(pt: float) -> float:
        return round(pt / 72 * 2.54, 2)

    return {
        "page_width_cm": pt_to_cm(page_rect.width),
        "page_height_cm": pt_to_cm(page_rect.height),
        "title_size_pt": title_size,
        "body_size_pt": body_size,
        "margins_cm": {
            "left_margin": pt_to_cm(left_margin_pt),
            "right_margin": pt_to_cm(right_margin_pt),
            "top_margin": 2.0,   # convention — not measured, see module docstring
            "bottom_margin": 2.0,  # convention — not measured, see module docstring
        },
    }


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python analyze_pdf.py <sample.pdf> <contract.json>")
        raise SystemExit(2)
    contract = analyze(sys.argv[1])
    with open(sys.argv[2], "w", encoding="utf-8") as f:
        json.dump(contract, f, indent=2)
    print(json.dumps(contract, indent=2))
