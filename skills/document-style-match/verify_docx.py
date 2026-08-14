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
import sys
import json

from analyze_docx import analyze

CM_TOLERANCE = 0.05
PT_TOLERANCE = 0.5


def _flatten(contract: dict) -> dict:
    flat = dict(contract)
    flat.update(flat.pop("margins_cm", {}))
    return flat


def verify(contract_path: str, docx_path: str) -> list[str]:
    contract = _flatten(json.load(open(contract_path, encoding="utf-8")))
    measured = _flatten(analyze(docx_path))

    problems = []
    for key, actual in measured.items():
        expected = contract.get(key)
        if expected is None:
            continue
        if isinstance(expected, str):
            if str(actual) != expected:
                problems.append(f"{key}: got {actual!r}, contract {expected!r}")
            continue
        tolerance = PT_TOLERANCE if key.endswith("_pt") else CM_TOLERANCE
        if abs(actual - expected) > tolerance:
            problems.append(f"{key}: got {actual}, contract {expected}")
    return problems


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python verify_docx.py <contract.json> <output.docx>")
        raise SystemExit(2)
    problems = verify(sys.argv[1], sys.argv[2])
    if problems:
        print("CONTRACT MISMATCH:")
        for p in problems:
            print(" -", p)
        raise SystemExit(1)
    print("Matches contract.")
