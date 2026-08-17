"""One entry point for the whole skill. Measure, build, verify, clean up.

    python run.py measure <sample.pdf|.docx> <name>
    python run.py build   <sample> <name> <plan.json> <output.docx> [options]

`measure` writes `<name>_style_contract.json` and `<name>_structure.json` and
prints the contract. `build` takes the plan you wrote against that structure
and carries it all the way to a verified pair of deliverables, or says exactly
which check stopped it.

## Why this exists

The skill was nine scripts and a numbered list, and following the list was
optional. Measured across three sessions given the same prompt against the
same sample:

* One ran every step and shipped a document matching the sample.
* One ran **none** of them — read the sample with a generic text tool, copied
  the shape of its own previous output instead, wrote a python-docx generator
  from scratch, checked the result by extracting its *text*, and reported
  "both files look great". The document was US Letter, not A4, at 1.15 line
  spacing instead of 1.5, with a 14pt title where the sample has 20pt and its
  section headings flush left where the sample centres them. Six defects, all
  of them caught by one `verify_docx.py` run costing 0.3 seconds, which was
  never made.
* One never reached a deliverable at all.

Nothing about that is a hard problem. Skipping the checks simply cost nothing,
and running them cost eight sequential tool calls. This inverts that: the
cheap path is now the correct one, and there is no shorter way to a verdict.

## What it guarantees

`build` exits 0 **only** when every applicable check passed. There is no
partial success and no "looks similar": the last line is either

    VERDICT: PASS — <output.docx> and <output.pdf> match the sample.

or `VERDICT: FAIL` followed by what to fix. A failed run leaves the working
files in place so the next attempt can start from them; a passing run deletes
them (Step Z), which two of three sessions otherwise got wrong in opposite
directions — one left six scaffolding files beside two deliverables, one
deleted nothing because its turn ended first.

## Options

    --pdf <path>   use this already-converted PDF instead of converting
    --keep         keep the scaffolding even on a pass
    --route a|b    override the route (default: from the sample's suffix)
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))

# Every sample this skill is used on is Cyrillic, and a sub-step's output
# quotes it. Piped — which is how the agent runs this — Python picks the
# console codepage instead of UTF-8, and cp1251 cannot encode a box-drawing
# character, let alone the mixed Ukrainian/ASCII a plan check prints. That
# raised UnicodeEncodeError *from the progress banner*, before the first
# check ran.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

#: Printed above each sub-step's own output so a 200-line transcript can still
#: be read as a sequence of decisions.
RULE = "-" * 68


def _script(name: str) -> str:
    return os.path.join(HERE, name)


class Steps:
    """The run's ledger: what was attempted, what it said, what it cost.

    Output is *buffered* rather than streamed, so the verdict can be printed
    first. That ordering is not cosmetic. `agent.py` releases old tool results
    from context once they exceed 2,000 characters, keeping only the head —
    and this script's output was 3,200-3,500. Measured on a live session,
    `big-pickle` ran `build` six times and every explanation of the failure
    was released before it could act on one; it then abandoned the gate and
    hand-built a document on the wrong paper size. A verdict at the *end* of a
    long output is the first thing lost.

    Passing steps contribute one tick each. Only failures print in full: on a
    pass this is ~400 characters instead of 3,400, which is also why fewer of
    them get released in the first place.
    """

    def __init__(self) -> None:
        self.entries: list[tuple[str, str, bool, str]] = []   # key,title,ok,out

    def run(self, key: str, title: str, args: list[str]) -> int:
        # One live line to stderr: a 15-second build with a silent terminal
        # reads as a hang to a person running this by hand, and stderr keeps
        # it out of the buffered stdout the ordering above depends on.
        print(f"  … {title}", file=sys.stderr, flush=True)
        proc = subprocess.run([sys.executable, *args], capture_output=True,
                              text=True, encoding="utf-8", errors="replace")
        out = (proc.stdout or "") + (proc.stderr or "")
        # PyMuPDF's `fitz` shim prints a deprecation warning on import, every
        # call. Six sub-steps means six copies above the output that matters.
        out = "\n".join(l for l in out.splitlines()
                        if "fitz` API is deprecated" not in l).strip()
        self.entries.append((key, title, proc.returncode == 0, out))
        return proc.returncode

    def note(self, key: str, title: str, ok: bool, out: str = "") -> None:
        """Record a step this script performed itself (conversion, skips)."""
        self.entries.append((key, title, ok, out.strip()))

    @property
    def failed(self) -> list[str]:
        return [k for k, _, ok, _ in self.entries if not ok]

    def ribbon(self) -> str:
        return "  ".join(f"{'v' if ok else 'X'} {k}" for k, _, ok, _ in self.entries)

    def detail(self) -> str:
        """Full output of the steps that failed, and nothing else."""
        blocks = []
        for key, title, ok, out in self.entries:
            if ok or not out:
                continue
            blocks.append(f"{RULE}\n{title}\n{RULE}\n{out}")
        return "\n\n".join(blocks)


# ── docx → pdf ──────────────────────────────────────────────────────────────
#
# The agent has a `convert_to_pdf` tool, but this script must be runnable on
# its own — a gate that needs a tool call in the middle of it is a gate with a
# hole in it. Word first because it is what `convert_to_pdf` uses, so the PDF
# this checks is the PDF the user will open.

def _convert_with_word(docx_path: str, pdf_path: str) -> str:
    import win32com.client            # from pywin32
    word = win32com.client.Dispatch("Word.Application")
    word.Visible = False
    try:
        doc = word.Documents.Open(os.path.abspath(docx_path))
        try:
            doc.SaveAs(os.path.abspath(pdf_path), FileFormat=17)  # wdFormatPDF
        finally:
            doc.Close(False)
    finally:
        word.Quit()
    return "Word"


def _convert_with_libreoffice(docx_path: str, pdf_path: str) -> str:
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        raise RuntimeError("LibreOffice not on PATH")
    # --outdir, not an output filename: soffice names the file after the input
    # and silently ignores anything else, so converting into a temp directory
    # and moving the result is the only way to control where it lands.
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run([soffice, "--headless", "--convert-to", "pdf",
                        "--outdir", tmp, os.path.abspath(docx_path)],
                       check=True, capture_output=True, timeout=300)
        stem = os.path.splitext(os.path.basename(docx_path))[0] + ".pdf"
        produced = os.path.join(tmp, stem)
        if not os.path.exists(produced):
            raise RuntimeError("soffice produced no PDF")
        shutil.move(produced, pdf_path)
    return "LibreOffice"


def convert_to_pdf(docx_path: str, pdf_path: str) -> tuple[bool, str]:
    """Returns (ok, how). Never raises — a failure here is reportable, not fatal."""
    attempts = []
    for convert in (_convert_with_word, _convert_with_libreoffice):
        try:
            return True, convert(docx_path, pdf_path)
        except Exception as e:                      # noqa: BLE001 — see docstring
            attempts.append(f"{convert.__name__}: {type(e).__name__}: {e}")
    return False, "; ".join(attempts)


# ── provenance ──────────────────────────────────────────────────────────────

#: Written into every document this skill renders, and looked for on the way
#: back in. Exact, unlike the author heuristic below — but only present on
#: documents produced after it was added, which is why both signals exist.
STAMP = "generated by document-style-match"

#: Marks a value in a generated `edits.json` that has not been written yet.
#: `edit_copy.validate_edits` refuses it, so an unfinished edit file cannot
#: reach a document. Shared with that module by value, not import — the
#: scripts are run standalone as often as they are imported.
TODO_PREFIX = "<<TODO:"

#: Weaker, and the only thing that can recognise an *older* output: python-docx
#: names itself as the author of everything it writes, and that survives the
#: conversion to PDF. A hand-written sample does not carry it — the methodichka
#: these rules were measured against reports `producer: ilovepdf.com` and an
#: empty author — but any document anyone built with python-docx does, so this
#: warns and never refuses.
_GENERATED_BY = "python-docx"


def _looks_machine_generated(path: str) -> tuple[bool, str]:
    """Is this sample a previous output rather than an original? (flag, why)

    Not a style question. One measured session took its own earlier output as
    the reference instead of the file the user pointed at — having been asked
    in as many words not to — and inherited every defect in it. Rule 2 of
    SKILL.md said not to and was ignored; this is the part that can see it
    happening.
    """
    try:
        if path.lower().endswith(".docx"):
            import docx
            props = docx.Document(path).core_properties
            if STAMP in (props.comments or ""):
                return True, "it carries this skill's own stamp"
            if props.author == _GENERATED_BY:
                return True, f"its author is {_GENERATED_BY}"
        elif path.lower().endswith(".pdf"):
            import pymupdf
            with pymupdf.open(path) as doc:
                meta = doc.metadata or {}
            if STAMP in (meta.get("subject") or meta.get("keywords") or ""):
                return True, "it carries this skill's own stamp"
            if meta.get("author") == _GENERATED_BY:
                return True, f"its author is {_GENERATED_BY}"
    except Exception:                                # noqa: BLE001
        # Provenance is advisory. A sample that cannot be opened is the next
        # step's problem to report properly, not this one's to guess at.
        return False, ""
    return False, ""


# ── steps ───────────────────────────────────────────────────────────────────

def route_for(sample: str) -> str:
    """`a` for a DOCX sample (copy & replace), `b` for a PDF one (rebuild)."""
    return "a" if sample.lower().endswith(".docx") else "b"


def _paths(name: str, work_dir: str) -> dict:
    """Where this run's working files live.

    Beside the deliverable, never in the current directory. `run.py` used to
    build these names relative to cwd and the agent runs it from the project
    root, so a failed build left six `course_ai_drones_*.json` files in the
    repository root — and Step Z could not clean them, because cleanup only
    happens on a pass.
    """
    return {
        "contract": os.path.join(work_dir, f"{name}_style_contract.json"),
        "structure": os.path.join(work_dir, f"{name}_structure.json"),
        "plan": os.path.join(work_dir, f"{name}_content_plan.json"),
        "edits": os.path.join(work_dir, f"{name}_edits.json"),
    }


def _write_edits_template(structure_path: str, edits_path: str) -> int:
    """Seed `<name>_edits.json` with the sample's own text, correctly shaped.

    Route A's edit file is `{index: new-text}`. Told that in prose, one
    measured session wrote `{index: {block object}}` four times running,
    because `measure` had just handed it a structure full of block objects.
    Handing back a file in the *right* shape ends the guessing: the model
    edits values instead of inventing a format, which is exactly why route B
    works — its plan is the structure with the words swapped.
    """
    with open(structure_path, encoding="utf-8") as f:
        blocks = json.load(f).get("blocks", [])
    template = {}
    for i, b in enumerate(blocks):
        if b.get("kind") == "spacer":
            continue          # nothing to replace; leaving it out keeps it
        if b.get("kind") == "table":
            template[str(i)] = {"rows": b.get("rows", [])}
            continue
        # The analyzer clips long text to a preview and records `full_len`.
        # Copying that preview in would be a trap: an entry left unedited
        # would *shorten* the paragraph to the preview instead of leaving it
        # alone. These are the body paragraphs that must be rewritten anyway,
        # so they get a sentinel `validate_edits` refuses — forgetting a
        # section then fails loudly instead of shipping a truncated one.
        if b.get("full_len"):
            template[str(i)] = (f"{TODO_PREFIX} {b['full_len']} chars here — "
                                f"the sample says: {b.get('text', '')}")
        else:
            template[str(i)] = b.get("text", "")
    with open(edits_path, "w", encoding="utf-8") as f:
        json.dump(template, f, ensure_ascii=False, indent=1)
    return len(template)


def measure(sample: str, name: str, work_dir: str = ".") -> int:
    generated, why = _looks_machine_generated(sample)
    if generated:
        print(f"WARNING: {os.path.basename(sample)} looks like a generated "
              f"document, not an original — {why}.\n"
              f"If it is a previous output, measuring it copies its defects "
              f"forward: use the file the user pointed at instead.\n")
    os.makedirs(work_dir, exist_ok=True)
    p = _paths(name, work_dir)
    route = route_for(sample)
    analyzer = "analyze_docx.py" if route == "a" else "analyze_pdf.py"

    steps = Steps()
    code = steps.run("measure", f"measure the sample ({analyzer})",
                     [_script(analyzer), sample, p["contract"], p["structure"]])
    if code:
        print(f"VERDICT: FAIL — could not measure {sample}.\n")
        print(steps.detail())
        return code
    print(steps.entries[-1][3])          # the contract itself: read this

    # The advice has to match the route `build` will actually take. It did not:
    # `measure` printed route B's instructions for every sample, so a DOCX
    # sample produced a content plan that `build` then fed to route A's
    # `edit_copy`, which wrote the plan's *key names* into the document.
    if route == "b":
        print(f"\n{RULE}\nNext: write {p['plan']} from {p['structure']}'s "
              f"blocks — replace each block's \"text\", keep everything else "
              f"including source_index — then:\n"
              f"  run.py build \"{sample}\" {name} \"{p['plan']}\" <output.docx>")
    else:
        n = _write_edits_template(p["structure"], p["edits"])
        print(f"\n{RULE}\nRoute A (the sample is a .docx): formatting is "
              f"copied, not rebuilt — you replace text only.\n"
              f"Wrote {p['edits']} with {n} entries, already in the right "
              f"shape: {{\"<index>\": \"<text>\"}}.\n"
              f"Edit the *values* — a string, a list of strings (one per run), "
              f"null to delete, or {{\"rows\": …}} for a table. Never a block "
              f"object. Then:\n"
              f"  run.py build \"{sample}\" {name} \"{p['edits']}\" <output.docx>")
    return 0


def build(sample: str, name: str, plan: str, output: str, *,
          pdf: str | None, keep: bool, route: str) -> int:
    work_dir = os.path.dirname(os.path.abspath(output)) or "."
    p = _paths(name, work_dir)
    contract, structure = p["contract"], p["structure"]
    scaffolding = [contract, structure, plan]

    for needed in (sample, plan):
        if not os.path.exists(needed):
            print(f"VERDICT: FAIL — {needed} does not exist.")
            return 2
    if not os.path.exists(contract):
        print(f"{contract} is missing — measuring the sample first.\n",
              file=sys.stderr)
        if measure(sample, name, work_dir):
            return 2

    steps = Steps()

    # ── shape and render ────────────────────────────────────────────────
    if route == "a":
        # Route A edits a copy of the sample, so there is no plan to shape-check
        # and no contract to render from: the formatting is carried, not rebuilt.
        steps.run("edit_copy", "apply the edits (edit_copy.py)",
                  [_script("edit_copy.py"), sample, plan, output])
    else:
        if steps.run("check_plan", "check the plan's shape (check_plan.py)",
                     [_script("check_plan.py"), structure, plan]):
            # Stop here. Rendering a plan that does not match the sample's
            # shape produces a document every later check will fail for a
            # reason this one already named, three steps further from it.
            return _verdict(output, None, steps, scaffolding, keep,
                            note="Nothing was rendered — fix the plan and "
                                 "re-run the same command.")
        if steps.run("render", "render (render_from_structure.py)",
                     [_script("render_from_structure.py"),
                      contract, plan, output]):
            return _verdict(output, None, steps, scaffolding, keep)

    if not os.path.exists(output):
        return _verdict(output, None, steps, scaffolding, keep,
                        note=f"{output} was not produced.")

    # ── DOCX side ───────────────────────────────────────────────────────
    steps.run("verify_docx", "verify the DOCX (verify_docx.py)",
              [_script("verify_docx.py"), contract, output, structure])

    # ── PDF side ────────────────────────────────────────────────────────
    pdf_path = pdf or os.path.splitext(output)[0] + ".pdf"
    if pdf:
        if not os.path.exists(pdf):
            print(f"VERDICT: FAIL — --pdf {pdf} does not exist.")
            return 2
    else:
        ok, how = convert_to_pdf(output, pdf_path)
        if not ok:
            # Not a failed check — an unavailable converter. Say which tool to
            # reach for rather than reporting the document as broken.
            steps.note("convert", "convert to PDF", False,
                       f"could not convert: {how}")
            return _verdict(output, None, steps, scaffolding, keep,
                            note=f"No PDF converter available. Run the "
                                 f"convert_to_pdf tool on {output}, then "
                                 f"re-run this with --pdf <that file>. "
                                 f"(Or: pip install pywin32)")
        steps.note("convert", "convert to PDF", True, f"converted with {how}")

    steps.run("verify_pdf", "verify the PDF layout (verify_pdf.py)",
              [_script("verify_pdf.py"), contract, pdf_path])

    if route == "b":
        steps.run("verify_render",
                  "compare the render with the sample (verify_render.py)",
                  [_script("verify_render.py"), sample, pdf_path])

    return _verdict(output, pdf_path, steps, scaffolding, keep)


def _verdict_path(output: str) -> str:
    """Where the last verdict for this deliverable is recorded."""
    return os.path.splitext(output)[0] + ".verdict"


def _previous_failures(output: str) -> list[str]:
    try:
        with open(_verdict_path(output), encoding="utf-8") as f:
            return json.load(f).get("failed", [])
    except (OSError, ValueError):
        return []


def _record_verdict(output: str, failed: list[str]) -> None:
    """Leave the verdict beside the deliverable.

    A `VERDICT: FAIL` printed to a tool result can be released from context,
    argued away, or simply not mentioned — measured, a session got three
    consecutive FAILs and reported "formatting fully reproduced" for a
    document whose every paragraph held JSON key names. A file on disk beside
    the .docx cannot be forgotten by the next turn and can be read by a
    person. Removed on a pass, so its presence means exactly one thing.
    """
    path = _verdict_path(output)
    if not failed:
        try:
            os.remove(path)
        except OSError:
            pass
        return
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"failed": failed, "output": os.path.basename(output)},
                      f, ensure_ascii=False, indent=1)
    except OSError:
        pass


def _verdict(output: str, pdf_path: str | None, steps: "Steps",
             scaffolding: list[str], keep: bool, note: str = "") -> int:
    failed = steps.failed
    previous = _previous_failures(output)
    _record_verdict(output, failed)

    # The verdict leads. Everything below it can be released from context by
    # the caller's history pruning; this line is what must survive, and the
    # pruner keeps the head. See `Steps`.
    if failed:
        print(f"VERDICT: FAIL — {', '.join(failed)}")
    else:
        print(f"VERDICT: PASS — {output}"
              + (f" and {pdf_path}" if pdf_path else "")
              + " match the sample.")
    print(steps.ribbon())
    if note:
        print(note)

    if not failed:
        if keep:
            print(f"Working files kept (--keep): {', '.join(scaffolding)}")
        else:
            removed = [os.path.basename(f) for f in scaffolding
                       if _try_remove(f)]
            if removed:
                print(f"Cleaned up: {', '.join(removed)}")
            print("Any scratch scripts you wrote yourself are still yours "
                  "to delete.")
        return 0

    # Repeating the same failure means the previous message did not land.
    # Measured: six identical `stretched lines` failures in a row, after which
    # the model abandoned the gate entirely rather than change the text.
    repeated = [k for k in failed if k in previous]
    if repeated:
        print(f"\nSTILL FAILING after your last attempt: {', '.join(repeated)}. "
              f"Whatever you changed did not address it. Re-read the numbers "
              f"below and change the *input they name* — a different renderer, "
              f"a different tool or a hand-written generator will not move "
              f"them, and abandoning this script means shipping a document "
              f"nothing has checked.")
    print(f"Working files kept: {', '.join(scaffolding)}")
    print(f"Verdict recorded at {_verdict_path(output)} — it is removed only "
          f"by a pass. Do not report success while it exists.\n")
    print(steps.detail())
    return 1


def _try_remove(path: str) -> bool:
    try:
        os.remove(path)
        return True
    except OSError:
        return False


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="run.py", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    m = sub.add_parser("measure", help="measure the sample into a contract + structure")
    m.add_argument("sample")
    m.add_argument("name")
    m.add_argument("--work-dir", default=".",
                   help="where the working files go. Use the folder the "
                        "deliverable will live in; `build` derives it from "
                        "the output path and expects them there.")

    b = sub.add_parser("build", help="plan -> document -> PDF -> every check")
    b.add_argument("sample")
    b.add_argument("name")
    b.add_argument("plan")
    b.add_argument("output")
    b.add_argument("--pdf", default=None)
    b.add_argument("--keep", action="store_true")
    b.add_argument("--route", choices=("a", "b"), default=None)

    args = parser.parse_args(argv)
    if args.command == "measure":
        return measure(args.sample, args.name, args.work_dir)
    return build(args.sample, args.name, args.plan, args.output,
                 pdf=args.pdf, keep=args.keep,
                 route=args.route or route_for(args.sample))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
