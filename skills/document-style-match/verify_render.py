"""Compare a rendered result against the *sample*, not against the contract.

Usage: python verify_render.py <sample.pdf> <output.pdf> [--plan <content_plan.json>] [pages]

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

Pass `--plan` and every failure names the *blocks* to change. Without it this
reports a number and the reader has to find the cause: measured, a session
that met one `stretched lines` figure spent twenty tool calls and nine
throwaway scripts re-deriving which rows produced it, then read all six of
this skill's scripts (~27k tokens) looking for the answer. A metric is not an
instruction.

Exits 0 with "Render matches the sample's rhythm." or 1 with the specific
drift. A drift here is not automatically a bug — a title page the user asked
to fill with teacher names legitimately moves, and the report says so rather
than failing silently either way.
"""
import json
import math
import re
import statistics
import sys
from collections import Counter

import fitz

# Same guard, same reason as `run.py`: piped, Python picks the console
# codepage for stdout, and cp1251 cannot encode the arrow this script draws
# between a rendered row and the block it came from. Without it the *report*
# raises UnicodeEncodeError and the failure it was explaining is lost.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

#: Two fragments this close in y are the same visual line. Same value and the
#: same reason as `analyze_pdf._ROW_TOLERANCE_PT`.
ROW_TOLERANCE_PT = 1.5

#: How many times the sample's own rate of extractor-split lines the output
#: may show. The sample has some: a justified line whose word spacing grew
#: large is split by every PDF extractor. Many more than the sample means the
#: output is stretching lines the sample did not.
MAX_SPLIT_RATIO = 1.5

#: Fragments a row must break into before it counts as a stretched line.
#:
#: Justification spreads its slack across *every* word gap on the line, so an
#: extractor that breaks a stretched line breaks it into many pieces — the
#: measured ones came apart into 6 and 7. A row that breaks into exactly two
#: is a column gutter: a table cell boundary, or the run boundary either side
#: of a hyperlink. Counting those as stretched lines is a false positive by
#: construction, and it was not a small one — an 11-row `Варіанти` table
#: contributed 11 of one output's 28 "splits", pushing 15% over a 17.6%
#: limit. The session that met that number deleted the table to satisfy it,
#: which is a content change forced by a measurement artifact, and this
#: script's own advice ("FIX THE TEXT, NOT THE RENDERER") was wrong for 40%
#: of the figure it was attached to.
MIN_STRETCH_FRAGMENTS = 3

#: How far apart the widest and narrowest gap on a row may be and still read
#: as justification. Justification distributes slack evenly, so its gaps are
#: near-identical — measured on a real stretched line: 15.4, 15.5, 15.6,
#: 15.5, 15.6, 15.6 pt. Column gutters in a three-column table are not.
MAX_GAP_SPREAD = 2.0

#: A fragment that begins at the same x on this many rows of one page is a
#: column, not a coincidence.
#:
#: The two tests above still miss the case a table is *most* likely to hit:
#: three or more columns of equal width, whose gutters are as uniform as
#: justification's. Measured on the sample's page 5, the row
#: `Ключове | Параметр 1 | Параметр 2` came apart into three fragments with
#: even gaps and read as a stretched line. Where a justified line breaks is
#: decided by word lengths and never repeats down the page; a column boundary
#: repeats on every row of its table, which is what this looks for.
MIN_COLUMN_ROWS = 3
COLUMN_X_TOLERANCE_PT = 2.0

#: Blank lines per 100 text rows, as a share of the sample's rate.
#:
#: Wide, and deliberately so. The metric divides by rows, and rows scale with
#: how much prose each section holds — so an output that reproduced every one
#: of the sample's spacers still reads low when its sections are longer than
#: the sample's. Measured: two rebuilds of the same methodichka, one correct
#: and one built on the wrong paper size, came in at 10.7% and 9.9% against a
#: sample's 15.5% — the metric failed both and separated neither. At 0.45 the
#: failure this was written for (a rebuild carrying a *third* of the sample's
#: blank lines) is still caught, and the absolute spacer count — which does
#: not have the coupling — is checked properly by `verify_docx.verify_rhythm`
#: against the structure file.
BLANK_DENSITY_TOLERANCE = 0.45

#: Median row-to-row distance, as a share of the sample's. This is line
#: spacing as the page actually renders it, and nothing else here could see
#: it: a rebuild left at python-docx's default 1.15 against a sample's 1.5
#: measured 18.60 against 24.12 — a fifth of the page's vertical rhythm gone —
#: while `split_rate`, `blank_density` and `chars_per_row` all passed it. The
#: band is tight because pitch follows from body size and line spacing, both
#: of which the contract fixes; drift here means one of them was not applied.
PITCH_TOLERANCE = 0.12

#: Mean characters per rendered row, as a share of the sample's.
ROW_FILL_TOLERANCE = 0.30

#: How far the first line of page 1 may sit from where the sample's does.
PAGE1_OFFSET_TOL_PT = 40.0

#: Offending rows named per failure. Enough to see the pattern, few enough
#: that the report stays readable.
MAX_OFFENDERS_REPORTED = 5


def _bucket(x: float) -> int:
    return round(x / COLUMN_X_TOLERANCE_PT)


def _column_starts(groups) -> set:
    """x buckets where a fragment begins on several rows of the same page."""
    seen: Counter = Counter()
    for group in groups:
        for fragment in group[1:]:
            seen[_bucket(fragment[0])] += 1
    return {bucket for bucket, n in seen.items() if n >= MIN_COLUMN_ROWS}


def _is_stretch(group, columns) -> bool:
    """Did justification pull this row apart, or is it a column gutter?

    `group` is the fragments merged onto one visual line, in x order, as
    (x0, x1, text, y0). See MIN_STRETCH_FRAGMENTS.
    """
    if len(group) < MIN_STRETCH_FRAGMENTS:
        return False
    if all(_bucket(f[0]) in columns for f in group[1:]):
        return False          # every break sits on a repeating column boundary
    gaps = [b[0] - a[1] for a, b in zip(group, group[1:])]
    gaps = [g for g in gaps if g > 0]
    if len(gaps) < 2:
        return False
    return max(gaps) / min(gaps) <= MAX_GAP_SPREAD


def _rows(page):
    """Visual lines of a page, fragments merged.

    Returns (rows, stretched, column_merges):
      rows       — [(x0, x1, y0, text)], one per visual line
      stretched  — [(y0, text, fragment_count)] for the justification splits
      column_merges — fragment joins that were *not* stretched lines
    """
    frags = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            spans = [s for s in line.get("spans", []) if s["text"].strip()]
            if spans:
                frags.append((line["bbox"][1], line["bbox"][0], line["bbox"],
                              "".join(s["text"] for s in spans)))
    frags.sort(key=lambda f: (f[0], f[1]))

    groups: list[list[tuple]] = []
    for y0, _x0, bbox, text in frags:
        if groups and abs(y0 - groups[-1][0][3]) <= ROW_TOLERANCE_PT:
            groups[-1].append((bbox[0], bbox[2], text, groups[-1][0][3]))
            continue
        groups.append([(bbox[0], bbox[2], text, y0)])

    columns = _column_starts(groups)
    rows, stretched, column_merges = [], [], 0
    for group in groups:
        y0 = group[0][3]
        text = " ".join(g[2] for g in group)
        rows.append((min(g[0] for g in group), max(g[1] for g in group), y0, text))
        if len(group) > 1:
            if _is_stretch(group, columns):
                stretched.append((y0, text, len(group)))
            else:
                column_merges += len(group) - 1
    return rows, stretched, column_merges


def measure(pdf_path: str, pages=None) -> dict:
    doc = fitz.open(pdf_path)
    scanned = range(doc.page_count) if pages is None else pages
    scanned = [p for p in scanned if p < doc.page_count]

    rows_total = splits_total = merges_total = 0
    chars = 0
    deltas: list[float] = []
    offenders: list[tuple] = []
    first_page_top = None
    for i, page_no in enumerate(scanned):
        rows, stretched, merges = _rows(doc[page_no])
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
        splits_total += sum(n - 1 for _, _, n in stretched)
        merges_total += merges
        offenders += [(page_no + 1, y, text, n) for y, text, n in stretched]
        chars += sum(len(r[3]) for r in rows)
        tops = [r[2] for r in rows]
        deltas += [b - a for a, b in zip(tops, tops[1:]) if 0 < b - a < 120]

    pitch = statistics.median(deltas) if deltas else 0.0
    blanks = (sum(max(0, round(d / pitch) - 1) for d in deltas)
              if pitch else 0)
    offenders.sort(key=lambda o: -o[3])
    return {
        "pages": doc.page_count,
        "rows": rows_total,
        "splits": splits_total,
        "column_merges": merges_total,
        "split_rate": splits_total / rows_total if rows_total else 0.0,
        "blank_density": blanks / rows_total if rows_total else 0.0,
        "blanks": blanks,
        "chars_per_row": chars / rows_total if rows_total else 0.0,
        "page1_top": first_page_top,
        "pitch": pitch,
        "offenders": offenders,
    }


# ── naming the blocks ───────────────────────────────────────────────────────
#
# A metric says a document is wrong; a block index says which paragraph to
# edit. The join is done on text rather than coordinates because coordinates
# only exist after the PDF conversion, and the renderer that knows the block
# boundaries has long since exited.

def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _plan_entries(plan_path: str) -> list[dict]:
    try:
        payload = json.load(open(plan_path, encoding="utf-8"))
    except (OSError, ValueError):
        return []
    blocks = payload["blocks"] if isinstance(payload, dict) else payload
    entries = []
    for i, b in enumerate(blocks):
        if not isinstance(b, dict):
            continue
        text = _norm(b.get("text") or "")
        if not text:
            continue
        entries.append({"i": i, "source_index": b.get("source_index"),
                        "text": text, "chars": len(text),
                        "target": b.get("target_chars")})
    return entries


def _locate(row_text: str, entries: list[dict]) -> dict | None:
    """The plan block a rendered row came from, or None."""
    key = _norm(row_text)
    if not key:
        return None
    for entry in entries:
        if key in entry["text"]:
            return entry
    # A row broken across a page boundary, or one the extractor spaced
    # differently: fall back to its opening words.
    head = " ".join(key.split()[:4])
    if len(head) < 12:
        return None
    for entry in entries:
        if head in entry["text"]:
            return entry
    return None


def _describe(entry: dict | None) -> str:
    if entry is None:
        return "block ?"
    label = f"block {entry['i']}"
    if entry["source_index"] is not None:
        label += f" (source_index {entry['source_index']})"
    if entry["target"]:
        target = int(entry["target"])
        drift = entry["chars"] / target if target else 1.0
        # The direction matters and used to be assumed. A block under its
        # target ends on a half-empty line and wants more prose; a block well
        # over it wraps differently from the sample's and wants trimming or
        # splitting. Telling the second one to "lengthen" is how a fix makes
        # the number worse.
        if drift < 0.85:
            verb = "LENGTHEN"
        elif drift > 1.25:
            verb = "TRIM or SPLIT"
        else:
            verb = "reword"
        label += f"  wrote {entry['chars']} / target {target} → {verb}"
    return label


def _offender_lines(output: dict, entries: list[dict]) -> list[str]:
    lines = []
    offenders = output.get("offenders") or []
    for page_no, y, text, frags in offenders[:MAX_OFFENDERS_REPORTED]:
        preview = _norm(text)[:52]
        lines.append(f"     p{page_no} y={y:.0f}  \"{preview}…\"  "
                     f"→ {_describe(_locate(text, entries))}")
    extra = len(offenders) - MAX_OFFENDERS_REPORTED
    if extra > 0:
        lines.append(f"     … and {extra} more stretched rows")
    return lines


def _band(sample: float, tolerance: float) -> tuple[float, float]:
    return sample * (1 - tolerance), sample * (1 + tolerance)


def compare(sample: dict, output: dict, entries=()) -> tuple[list[str], list[str]]:
    """Returns (problems, notes). Notes are differences worth stating that
    are not failures — a deliberately filled cover page is the usual one."""
    problems, notes = [], []
    entries = list(entries)

    # Stretched lines. Compared as a *rate* because the output is a different
    # length; compared against the sample's own rate because a sample that
    # stretches lines is entitled to an output that does.
    limit = max(sample["split_rate"] * MAX_SPLIT_RATIO, 0.02)
    if output["split_rate"] > limit:
        detail = "\n".join(_offender_lines(output, entries))
        problems.append(
            f"stretched lines: {output['splits']} of {output['rows']} rows "
            f"({output['split_rate']:.0%}) against the sample's "
            f"{sample['split_rate']:.0%}  [limit {limit:.0%}]\n"
            f"   these rows end short and justification is spreading the word "
            f"gaps to fill them. Bring the blocks named below towards their "
            f"target_chars — each line says which direction. Swapping "
            f"renderers cannot move this number: one session spent half a turn "
            f"proving that with reportlab and Word COM in turn.\n"
            + (detail if detail else
               "   (pass --plan <content_plan.json> to name the blocks)"))

    if output.get("column_merges"):
        notes.append(
            f"{output.get('column_merges', 0)} column merges (table cell boundaries, "
            f"hyperlink runs) were seen and are NOT counted as stretched "
            f"lines — they are how a table renders, not a justification "
            f"defect.")

    if sample["blank_density"]:
        low, high = _band(sample["blank_density"], BLANK_DENSITY_TOLERANCE)
        if output["blank_density"] < low:
            need = max(1, math.ceil(low * output["rows"]) - output.get("blanks", 0))
            problems.append(
                f"vertical rhythm: {output['blank_density'] * 100:.1f} blank "
                f"lines per 100 rows against the sample's "
                f"{sample['blank_density'] * 100:.1f} — sections run into each "
                f"other.\n"
                f"   FIX: add {need} more {{\"kind\": \"spacer\"}} block"
                f"{'s' if need > 1 else ''} to the plan. Put them where the "
                f"sample has two consecutive spacers and the plan has one — "
                f"after a section heading, and between the closing sections.")
        elif output["blank_density"] > high:
            # Not a failure. Extra breathing room is what a filled cover page
            # and a shorter body look like, and failing it sent one run
            # deleting spacers it had correctly reproduced.
            notes.append(
                f"{output['blank_density'] * 100:.1f} blank lines per 100 rows "
                f"against the sample's {sample['blank_density'] * 100:.1f} — "
                f"more open than the sample. Expected if your sections are "
                f"shorter than its; worth a look if they are not.")

    if sample["pitch"] and output["pitch"]:
        low, high = _band(sample["pitch"], PITCH_TOLERANCE)
        if not low <= output["pitch"] <= high:
            ratio = output["pitch"] / sample["pitch"]
            problems.append(
                f"line pitch: {output['pitch']:.2f}pt between rows against the "
                f"sample's {sample['pitch']:.2f} ({ratio:.0%}) — the rendered "
                f"line spacing is "
                f"{'tighter' if ratio < 1 else 'looser'} than the sample's.\n"
                f"   FIX: `line_spacing` and `body_size_pt` from the contract "
                f"did not reach the document. python-docx defaults to 1.15 and "
                f"writes nothing unless told, which is exactly this number "
                f"against a 1.5 sample.")

    if sample["chars_per_row"]:
        low, high = _band(sample["chars_per_row"], ROW_FILL_TOLERANCE)
        if not low <= output["chars_per_row"] <= high:
            emptier = output["chars_per_row"] < low
            fix = ("give the blocks more prose (check target_chars)" if emptier
                   else "the body font or size is smaller than the contract says")
            problems.append(
                f"line fill: {output['chars_per_row']:.0f} characters per row "
                f"against the sample's {sample['chars_per_row']:.0f} — rows "
                f"are {'emptier' if emptier else 'denser'} than the sample's.\n"
                f"   FIX: {fix}.")

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


def _parse_argv(argv: list[str]) -> tuple[str, str, str | None, int | None]:
    plan, positional = None, []
    i = 0
    while i < len(argv):
        if argv[i] == "--plan" and i + 1 < len(argv):
            plan, i = argv[i + 1], i + 2
            continue
        positional.append(argv[i])
        i += 1
    if len(positional) not in (2, 3):
        raise ValueError
    pages = int(positional[2]) if len(positional) == 3 else None
    return positional[0], positional[1], plan, pages


if __name__ == "__main__":
    try:
        sample_pdf, output_pdf, plan_path, sample_pages = _parse_argv(sys.argv[1:])
    except (ValueError, IndexError):
        print("Usage: python verify_render.py <sample.pdf> <output.pdf> "
              "[--plan <content_plan.json>] [sample_pages]")
        raise SystemExit(2)

    output_measure = measure(output_pdf)
    # Like for like. The output is rebuilt from the first few pages of the
    # sample, and the sample's later pages are denser — measured on this
    # convention's methodichka, the split rate over all 47 pages is 18.3%
    # against 7.1% over the first four, so comparing a 5-page output with the
    # whole sample excuses exactly the defect this script exists to catch.
    # Default: the same number of pages the output has.
    window = range(sample_pages if sample_pages is not None
                   else output_measure["pages"])
    sample_measure = measure(sample_pdf, window)
    print(f"sample measured over its first {len(list(window))} page(s), "
          f"to match the output's extent\n")

    print(f"{'':<16}{'sample':>10}{'output':>10}")
    for key, fmt in (("pages", "{:.0f}"), ("rows", "{:.0f}"),
                     ("split_rate", "{:.1%}"), ("blank_density", "{:.1%}"),
                     ("chars_per_row", "{:.1f}"), ("pitch", "{:.2f}")):
        print(f"  {key:<14}"
              f"{fmt.format(sample_measure[key]):>10}"
              f"{fmt.format(output_measure[key]):>10}")

    entries = _plan_entries(plan_path) if plan_path else []
    problems, notes = compare(sample_measure, output_measure, entries)
    for note in notes:
        print(f"\nNOTE: {note}")
    if problems:
        print("\nRENDER DOES NOT MATCH THE SAMPLE:")
        for p in problems:
            print(" -", p)
        raise SystemExit(1)
    print("\nRender matches the sample's rhythm.")
