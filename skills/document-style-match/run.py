"""One entry point for the whole skill. Measure, build, verify, clean up.

    python run.py measure <sample.pdf|.docx> <name>
    python run.py build   <sample> <name> <texts.json> <output.docx> [options]

`measure` measures the sample into `.dsm/<name>/` and writes a `texts.json`
you fill in — `{"<block index>": "<new text>"}`, the same shape for a PDF
sample and a DOCX one. `build` takes that file and carries it all the way to
a verified pair of deliverables, or says exactly which check stopped it and
which blocks to change.

`run.py plan <name>` expands the texts file into a content plan on its own,
if you want to see or hand-tune it before building. `build` does it for you.

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

or `VERDICT: FAIL` followed by what to fix, naming the blocks to change.

Working files live in `.dsm/<name>/` beside the deliverable and are kept
across runs, keyed by the sample's size and mtime — nothing is littered next
to the output, and a second pass over the same sample costs no measurement.

## Options

    --pdf <path>   use this already-converted PDF instead of converting
    --keep         accepted and ignored; the cache is always kept
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

    def notes(self) -> list[str]:
        """`NOTE:` lines from every step, passing or not.

        The one channel a step has for something the reader must carry into
        the final answer but which is not a failure: a formula removed on
        request, a check that had nothing to compare against.
        """
        return [line for _, _, _, out in self.entries
                for line in out.splitlines() if line.startswith("NOTE:")]

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


def _convert_with_docx2pdf(docx_path: str, pdf_path: str) -> str:
    """Last resort, and the reason the MCP `word-docs` server exists here.

    Word and LibreOffice are both absent often enough to matter, and the old
    failure message ("pip install pywin32") is advice a session cannot act on
    inside its own turn. `docx2pdf` is what the `word-docs` MCP server's own
    `convert_to_pdf` uses, so reaching for the library directly gets the same
    result without spending a turn and ~7.5k tokens of tool schema on the
    server that wraps it.
    """
    from docx2pdf import convert
    convert(os.path.abspath(docx_path), os.path.abspath(pdf_path))
    if not os.path.exists(pdf_path):
        raise RuntimeError("docx2pdf produced no PDF")
    return "docx2pdf"


def convert_to_pdf(docx_path: str, pdf_path: str) -> tuple[bool, str]:
    """Returns (ok, how). Never raises — a failure here is reportable, not fatal."""
    attempts = []
    for convert in (_convert_with_word, _convert_with_libreoffice,
                    _convert_with_docx2pdf):
        try:
            return True, convert(docx_path, pdf_path)
        except Exception as e:                      # noqa: BLE001 — see docstring
            attempts.append(f"{convert.__name__}: {type(e).__name__}: {e}")
    return False, "; ".join(attempts)


def _text_layer_missing(sample: str) -> bool:
    """Is this PDF a scan? Then nothing downstream can measure it.

    `analyze_pdf` measures a scanned sample without objecting and reports
    plausible numbers taken from nothing — page size is real, every font and
    spacing figure is a default. One line here is worth more than any check
    further down, because further down there is nothing left to check.
    """
    if not sample.lower().endswith(".pdf"):
        return False
    try:
        import pymupdf
        with pymupdf.open(sample) as doc:
            pages = min(3, doc.page_count)
            return not any(doc[i].get_text().strip() for i in range(pages))
    except Exception:                                # noqa: BLE001
        return False


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

    They now live in a `.dsm/<name>/` cache rather than beside the
    deliverable, because "don't litter the user's folder" and "throw the
    measurement away" are different requirements and cleanup used to do
    both. Measured: a session reached PASS, cleanup deleted the structure,
    the user asked for one sentence to change, and the whole sample had to
    be measured again to change it.
    """
    cache = os.path.join(work_dir, ".dsm", name)
    return {
        "cache": cache,
        "contract": os.path.join(cache, "style_contract.json"),
        "structure": os.path.join(cache, "structure.json"),
        "plan": os.path.join(cache, "content_plan.json"),
        "edits": os.path.join(cache, "edits.json"),
        "texts": os.path.join(cache, "texts.json"),
        "fingerprint": os.path.join(cache, "sample.json"),
    }


def _fingerprint(sample: str) -> dict:
    try:
        st = os.stat(sample)
        return {"path": os.path.abspath(sample), "size": st.st_size,
                "mtime": round(st.st_mtime, 3)}
    except OSError:
        return {}


def _cache_is_fresh(sample: str, p: dict) -> bool:
    """Has this exact sample already been measured into this cache?"""
    if not (os.path.exists(p["contract"]) and os.path.exists(p["structure"])):
        return False
    try:
        with open(p["fingerprint"], encoding="utf-8") as f:
            return json.load(f) == _fingerprint(sample)
    except (OSError, ValueError):
        return False


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
                                f"{_embedded_note(b)}"
                                f"the sample says: {b.get('text', '')}")
        else:
            template[str(i)] = b.get("text", "")
    with open(edits_path, "w", encoding="utf-8") as f:
        json.dump(template, f, ensure_ascii=False, indent=1)
    return len(template)


def _embedded_note(block: dict) -> str:
    """What this paragraph carries that replacing its text will not touch.

    Route A copies formulas, images and hyperlinks and cannot edit them, and
    nothing said so until `_lost_embedded` ran — after the copy. Measured: a
    session facing 28 equations from a numerical-integration paper it was
    re-theming to AI built a doctored copy of the sample, stripped the maths
    with python-docx, reset `core_properties.author` to defeat the
    provenance warning, and fed *that* to `build` as the reference. The skill
    made rule 2 the only workable path. Saying it here, with the verb that
    handles it, is what removes the incentive.
    """
    embedded = block.get("embedded") or {}
    parts = [f"{n} {kind}" for kind, n in sorted(embedded.items()) if n]
    if not parts:
        return ""
    note = f"CARRIES {', '.join(parts)}, kept as-is. "
    if embedded.get("links"):
        # A hyperlink is not droppable and does not want to be: the e-mail in
        # an author line outlives a rewrite of that line. It is named because
        # its text is *not* in the runs, so a replacement string has to stop
        # where the link begins — "…, e-mail: " and no further.
        note += ("The link text lives outside the runs: end your replacement "
                 "where it begins. ")
    if embedded.get("math") or embedded.get("images"):
        note += ("To remove them write {\"text\": \"…\", \"drop_math\": true} "
                 "(or \"__doc__\": {\"drop_math\": true} for all of them). ")
    return note


# ── texts -> plan ───────────────────────────────────────────────────────────

def _is_texts_payload(payload) -> bool:
    """`{index: text}` (route A's shape) rather than a rendered block list."""
    if not isinstance(payload, dict) or "blocks" in payload:
        return False
    keys = [k for k in payload if not k.startswith("__")]
    return bool(keys) and all(str(k).lstrip("-").isdigit() for k in keys)


def expand_texts(structure_path: str, texts_path: str, out_path: str) -> dict:
    """Turn `{block_index: new text}` into a content plan.

    This is the whole of what two measured sessions wrote by hand: load the
    structure, swap each block's `text`, carry `source_index`, `align`,
    `bold`, `indent_cm` and `target_chars` through untouched, write the
    result. One of them authored 8,562 characters of generator to do it,
    then rewrote it at 9,012 to change one sentence — scaffolding for a
    transformation with no decisions in it.

    Carrying the fields is not bookkeeping: `align` and `bold` *are* the
    formatting a reader checks first, and a plan that drops them renders a
    document that passes the contract and looks nothing like the sample.

    New blocks go in `__insert__`: `[{"after": 49, "blocks": [...]}]`, where
    each entry is a block object or a plain string that inherits the anchor
    block's formatting.
    """
    with open(structure_path, encoding="utf-8") as f:
        blocks = json.load(f).get("blocks", [])
    with open(texts_path, encoding="utf-8") as f:
        payload = json.load(f)

    inserts: dict[int, list] = {}
    for spec in (payload.get("__insert__") or []):
        inserts.setdefault(int(spec["after"]), []).extend(spec.get("blocks", []))
    texts, unknown = {}, []
    for key, value in payload.items():
        if str(key).startswith("__"):
            continue
        index = int(key)
        if not 0 <= index < len(blocks):
            unknown.append(index)
            continue
        texts[index] = value
    if unknown:
        raise ValueError(
            f"{texts_path}: block index {unknown[:6]} is outside the "
            f"structure's 0..{len(blocks) - 1}. Indices are positions in "
            f"`blocks`, not line numbers in the file.")

    out, replaced, dropped, inserted = [], 0, 0, 0
    for i, block in enumerate(blocks):
        keep = dict(block)
        if i in texts:
            value = texts[i]
            if value is None:
                dropped += 1
                keep = None
            else:
                if isinstance(value, list):
                    value = "".join(value)
                if isinstance(value, dict):
                    value = value.get("text", "")
                if not isinstance(value, str):
                    raise ValueError(
                        f"block {i}: expected a string, a list of strings or "
                        f"null — got {type(value).__name__}")
                if value.lstrip().startswith(TODO_PREFIX):
                    raise ValueError(
                        f"block {i}: still holds the `{TODO_PREFIX}…` "
                        f"placeholder `measure` wrote. Replace it with the "
                        f"text this section should contain.")
                keep["text"] = value
                replaced += 1
        if keep is not None:
            out.append(keep)
        for extra in inserts.get(i, []):
            if isinstance(extra, str):
                anchor = {k: v for k, v in block.items()
                          if k in ("align", "bold", "size_pt", "indent_cm",
                                   "style_name")}
                anchor["text"] = extra
                out.append(anchor)
            else:
                out.append(dict(extra))
            inserted += 1

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"blocks": out}, f, ensure_ascii=False, indent=1)

    written = expected = 0
    for b in out:
        target = b.get("target_chars")
        if target is None or b.get("kind"):
            continue
        written += len(b.get("text", ""))
        expected += int(target)
    return {"blocks": len(out), "replaced": replaced, "dropped": dropped,
            "inserted": inserted, "written": written, "expected": expected,
            "untouched": sum(1 for i, b in enumerate(blocks)
                             if i not in texts and b.get("full_len"))}


def measure(sample: str, name: str, work_dir: str = ".") -> int:
    generated, why = _looks_machine_generated(sample)
    if generated:
        print(f"WARNING: {os.path.basename(sample)} looks like a generated "
              f"document, not an original — {why}.\n"
              f"If it is a previous output, measuring it copies its defects "
              f"forward: use the file the user pointed at instead.\n")
    if _text_layer_missing(sample):
        print(f"VERDICT: FAIL — {os.path.basename(sample)} has no text layer "
              f"on its first pages: it is a scan, and every figure measured "
              f"from it would be a default dressed as a measurement.\n"
              f"Ask the user for the .docx, or OCR it first "
              f"(mcp pdf server: textextraction__ocr_pdf).")
        return 2
    p = _paths(name, work_dir)
    os.makedirs(p["cache"], exist_ok=True)
    route = route_for(sample)
    analyzer = "analyze_docx.py" if route == "a" else "analyze_pdf.py"

    if _cache_is_fresh(sample, p):
        print(f"{os.path.basename(sample)} is already measured in "
              f"{p['cache']} — reusing it (delete that folder to re-measure).")
    else:
        steps = Steps()
        code = steps.run("measure", f"measure the sample ({analyzer})",
                         [_script(analyzer), sample, p["contract"], p["structure"]])
        if code:
            print(f"VERDICT: FAIL — could not measure {sample}.\n")
            print(steps.detail())
            return code
        with open(p["fingerprint"], "w", encoding="utf-8") as f:
            json.dump(_fingerprint(sample), f)
        # The sub-step's own stdout carried the whole contract — every
        # `style_signature`, twelve of them on a real sample, ~2k characters
        # nothing acts on. `verify_docx` reads them off disk. What a reader
        # needs is the one line they are asked to quote back.
        for line in steps.entries[-1][3].splitlines():
            if line.startswith(("NOTE:", "WARNING:")) or "page_window" in line:
                print(line)

    print(_contract_line(p["contract"], p["structure"]))

    # Both routes now take the same input shape: `{block index: new text}`.
    # Route B used to be told to write the plan itself, and it did — one
    # session's generator ran to 8.5k characters for a transformation with no
    # decisions in it, and the asymmetry (route A got a filled template,
    # route B got a paragraph of prose) is what produced it.
    target = p["edits"] if route == "a" else p["texts"]
    n = _write_edits_template(p["structure"], target)
    how = ("Route A (.docx sample): formatting is copied, not rebuilt."
           if route == "a" else
           "Route B (.pdf sample): formatting is rebuilt from the contract.")
    print(f"\n{RULE}\n{how}\n"
          f"Wrote {target} — {n} entries, already the right shape:\n"
          f"  {{\"<block index>\": \"<new text>\"}}\n"
          f"Edit the *values only*: a string, a list of strings (one per run), "
          f"null to delete, or {{\"rows\": …}} for a table. Never a block "
          f"object. New blocks go in \"__insert__\": "
          f"[{{\"after\": <index>, \"blocks\": [\"…\"]}}].\n"
          f"Then, with no other step in between:\n"
          f"  run.py build \"{sample}\" {name} \"{target}\" <output.docx>")
    return 0


def _contract_line(contract_path: str, structure_path: str) -> str:
    """The contract as the one line SKILL.md asks to be quoted back."""
    try:
        with open(contract_path, encoding="utf-8") as f:
            c = json.load(f)
        with open(structure_path, encoding="utf-8") as f:
            s = json.load(f)
    except (OSError, ValueError):
        return ""
    m = c.get("margins_cm", {})
    margins = "/".join(
        f"{m.get(k, 0):g}" for k in
        ("left_margin", "right_margin", "top_margin", "bottom_margin"))
    blocks = s.get("blocks", [])
    shape = (f"{len(blocks)} blocks "
             f"({sum(1 for b in blocks if b.get('kind') == 'spacer')} spacers, "
             f"{sum(1 for b in blocks if b.get('kind') == 'table')} tables, "
             f"{sum(1 for b in blocks if b.get('page_break_before'))} breaks)")
    note = ""
    if s.get("truncated"):
        note = ("\nNOTE: only part of the sample was opened — say which of its "
                "sections your document does not contain.")
    return (f"\n{c.get('page_width_cm')}×{c.get('page_height_cm')}cm · "
            f"{c.get('body_font')} · body {c.get('body_size_pt')}pt · "
            f"title {c.get('title_size_pt')}pt · margins {margins}cm · "
            f"spacing {c.get('line_spacing')} · "
            f"indent {c.get('body_indent_cm')}cm\n{shape}{note}")


def build(sample: str, name: str, plan: str, output: str, *,
          pdf: str | None, keep: bool, route: str) -> int:
    work_dir = os.path.dirname(os.path.abspath(output)) or "."
    p = _paths(name, work_dir)
    contract, structure = p["contract"], p["structure"]

    for needed in (sample, plan):
        if not os.path.exists(needed):
            print(f"VERDICT: FAIL — {needed} does not exist.")
            return 2
    if not os.path.exists(contract):
        print(f"{contract} is missing — measuring the sample first.\n",
              file=sys.stderr)
        if measure(sample, name, work_dir):
            return 2

    # A `{index: text}` file is the input shape for both routes. Route A
    # feeds it to `edit_copy` as-is; route B expands it into a plan here,
    # which is the step two sessions wrote a generator for.
    if route == "b":
        try:
            with open(plan, encoding="utf-8") as f:
                payload = json.load(f)
        except (OSError, ValueError) as e:
            print(f"VERDICT: FAIL — {plan} is not readable JSON: {e}")
            return 2
        if _is_texts_payload(payload):
            try:
                stats = expand_texts(structure, plan, p["plan"])
            except (ValueError, KeyError) as e:
                print(f"VERDICT: FAIL — {plan} could not be expanded.\n{e}")
                return 2
            ratio = (stats["written"] / stats["expected"]
                     if stats["expected"] else 1.0)
            print(f"plan: {stats['blocks']} blocks "
                  f"({stats['replaced']} replaced, {stats['dropped']} dropped, "
                  f"{stats['inserted']} inserted) · volume "
                  f"{stats['written']}/{stats['expected']} = {ratio:.0%}"
                  + (f" · {stats['untouched']} long blocks still hold the "
                     f"sample's own text" if stats["untouched"] else ""))
            plan = p["plan"]

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
            return _verdict(output, None, steps, p['cache'], keep,
                            note="Nothing was rendered — fix the plan and "
                                 "re-run the same command.")
        if steps.run("render", "render (render_from_structure.py)",
                     [_script("render_from_structure.py"),
                      contract, plan, output]):
            return _verdict(output, None, steps, p['cache'], keep)

    if not os.path.exists(output):
        return _verdict(output, None, steps, p['cache'], keep,
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
            return _verdict(output, None, steps, p['cache'], keep,
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
                  [_script("verify_render.py"), sample, pdf_path,
                   "--plan", plan])

    return _verdict(output, pdf_path, steps, p['cache'], keep)


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
             cache: str, keep: bool, note: str = "") -> int:
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
    # A passing step can still have something to say — a formula removed on
    # request, a check that could not run. `detail()` shows failures only, so
    # without this those lines exist and are never read.
    for line in steps.notes():
        print(line)
    if note:
        print(note)

    if not failed:
        # The measurement is kept, not deleted. "Don't litter the user's
        # folder" and "throw away what it cost 20 seconds to derive" were one
        # behaviour, and the second one has a price: a session reached PASS,
        # cleanup removed the structure, the user asked for one sentence to
        # change, and the sample had to be measured again to change it. The
        # cache is hidden and self-invalidating; nothing is beside the
        # deliverable either way.
        print(f"Measurement kept in {cache} (re-used automatically; delete "
              f"the folder to force a re-measure).")
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
    print(f"Working files kept in {cache}")
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

    pl = sub.add_parser("plan", help="expand {index: text} into a content plan")
    pl.add_argument("name")
    pl.add_argument("texts", nargs="?", default=None,
                    help="defaults to the cache's texts.json")
    pl.add_argument("--work-dir", default=".")

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
    if args.command == "plan":
        p = _paths(args.name, args.work_dir)
        try:
            stats = expand_texts(p["structure"], args.texts or p["texts"],
                                 p["plan"])
        except (OSError, ValueError, KeyError) as e:
            print(f"FAIL — {e}")
            return 2
        ratio = stats["written"] / stats["expected"] if stats["expected"] else 1.0
        print(f"Wrote {p['plan']}: {stats['blocks']} blocks "
              f"({stats['replaced']} replaced, {stats['dropped']} dropped, "
              f"{stats['inserted']} inserted) · volume "
              f"{stats['written']}/{stats['expected']} = {ratio:.0%}")
        if not 0.65 <= ratio <= 1.35:
            print("Volume is outside check_plan's ±35% — adjust the text "
                  "before building.")
        return 0
    return build(args.sample, args.name, args.plan, args.output,
                 pdf=args.pdf, keep=args.keep,
                 route=args.route or route_for(args.sample))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
