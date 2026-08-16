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

`body_font` is the mode of span font families on the body pages (style
suffixes like ",Bold"/",Italic" stripped before counting, or three variants
of the same family split the vote). Two live sessions each wrote a
throwaway script to get this exact value via `get_fonts()`/span inspection
before it was measured here — Step 3 already reads `c.get("body_font")`.

`blocks` is a structure snapshot: one entry per rendered line on the title
page plus the body pages, in reading order, each carrying its own measured
text/alignment/bold/size/indent. This is not a paragraph-exact snapshot —
a rendered PDF has no paragraph objects, only glyph positions, so a
multi-line paragraph becomes several consecutive `justify` blocks here
rather than one (a DOCX sample does not have this limit — see
`analyze_docx.py`). It replaces, not supplements, the throwaway
`inspect_pdf*.py`/`probe_align.py` scripts two live sessions each wrote by
hand to get exactly this (line text + font size + bold + centered-or-not)
before generating: read `blocks` instead of re-deriving it.
"""
import re
import sys
import json
import statistics
from collections import Counter

import fitz

# One implementation of the contract file format, shared by both
# analyzers — the split, the previewing and the ensure_ascii=False all
# have to agree or the renderer and verifier see two different shapes.
from analyze_docx import (_structure_path, _write, _write_structure,
                          split_contract)

_BLOCK_LIMIT = 400

# A numbered/bulleted list item ("2.Question text", "- item") sits inside
# generous margins on both sides when it is short, which reads exactly like
# a centered heading to the gap-symmetry check below — measured: two
# numbered questions in a "Контрольні запитання" list were centered by this
# accident. The marker itself is a much cheaper, unambiguous signal: a list
# item is never a centered heading in this document convention, so it wins
# outright regardless of geometry.
_LIST_MARKER_RE = re.compile(r"^(\d+[.)]|[-–—])\s*\S")

#: How tall one single-spaced line is, as a multiple of the font size. Word
#: does not lay out a 14pt line in 14pt — it uses the font's own line height,
#: which for the Times New Roman family this convention uses is ~1.15x.
#: Confirmed both ways on a live pair: the sample's 24.12pt median pitch at
#: 14pt body is 1.5 line spacing (14 x 1.15 x 1.5 = 24.15), and a document
#: this renderer produced at its 1.15 default measured back as 18.48pt
#: (14 x 1.15 x 1.15 = 18.52).
_SINGLE_LINE_FACTOR = 1.15
#: Line spacings a document is actually authored with. A measurement inside
#: `_SPACING_SNAP` of one of these is reported as that value: 1.498 is 1.5
#: set in Word, not a distinct spacing, and reporting it raw makes a contract
#: that reads like a measurement error.
_COMMON_SPACINGS = (1.0, 1.15, 1.5, 2.0)
_SPACING_SNAP = 0.08


def _line_spacing_of(doc, pages, body_size_pt: float | None) -> float | None:
    """Line spacing as a multiple, measured from consecutive line tops.

    Not in the contract at all until now, and `verify_docx` skips any field
    the contract lacks (`expected is None -> continue`) — so on the PDF route
    this was the one number that could not fail a check. Measured live: a
    generated document came back at single spacing against a sample set to
    1.5, and all three checks passed.

    The median, not the mode: pitch jitters over a tenth of a point
    (24.1/24.2/24.3 on the same page), which splits a mode three ways, and
    bucketing to consolidate it splits differently depending on where the
    boundary lands. Paragraph gaps and spacer lines sit in the same
    population but are a minority, and the median ignores them.
    """
    if not body_size_pt:
        return None
    deltas: list[float] = []
    for page_no in pages:
        tops = sorted(
            line["bbox"][1]
            for block in doc[page_no].get_text("dict")["blocks"]
            for line in block.get("lines", [])
            if any(s["text"].strip() for s in line.get("spans", [])))
        deltas += [b - a for a, b in zip(tops, tops[1:]) if 0 < b - a < 80]
    if len(deltas) < 8:
        return None
    ratio = statistics.median(deltas) / (body_size_pt * _SINGLE_LINE_FACTOR)
    for common in _COMMON_SPACINGS:
        if abs(ratio - common) <= _SPACING_SNAP:
            return common
    return round(ratio, 2)


def _body_indent_of(blocks: list[dict]) -> float | None:
    """The paragraph first-line indent, as the mode of the non-zero ones.

    Read off the blocks this module already measured rather than off raw
    line-start positions: the raw positions put list-item *continuation*
    lines (1.88cm here, 19 lines once near-identical values merge) ahead of
    the real body indent (1.25cm, 18 lines), and which one wins depends on
    the bucket width. The block pass has already decided what is a list item
    and what is an indented body line.

    Zero is excluded deliberately — flush-left lines are the majority in a
    justified document (29 of them here), and the question this answers is
    "when a paragraph *is* indented, by how much".
    """
    indents = Counter(
        b["indent_cm"] for b in blocks
        if b.get("align") == "justify" and not b.get("bold")
        and b.get("indent_cm"))
    if not indents:
        return None
    return indents.most_common(1)[0][0]


def _line_sizes(page) -> Counter:
    sizes: Counter = Counter()
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                if span["text"].strip():
                    sizes[round(span["size"], 1)] += 1
    return sizes


def _line_fonts(page) -> Counter:
    # Font family only — "Times New Roman,Bold" and "Times New Roman,Italic"
    # must count as the same family, or the mode splits three ways and a
    # body page that is mostly plain text can lose to no family at all.
    fonts: Counter = Counter()
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                if span["text"].strip():
                    fonts[span["font"].split(",")[0]] += 1
    return fonts


#: Two fragments whose tops differ by less than this are on the same visual
#: line. PyMuPDF splits a *justified* line whose inter-word spacing grew large
#: into several "lines" at the identical y — measured on page 4 of the sample,
#: one line came back as 7 fragments, and recording them as 7 blocks produced
#: a literal staircase down the page in one generated document. Nothing else
#: over-merged: pages 1-3 were 3/27/28 fragments and 3/27/28 rows.
_ROW_TOLERANCE_PT = 1.5

#: The most blank paragraphs one gap may stand for. A cover page legitimately
#: needs a dozen; a hundred means the measurement went wrong, not that the
#: sample has a hundred empty paragraphs.
_MAX_SPACER_RUN = 24

#: How many rendered rows may be read per block of the final structure.
#: Rows become paragraphs at roughly 2:1 on this convention (84 -> 41 on the
#: sample); 4 leaves room for a document that wraps harder without letting a
#: pathological page read the whole file.
_ROWS_PER_BLOCK_ALLOWANCE = 4


def _rows_of_page(page) -> list[dict]:
    """One entry per visual line, fragments merged in reading order.

    Returns dicts with `y0`, `y1`, `x0`, `x1`, `text`, `spans` — the same
    things the caller used to read straight off a PyMuPDF line, except that a
    line the extractor split across several entries is one entry again.
    """
    frags = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            spans = [s for s in line.get("spans", []) if s["text"].strip()]
            if spans:
                frags.append((line["bbox"][1], line["bbox"][0], line["bbox"],
                              spans))
    frags.sort(key=lambda f: (f[0], f[1]))

    rows: list[dict] = []
    for y0, x0, bbox, spans in frags:
        if rows and abs(y0 - rows[-1]["y0"]) <= _ROW_TOLERANCE_PT:
            row = rows[-1]
            # A space between fragments: they were separate extractor lines,
            # so no whitespace survives between them, and joining bare would
            # weld the words of a stretched justified line together.
            row["text"] = f'{row["text"]} {"".join(s["text"] for s in spans)}'
            row["x1"] = max(row["x1"], bbox[2])
            row["y1"] = max(row["y1"], bbox[3])
            row["spans"].extend(spans)
            continue
        rows.append({"y0": y0, "y1": bbox[3], "x0": bbox[0], "x1": bbox[2],
                     "text": "".join(s["text"] for s in spans),
                     "spans": list(spans)})
    for row in rows:
        row["text"] = re.sub(r"\s+", " ", row["text"]).strip()
    return [r for r in rows if r["text"]]


def _line_pitch(rows_by_page: dict) -> float:
    """One single-spaced line, in points, as the median top-to-top step."""
    deltas = []
    for rows in rows_by_page.values():
        tops = [r["y0"] for r in rows]
        deltas += [b - a for a, b in zip(tops, tops[1:]) if b > a]
    return statistics.median(deltas) if deltas else 0.0


#: How close a line's indent must be to the body indent to count as a
#: paragraph's first line rather than a wrapped continuation.
_INDENT_MATCH_CM = 0.35


def _paragraphs_of(blocks: list[dict]) -> list[dict]:
    """Wrapped lines merged back into the paragraphs they came from.

    A PDF has no paragraphs to read — only rendered lines — and every line
    was being recorded as a block, which `render_from_structure` then turned
    into a paragraph of its own. Word does not justify a paragraph's *last*
    line, so a document rebuilt this way alternates a stretched line with a
    short one every line or two, and reads as broken mid-sentence. Measured
    across three live rebuilds of the same sample: 87-95 paragraphs of 56-71
    characters against a column that holds ~47, i.e. almost every paragraph
    ending on a partly-filled line; one of them came back with 18 lines the
    extractor had to re-split, against the sample's own 6.

    The rule, and why each half of it is needed:

    * The body's first-line indent is the *mode* of the non-zero indents on
      justified text (1.25cm in the sample). A line at that indent starts a
      paragraph.
    * A **larger** indent is a hanging continuation inside a list item, not a
      new paragraph — the sample has 1.73/1.88/1.89cm continuation lines, and
      treating "indent > 0" as a paragraph start splits every list item into
      one paragraph per line (measured: 61 paragraphs instead of 41).
    * Alignment, boldness, a list marker and a page break all end a paragraph
      too: a centred heading is never a continuation of justified body text.

    `source_rows` and `target_chars` ride along because they are what a
    content plan cannot otherwise know: how much text belonged here. Without
    them a plan of 4,895 characters and one of 6,711 both "match the shape".
    """
    body_indent = Counter(
        b["indent_cm"] for b in blocks
        if b.get("align") == "justify" and b.get("indent_cm")).most_common(1)
    body_indent = body_indent[0][0] if body_indent else None

    merged: list[dict] = []
    current: dict | None = None
    for b in blocks:
        if b.get("kind"):
            merged.append(b)
            current = None
            continue
        text = b.get("text", "")
        indent = b.get("indent_cm") or 0.0
        starts = (
            current is None
            or b.get("align") != "justify"
            or current.get("align") != "justify"
            or bool(b.get("bold")) != bool(current.get("bold"))
            or abs(b.get("size_pt") or 0) != abs(current.get("size_pt") or 0)
            or b.get("page_break_before")
            or b.get("style_name") != current.get("style_name")
            or _LIST_MARKER_RE.match(text)
            or (body_indent is not None
                and abs(indent - body_indent) < _INDENT_MATCH_CM)
            # No body indent could be measured (a document that indents
            # nothing): fall back to "any indent starts a paragraph", which
            # is the best available signal and what the sample-less case had.
            or (body_indent is None and indent > 0)
        )
        if starts:
            current = dict(b)
            current["source_rows"] = 1
            merged.append(current)
        else:
            current["text"] = f'{current["text"].rstrip()} {text.lstrip()}'
            current["source_rows"] += 1
    for b in merged:
        if not b.get("kind"):
            b["target_chars"] = len(b.get("text", ""))
    return merged


def _blocks(doc, pages, page_width_pt: float, left_margin_pt: float, right_margin_pt: float,
            title_size: float | None, *, limit: int = 120) -> list[dict]:
    right_edge_pt = page_width_pt - right_margin_pt
    content_width_pt = right_edge_pt - left_margin_pt
    # Tolerances in points. Alignment is read off the *gap* on each side of
    # the line, not its midpoint — a long justified line's midpoint also
    # sits near the page center (it spans most of the content width), which
    # first misclassified full-width body lines as centered headings
    # (measured: "Мета роботи - ..." and a body paragraph's own first line
    # both did this). A gap pair close to (0, 0) is ordinary full-justified
    # text; close to (x, x) for any x is centered, however wide; close to
    # (large, ~0) with a short line is right-aligned (this convention's
    # right-flushed labels, e.g. "Зауваження").
    EDGE_TOL, INDENT_TOL, SYMMETRY_TOL = 15.0, 8.0, 30.0
    # A section that deliberately starts on a new page leaves the previous
    # page ending early. A heading that merely *flowed* to the top of a
    # page follows a full page. Requiring the previous page to end above
    # this fraction of its text height is what separates the two — without
    # it, every heading landing at a page top was marked, which would
    # force breaks the sample does not have.
    EARLY_END_FRACTION = 0.85

    # A rendered PDF has no empty paragraphs to read — the vertical rhythm is
    # only visible as extra distance between consecutive line *tops*. Two
    # things were wrong with reading it one line at a time against a rolling
    # median: a gap of several blank lines emitted a single spacer, and blank
    # space *before the first line of a page* was invisible, because there is
    # no previous line on that page to measure from. The second is what left
    # every generated cover page jammed against the top margin while the
    # sample's title sits a third of the way down — measured on the sample:
    # 12 blank lines on page 1, and 9 more between sections on pages 2-4,
    # against the 6 spacers this function used to report for all four pages.
    rows_by_page = {p: _rows_of_page(doc[p]) for p in pages}
    pitch = _line_pitch(rows_by_page)
    # The top of the text area, measured rather than assumed: the smallest
    # first-row top across the scanned pages. Self-correcting — if every page
    # starts low, that is the margin and nothing is reported as leading blanks.
    text_top = min((rows[0]["y0"] for rows in rows_by_page.values() if rows),
                   default=0.0)

    def _blank_lines(distance: float) -> int:
        """How many empty paragraphs a vertical gap stands for."""
        if pitch <= 0:
            return 0
        return max(0, min(_MAX_SPACER_RUN, round(distance / pitch) - 1))

    blocks: list[dict] = []
    prev_bottom = prev_page = None
    for page_no in pages:
        page_height = doc[page_no].rect.height
        rows = rows_by_page[page_no]
        for row_index, row in enumerate(rows):
            text = row["text"]
            spans = row["spans"]
            x0, x1 = row["x0"], row["x1"]
            y0, y1 = row["y0"], row["y1"]
            new_page = prev_page is not None and page_no != prev_page
            if row_index == 0:
                # Blank space above the first line of a page. `+ pitch`
                # because `_blank_lines` counts the gap *between* two lines
                # and here there is no line above — the distance from the
                # text top to this row is entirely blank.
                for _ in range(_blank_lines(y0 - text_top + pitch)):
                    blocks.append({"kind": "spacer"})
                ended_early = (new_page and prev_bottom is not None
                               and prev_bottom < page_height * EARLY_END_FRACTION)
            else:
                ended_early = False
                for _ in range(_blank_lines(y0 - rows[row_index - 1]["y0"])):
                    blocks.append({"kind": "spacer"})
            if len(blocks) >= limit:
                return blocks[:limit]
            left_gap, right_gap = x0 - left_margin_pt, right_edge_pt - x1
            size = round(spans[0]["size"], 1)
            is_short = (x1 - x0) < content_width_pt * 0.5
            if title_size is not None and abs(size - title_size) < 0.5:
                # The cover page routinely uses its own margins, distinct
                # from the body pages' measured ones (confirmed: this
                # sample's title lines have visibly unequal gaps despite
                # being visually centered) — geometry alone misreads
                # them. Title-size text in this document convention is
                # centered essentially without exception, so trust that
                # over the gap measurement for this one size class.
                align = "center"
            elif _LIST_MARKER_RE.match(text):
                align = "justify"
            elif left_gap < INDENT_TOL:
                # Starts flush at the left margin — a short line here is
                # a list item or a paragraph's last wrapped line, not a
                # heading. Deciding this on left_gap alone (not also
                # requiring right_gap small) matters for a short list
                # item whose right-side gap happens to roughly match its
                # near-zero left one by coincidence — measured: a
                # "Контрольні запитання" list item was centered by this
                # accident before the two-sided check was narrowed to one.
                align = "justify"
            elif is_short and right_gap < EDGE_TOL and left_gap > EDGE_TOL:
                align = "right"
            elif abs(left_gap - right_gap) < SYMMETRY_TOL and max(left_gap, right_gap) > INDENT_TOL:
                align = "center"
            else:
                align = "justify"
            indent_cm = 0.0
            if align == "justify" and left_gap > INDENT_TOL:
                indent_cm = round(left_gap / 72 * 2.54, 2)
            entry = {
                "text": text,
                "align": align,
                "bold": any(s["flags"] & 16 for s in spans),
                "size_pt": size,
                "indent_cm": indent_cm,
            }
            # A heading sitting at the very top of a page is a section
            # that starts on a new page. Measured on this convention's
            # sample: all six "ЛАБОРАТОРНА РОБОТА №N" headings sit at
            # y0 = 57pt, the top of the text area, and nothing else
            # heading-shaped does. Requiring heading shape as well as
            # position keeps an ordinary wrapped body line — which is
            # also first on its page — from being marked.
            if new_page and ended_early and (
                    align == "center" or entry["bold"]
                    or (title_size is not None and size >= title_size - 0.5)):
                entry["page_break_before"] = True
            blocks.append(entry)
            prev_bottom, prev_page = y1, page_no
            # The cap counts *rows* here and paragraphs on the way out, so it
            # is applied generously: merging is what decides how many blocks
            # a given number of rows becomes, and cutting rows at the block
            # limit would silently shorten the structure by whatever the
            # merge ratio happens to be (measured on the sample: 84 -> 41).
            if len(blocks) >= limit * _ROWS_PER_BLOCK_ALLOWANCE:
                return _paragraphs_of(blocks)[:limit]
    return _paragraphs_of(blocks)[:limit]


def analyze(pdf_path: str) -> dict:
    doc = fitz.open(pdf_path)
    page_rect = doc[0].rect  # page size read off the actual page — never assume A4/Letter

    title_sizes = _line_sizes(doc[0])
    body_sizes: Counter = Counter()
    body_fonts: Counter = Counter()
    line_starts: Counter = Counter()
    line_ends: list[float] = []

    body_pages = range(1, min(4, doc.page_count)) if doc.page_count > 1 else range(0, 1)
    for page_no in body_pages:
        page = doc[page_no]
        body_sizes.update(_line_sizes(page))
        body_fonts.update(_line_fonts(page))
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                text = "".join(s["text"] for s in line.get("spans", []))
                if not text.strip():
                    continue
                line_starts[round(line["bbox"][0], 1)] += 1
                line_ends.append(line["bbox"][2])

    title_size = title_sizes.most_common(1)[0][0] if title_sizes else None
    body_size = body_sizes.most_common(1)[0][0] if body_sizes else title_size
    body_font = body_fonts.most_common(1)[0][0] if body_fonts else None
    left_margin_pt = line_starts.most_common(1)[0][0] if line_starts else 0.0
    right_margin_pt = page_rect.width - max(line_ends) if line_ends else 0.0

    def pt_to_cm(pt: float) -> float:
        return round(pt / 72 * 2.54, 2)

    scanned = [0, *body_pages]
    blocks = _blocks(doc, scanned, page_rect.width, left_margin_pt,
                     right_margin_pt, title_size, limit=_BLOCK_LIMIT)

    contract = {
        "page_width_cm": pt_to_cm(page_rect.width),
        "page_height_cm": pt_to_cm(page_rect.height),
        "title_size_pt": title_size,
        "body_size_pt": body_size,
        "body_font": body_font,
        "margins_cm": {
            "left_margin": pt_to_cm(left_margin_pt),
            "right_margin": pt_to_cm(right_margin_pt),
            "top_margin": 2.0,   # convention — not measured, see module docstring
            "bottom_margin": 2.0,  # convention — not measured, see module docstring
        },
        "blocks": blocks,
        # Only the pages actually scanned are represented, so a reader can
        # see this is a sample of the document, not the whole of it.
        "_total_blocks": _count_lines(doc),
        # Which pages those were, and how many the document has. Reported
        # because `truncated: true` used to be the only signal and it reads
        # as "hit the 400-block limit" — on a 47-page sample this scanned 4
        # pages and recorded 96 blocks, and every model that saw it took the
        # limit as the explanation.
        "_pages_scanned": list(scanned),
        "_page_count": doc.page_count,
    }
    # Only set when measured. A key present-but-null would be read by
    # `verify_docx` as "no expectation" exactly like an absent one, but a
    # reader of the contract deserves to see the difference between "this
    # sample has no measurable body indent" and "this analyzer never looked".
    spacing = _line_spacing_of(doc, body_pages, body_size)
    if spacing is not None:
        contract["line_spacing"] = spacing
    body_indent = _body_indent_of(blocks)
    if body_indent is not None:
        contract["body_indent_cm"] = body_indent
    return contract


def _count_lines(doc) -> int:
    return sum(1 for page in doc
               for b in page.get_text("dict")["blocks"]
               for line in b.get("lines", [])
               if any(s["text"].strip() for s in line.get("spans", [])))


if __name__ == "__main__":
    if len(sys.argv) not in (3, 4):
        print("Usage: python analyze_pdf.py <sample.pdf> <contract.json> [structure.json]")
        raise SystemExit(2)
    full = analyze(sys.argv[1])
    total = full.pop("_total_blocks")
    scan = {"pages_scanned": full.pop("_pages_scanned"),
            "page_count": full.pop("_page_count")}
    style, structure = split_contract(full, total, scan=scan)
    contract_path = sys.argv[2]
    structure_path = sys.argv[3] if len(sys.argv) == 4 else _structure_path(contract_path)
    _write(contract_path, style)
    _write_structure(structure_path, structure)
    print(json.dumps(style, indent=2, ensure_ascii=False))
    print(f"\nstructure -> {structure_path} "
          f"({structure['blocks_recorded']} paragraphs from "
          f"{structure['rows_recorded']} of {structure['total_blocks']} rendered rows"
          f"{', TRUNCATED' if structure['truncated'] else ''})")
    # Said out loud, not left in the JSON, because it changes what the caller
    # can promise: a structure covering 4 of 47 pages does not contain the
    # sample's later sections, and a document rebuilt from it will be missing
    # them however well every check passes.
    if structure.get("truncated_reason") == "page_window":
        pages, count = len(scan["pages_scanned"]), scan["page_count"]
        print(f"\nNOTE: measured pages {scan['pages_scanned']} of {count} "
              f"({pages}/{count}). The structure covers only those pages — any "
              f"section of the sample living further in is NOT in it, and "
              f"cannot be reproduced from it. Say so when reporting.")
