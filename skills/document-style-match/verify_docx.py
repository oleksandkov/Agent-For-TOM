"""Diff a generated DOCX against its style contract. Not a self-check —
"some paragraphs are centered" would still pass a page-size-wrong document.

Usage: python verify_docx.py <contract.json> <output.docx>

Exits 0 and prints "Matches contract." when everything is within tolerance;
exits 1 and prints CONTRACT MISMATCH with the offending fields otherwise.
A mismatch means the generation script did not actually read the contract
for that field — fix that script and regenerate, do not report success.

Re-measures the output with the *same* `analyze()` used on the sample
(imported, not reimplemented) and diffs every field it returns — including
spacing/indent, not just page size and body font size — so a script that
read the contract for margins but hardcoded `line_spacing=1.15` regardless
of what the sample actually used gets caught here too.
"""
import os
import sys
import json

from analyze_docx import analyze

CM_TOLERANCE = 0.05
PT_TOLERANCE = 0.5


def _flatten(contract: dict) -> dict:
    flat = dict(contract)
    flat.update(flat.pop("margins_cm", {}))
    return flat


def verify_rhythm(structure_path: str, docx_path: str) -> list[str]:
    """Check the vertical rhythm the style fields cannot express.

    A document can match every number in the contract and still render as
    one unbroken wall of text: measured on two real outputs that both
    reported "Matches contract." while carrying zero spacer paragraphs and
    zero page breaks against a sample with 801 and 25. Spacing lives in
    the block list, so it needs the structure file, not the style half.
    """
    import docx
    from docx.oxml.ns import qn

    want = json.load(open(structure_path, encoding="utf-8"))
    blocks = want.get("blocks", [])
    want_spacers = sum(1 for b in blocks if b.get("kind") == "spacer")
    want_breaks = sum(1 for b in blocks if b.get("page_break_before"))

    d = docx.Document(docx_path)
    got_spacers = sum(1 for p in d.paragraphs if not p.text.strip())
    got_breaks = 0
    for p in d.paragraphs:
        pPr = p._p.find(qn("w:pPr"))
        if pPr is not None and pPr.find(qn("w:pageBreakBefore")) is not None:
            got_breaks += 1
        got_breaks += len(p._p.findall(".//" + qn("w:br") + '[@' + qn("w:type") + '="page"]'))

    problems = []
    # Proportional, not exact: the generated document legitimately has a
    # different number of paragraphs than the sample. Losing spacing
    # altogether is the failure worth catching, not being a few off.
    if want_spacers and got_spacers < max(1, want_spacers * 0.3):
        problems.append(f"spacer paragraphs: got {got_spacers}, sample has {want_spacers} "
                        f"— vertical gaps between sections are missing")
    if want_breaks and got_breaks == 0:
        problems.append(f"page breaks: got 0, sample has {want_breaks} "
                        f"— sections that should start on a new page do not")
    return problems


#: Measured off the output but not comparable to anything — reporting them as
#: unchecked would be noise, not information.
_NOT_A_STYLE_FIELD = frozenset({"blocks", "style_signatures"})


def verify(contract_path: str, docx_path: str) -> tuple[list[str], list[str]]:
    """Returns (problems, unchecked).

    `unchecked` is the second half of the answer, and it used to be silent.
    A field the contract does not carry is skipped here — correctly, there is
    nothing to compare against — but the caller then printed an unqualified
    "Matches contract." Measured live: a PDF-derived contract carried neither
    `line_spacing` nor `body_indent_cm`, the generated document had single
    spacing and no paragraph indent against a sample set to 1.5 and 1.25cm,
    and this said it matched. Both analyzers now measure both fields, so the
    gap should not recur — but a pass that names what it could not check is
    the thing that would have caught it, and it stays either way.
    """
    contract = _flatten(json.load(open(contract_path, encoding="utf-8")))
    measured = _flatten(analyze(docx_path))

    problems, unchecked = [], []
    for key, actual in measured.items():
        expected = contract.get(key)
        if key in _NOT_A_STYLE_FIELD or key.startswith("_"):
            # blocks carries the sample's own text for reference — the
            # generated document is expected to have different content, so
            # diffing it here would always "fail" on the one field that is
            # supposed to change.
            continue
        if expected is None:
            # Present-with-null and absent are different answers. A DOCX
            # sample that sets no heading spacing anywhere is *measured* as
            # having none, and the contract records that as null — there is
            # nothing to compare and nothing missing either. A field the
            # analyzer never looked at is simply not there, and that is the
            # one worth naming.
            if key not in contract and actual is not None:
                unchecked.append(key)
            continue
        if isinstance(expected, str):
            if str(actual) != expected:
                problems.append(f"{key}: got {actual!r}, contract {expected!r}")
            continue
        if actual is None:
            problems.append(f"{key}: not set in the output, contract {expected}")
            continue
        tolerance = PT_TOLERANCE if key.endswith("_pt") else CM_TOLERANCE
        if abs(actual - expected) > tolerance:
            problems.append(f"{key}: got {actual}, contract {expected}")
    return problems, unchecked


if __name__ == "__main__":
    if len(sys.argv) not in (3, 4):
        print("Usage: python verify_docx.py <contract.json> <output.docx> [structure.json]")
        raise SystemExit(2)
    problems, unchecked = verify(sys.argv[1], sys.argv[2])
    # The structure file is optional so an existing 2-argument call still
    # works, but without it the rhythm check cannot run — say so rather
    # than printing an unqualified pass.
    rhythm_checked = False
    structure_path = sys.argv[3] if len(sys.argv) == 4 else None
    if structure_path is None:
        guess = sys.argv[1]
        for suffix in ("_style_contract.json", ".json"):
            if guess.endswith(suffix):
                candidate = guess[: -len(suffix)] + "_structure.json"
                if os.path.exists(candidate):
                    structure_path = candidate
                break
    if structure_path and os.path.exists(structure_path):
        problems += verify_rhythm(structure_path, sys.argv[2])
        rhythm_checked = True

    if unchecked:
        print("NOT checked (the contract carries no value for these): "
              + ", ".join(sorted(unchecked)))
        print("  Re-measure the sample with the current analyzer, or compare "
              "these by eye on the rendered page — a pass below says nothing "
              "about them.\n")

    if problems:
        print("CONTRACT MISMATCH:")
        for p in problems:
            print(" -", p)
        raise SystemExit(1)
    verdict = ("Matches contract." if rhythm_checked
               else "Matches contract. (no structure file — spacing/page breaks NOT checked)")
    if unchecked:
        verdict += f" ({len(unchecked)} field(s) NOT checked — see above)"
    print(verdict)
