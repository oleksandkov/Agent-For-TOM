"""Compare a rendered result against the *sample*, not against the contract.

Usage: python verify_render.py <sample.pdf> <output.pdf>

`verify_docx` compares style *numbers* — page size, margins, fonts, spacing —
and `verify_pdf` looks at page 1 for collapsed text. Neither looks at what the
finished page actually reads like, and that is where every measured failure
has lived:

* A rebuild with a third of the sample's blank lines and a cover page jammed
  against the top margin passed all three checks (round 2).
* A rebuild whose paragraphs were one source *line* long — so Word left every
  second line unjustified and the extractor had to re-split 18 stretched
  lines, against the sample's own 6 — passed all three checks (round 3).

So this measures the result the way a reader sees it and compares each figure
with the same figure taken off the sample. Ratios and densities, never
absolutes: the output is a different document about a different topic, and is
entitled to a different length.

Exits 0 with "Render matches the sample's rhythm." or 1 with the specific
drift. A drift here is not automatically a bug — a title page the user asked
to fill with teacher names legitimately moves, and the report says so rather
than failing silently either way.
"""
import statistics
import sys
from collections import defaultdict

import fitz

#: Two fragments this close in y are the same visual line. Same value and the
#: same reason as `analyze_pdf._ROW_TOLERANCE_PT`.
ROW_TOLERANCE_PT = 1.5

#: How many times the sample's own rate of extractor-split lines the output
#: may show. The sample has some: a justified line whose word spacing grew
#: large is split by every PDF extractor. Many more than the sample means the
#: output is stretching lines the sample did not.
MAX_SPLIT_RATIO = 1.5

#: Blank lines per 100 text rows, as a share of the sample's rate.
BLANK_DENSITY_TOLERANCE = 0.30

#: Mean characters per rendered row, as a share of the sample's.
ROW_FILL_TOLERANCE = 0.30

#: How far the first line of page 1 may sit from where the sample's does.
PAGE1_OFFSET_TOL_PT = 40.0


def _rows(page):
    """Visual lines of a page: (x0, x1, y0, text), fragments merged."""
    frags = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            spans = [s for s in line.get("spans", []) if s["text"].strip()]
            if spans:
                frags.append((line["bbox"][1], line["bbox"][0], line["bbox"],
                              "".join(s["text"] for s in spans)))
    frags.sort(key=lambda f: (f[0], f[1]))
    rows, splits = [], 0
    for y0, _x0, bbox, text in frags:
        if rows and abs(y0 - rows[-1][2]) <= ROW_TOLERANCE_PT:
            x0, x1, top, prev = rows[-1]
            rows[-1] = (min(x0, bbox[0]), max(x1, bbox[2]), top,
                        f"{prev} {text}")
            splits += 1
            continue
        rows.append((bbox[0], bbox[2], y0, text))
    return rows, splits


def measure(pdf_path: str, pages=None) -> dict:
    doc = fitz.open(pdf_path)
    scanned = range(doc.page_count) if pages is None else pages
    scanned = [p for p in scanned if p < doc.page_count]

    rows_total = splits_total = 0
    chars = 0
    deltas: list[float] = []
    first_page_top = None
    for i, page_no in enumerate(scanned):
        rows, splits = _rows(doc[page_no])
        if not rows:
            continue
        if i == 0:
            first_page_top = rows[0][2]
            # The cover page is excluded from every density below. It is the
            # one page a caller legitimately redesigns — the sample's is
            # twelve blank lines and a title, and a user who asked for
            # teacher names on it gets something else by request. Averaging
            # the two together made the body's rhythm unreadable: the
            # sample's blank density over its first five pages is 15% with
            # the cover and 11% without. Its position is still reported, as
            # a note rather than a failure.
            if len(scanned) > 1:
                continue
        rows_total += len(rows)
        splits_total += splits
        chars += sum(len(r[3]) for r in rows)
        tops = [r[2] for r in rows]
        deltas += [b - a for a, b in zip(tops, tops[1:]) if 0 < b - a < 120]

    pitch = statistics.median(deltas) if deltas else 0.0
    blanks = (sum(max(0, round(d / pitch) - 1) for d in deltas)
              if pitch else 0)
    return {
        "pages": doc.page_count,
        "rows": rows_total,
        "splits": splits_total,
        "split_rate": splits_total / rows_total if rows_total else 0.0,
        "blank_density": blanks / rows_total if rows_total else 0.0,
        "chars_per_row": chars / rows_total if rows_total else 0.0,
        "page1_top": first_page_top,
        "pitch": pitch,
    }


def _band(sample: float, tolerance: float) -> tuple[float, float]:
    return sample * (1 - tolerance), sample * (1 + tolerance)


def compare(sample: dict, output: dict) -> tuple[list[str], list[str]]:
    """Returns (problems, notes). Notes are differences worth stating that
    are not failures — a deliberately filled cover page is the usual one."""
    problems, notes = [], []

    # Stretched lines. Compared as a *rate* because the output is a different
    # length; compared against the sample's own rate because a sample that
    # stretches lines is entitled to an output that does.
    limit = max(sample["split_rate"] * MAX_SPLIT_RATIO, 0.02)
    if output["split_rate"] > limit:
        problems.append(
            f"stretched lines: {output['splits']} of {output['rows']} rows "
            f"({output['split_rate']:.0%}) against the sample's "
            f"{sample['split_rate']:.0%} — paragraphs are ending on a "
            f"partly-filled line. Usually means each block holds about one "
            f"line of text instead of a whole paragraph.")

    if sample["blank_density"]:
        low, high = _band(sample["blank_density"], BLANK_DENSITY_TOLERANCE)
        if not low <= output["blank_density"] <= high:
            problems.append(
                f"vertical rhythm: {output['blank_density'] * 100:.1f} blank "
                f"lines per 100 rows against the sample's "
                f"{sample['blank_density'] * 100:.1f} — "
                f"{'too few' if output['blank_density'] < low else 'too many'} "
                f"empty paragraphs.")

    if sample["chars_per_row"]:
        low, high = _band(sample["chars_per_row"], ROW_FILL_TOLERANCE)
        if not low <= output["chars_per_row"] <= high:
            problems.append(
                f"line fill: {output['chars_per_row']:.0f} characters per row "
                f"against the sample's {sample['chars_per_row']:.0f} — rows "
                f"are {'emptier' if output['chars_per_row'] < low else 'denser'} "
                f"than the sample's.")

    if sample["page1_top"] is not None and output["page1_top"] is not None:
        drift = abs(output["page1_top"] - sample["page1_top"])
        if drift > PAGE1_OFFSET_TOL_PT:
            notes.append(
                f"page 1 starts at y={output['page1_top']:.0f} where the "
                f"sample starts at y={sample['page1_top']:.0f}. If you added "
                f"cover-page content the sample did not have, this is "
                f"expected — say so. If you did not, the leading blank "
                f"paragraphs were dropped.")

    return problems, notes


if __name__ == "__main__":
    if len(sys.argv) not in (3, 4):
        print("Usage: python verify_render.py <sample.pdf> <output.pdf> [sample_pages]")
        raise SystemExit(2)
    output_measure = measure(sys.argv[2])
    # Like for like. The output is rebuilt from the first few pages of the
    # sample, and the sample's later pages are denser — measured on this
    # convention's methodichka, the split rate over all 47 pages is 18.3%
    # against 7.1% over the first four, so comparing a 5-page output with the
    # whole sample excuses exactly the defect this script exists to catch.
    # Default: the same number of pages the output has.
    if len(sys.argv) == 4:
        window = range(int(sys.argv[3]))
    else:
        window = range(output_measure["pages"])
    sample_measure = measure(sys.argv[1], window)
    print(f"sample measured over its first {len(list(window))} page(s), "
          f"to match the output's extent\n")

    print(f"{'':<16}{'sample':>10}{'output':>10}")
    for key, fmt in (("pages", "{:.0f}"), ("rows", "{:.0f}"),
                     ("split_rate", "{:.1%}"), ("blank_density", "{:.1%}"),
                     ("chars_per_row", "{:.1f}"), ("pitch", "{:.2f}")):
        print(f"  {key:<14}"
              f"{fmt.format(sample_measure[key]):>10}"
              f"{fmt.format(output_measure[key]):>10}")

    problems, notes = compare(sample_measure, output_measure)
    for note in notes:
        print(f"\nNOTE: {note}")
    if problems:
        print("\nRENDER DOES NOT MATCH THE SAMPLE:")
        for p in problems:
            print(" -", p)
        raise SystemExit(1)
    print("\nRender matches the sample's rhythm.")
