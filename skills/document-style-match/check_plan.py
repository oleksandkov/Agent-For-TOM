"""Compare a content plan against the sample's structure, before rendering.

Usage: python check_plan.py <structure.json> <content_plan.json>

The plan is meant to be the structure with its words swapped. When it is
authored from scratch instead, the output stops resembling the sample in
ways every later check still passes: `verify_docx.py` compares *style
numbers*, and a document with the right margins and the wrong shape
matches the contract perfectly.

Measured on a live run against a coursework sample whose structure had
400 blocks, 116 spacers, 8 page breaks, 22 heading-styled paragraphs and
4 tables: the plan the model wrote had 57 blocks, 11 spacers, 7 breaks,
**0** headings and **0** tables, and the rendered result was 57
paragraphs against a 5497-paragraph sample. Nothing downstream objected.

This runs before `render_from_structure.py` so the drift is caught while
it is still cheap to fix — one script call instead of a render, a PDF
conversion and two verifications.

Exits 0 with "Plan matches the sample's shape." or 1 with the specific
drift. Ratios, not equality: a new topic legitimately needs a different
number of questions. Losing a *category* wholesale is the failure.
"""
import sys
import json

# A plan may reasonably carry fewer blocks than the sample — but not a
# fraction this small, which means it was written rather than adapted.
MIN_BLOCK_RATIO = 0.5
# Structural features are either reproduced or they are not; a plan that
# keeps under this share of them has dropped the category, not trimmed it.
MIN_FEATURE_RATIO = 0.5


def _counts(blocks) -> dict:
    return {
        "blocks": len(blocks),
        "spacers": sum(1 for b in blocks if b.get("kind") == "spacer"),
        "tables": sum(1 for b in blocks if b.get("kind") == "table"),
        "page breaks": sum(1 for b in blocks if b.get("page_break_before")),
        "styled headings": sum(1 for b in blocks if b.get("style_name")),
        "list items": sum(1 for b in blocks if b.get("list_id")),
    }


def _blocks_of(payload):
    return payload["blocks"] if isinstance(payload, dict) else payload


# Share of a plan's blocks that must carry a `source_index` pointing at a real
# block of the structure. Not 100%: inserting a few genuinely new blocks (a
# teacher line on the title page, an extra control question) is legitimate and
# expected. Losing the provenance of nearly all of them is not.
MIN_TRACEABLE_RATIO = 0.8

# How far a plan's total text volume may sit from the sample's. Generous on
# purpose — a new topic is not the same length as the old one — but a plan at
# a third of the sample's volume is not a rewrite of it, it is a summary.
# Measured across three live rebuilds of the same sample: 4,895 / 6,603 /
# 6,711 characters, all reported as matching the same shape.
VOLUME_TOLERANCE = 0.35


def _volume(blocks) -> tuple[int, int]:
    """(characters the plan writes, characters the sample had there).

    `target_chars` rides on each block of a measured structure. A plan block
    that carries one is being compared with the sample text it replaces; a
    genuinely new block has none and is excluded from both sides, so adding
    a title-page header does not look like padding.
    """
    written = expected = 0
    for b in blocks:
        target = b.get("target_chars")
        if target is None or b.get("kind"):
            continue
        written += len(b.get("text", ""))
        expected += int(target)
    return written, expected


def _volume_drift(plan_blocks) -> str | None:
    """Whether the plan writes roughly as much text as the sample held.

    The shape checks count *blocks*; this counts what is in them. A plan can
    reproduce every heading, spacer and page break of a sample and still be
    half its length, which is a different document at the same size on disk.
    """
    written, expected = _volume(plan_blocks)
    if not expected:
        return None
    low, high = 1 - VOLUME_TOLERANCE, 1 + VOLUME_TOLERANCE
    ratio = written / expected
    if low <= ratio <= high:
        return None
    direction = "short of" if ratio < low else "over"
    return (f"volume: plan writes {written} characters where the sample had "
            f"{expected} ({ratio:.0%}) — {direction} the ±{VOLUME_TOLERANCE:.0%} "
            f"tolerance. Each block's `target_chars` says how much text "
            f"belonged there; write about that much.")


def _traceability(sample_blocks, plan_blocks) -> str | None:
    """Whether the plan was adapted from the structure or written beside it.

    The count checks below cannot answer this, and that is the failure they
    exist to catch. Measured live: a plan authored from scratch carried 62
    blocks against the sample's 96 — comfortably over the 50% floor — and
    passed, then rendered a document with none of the sample's paragraph
    indents. Counts are a proxy for provenance; `source_index` is provenance.

    A structure written before this field existed has none to check, and
    silence is the right answer there: reporting every old structure as
    untraceable would train the reader to ignore the line that matters.
    """
    indexed = [b for b in sample_blocks if "source_index" in b]
    if not indexed:
        return None
    valid = {b["source_index"] for b in indexed}
    if not plan_blocks:
        return None
    traced = sum(1 for b in plan_blocks if b.get("source_index") in valid)
    if traced >= len(plan_blocks) * MIN_TRACEABLE_RATIO:
        return None
    return (f"provenance: {traced} of {len(plan_blocks)} plan blocks carry a "
            f"source_index from the structure (need "
            f"{int(len(plan_blocks) * MIN_TRACEABLE_RATIO)}) — this plan was "
            f"written from scratch, not adapted. Load the structure file's "
            f"blocks, replace each \"text\", and keep every other field "
            f"including source_index.")


def check(structure_path: str, plan_path: str) -> list[str]:
    sample_blocks = _blocks_of(json.load(open(structure_path, encoding="utf-8")))
    plan_blocks = _blocks_of(json.load(open(plan_path, encoding="utf-8")))
    sample = _counts(sample_blocks)
    plan = _counts(plan_blocks)

    problems = []
    for key, want in sample.items():
        if not want:
            continue
        got = plan[key]
        ratio = MIN_BLOCK_RATIO if key == "blocks" else MIN_FEATURE_RATIO
        if got < want * ratio:
            problems.append(
                f"{key}: plan has {got}, sample has {want}"
                + (" — the plan was written from scratch, not adapted from the structure"
                   if key == "blocks" else " — this part of the sample's shape is missing"))
    drift = _traceability(sample_blocks, plan_blocks)
    if drift:
        problems.append(drift)
    volume = _volume_drift(plan_blocks)
    if volume:
        problems.append(volume)
    return problems, sample, plan


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python check_plan.py <structure.json> <content_plan.json>")
        raise SystemExit(2)
    problems, sample, plan = check(sys.argv[1], sys.argv[2])
    width = max(len(k) for k in sample)
    print("            " + "  sample    plan")
    for key in sample:
        print(f"  {key:<{width}}  {sample[key]:>6}  {plan[key]:>6}")
    if problems:
        print("\nPLAN DOES NOT MATCH THE SAMPLE'S SHAPE:")
        for p in problems:
            print(" -", p)
        print("\nFix the plan (start from the structure file's blocks and replace"
              "\nonly the text), then re-run this before rendering.")
        raise SystemExit(1)
    print("\nPlan matches the sample's shape.")
