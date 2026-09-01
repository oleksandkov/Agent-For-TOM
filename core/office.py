"""Act in the Office document the user already has open — their window, live.

The counterpart to `core/browser.py`. `python-docx` and the `word-docs` MCP
server write the `.docx` **file**; with the document open in Word that is not
a lesser version of this, it is a different and wrong thing — the writes go to
a file Word is not reading, the user sees nothing, and their next Ctrl+S
overwrites everything with Word's in-memory copy. This module attaches to the
running `winword.exe` through the COM Running Object Table, so `doc_*` edits
the document in front of them and they watch it change.

Four things Phase 0 measured, each of which shapes the code
(`OFFICE_LIVE_PLAN.md` §3):

**COM is apartment-threaded, and this is not optional.** A worker thread
without `pythoncom.CoInitialize()` gets `com_error -2147221008, "CoInitialize
has not been called"` — measured, not assumed. So `_ComThread` owns one thread
for the session, initialises COM on it, and every call is submitted there.
It is deliberately *not* `core/browser.py`'s asyncio loop: an asyncio loop and
a COM STA do not share cleanly, and a Word call blocked on a modal dialog
would otherwise freeze browser calls too.

**One Undo per COM edit.** Two `InsertAfter` calls needed exactly two `Undo()`
calls to restore the original text. That is the whole argument for `doc_edit`
sitting at `medium` risk where `tab_act` sits at `high`: a click in a
signed-in browser cannot be taken back, and this can, by the user, with the
keystroke they already know. `UndoClear()` is therefore never called here —
it would discard the only safety net the risk tier is justified by — and
`doc_save` is a separate tool, because saving is the one thing Undo cannot
reverse.

**`Paragraph.Range` includes the paragraph mark.** `Paragraphs(1).Range
.InsertAfter(x)` puts `x` at the *start of paragraph 2*, which Phase 0 caught
by inserting into two paragraphs and finding both edits in the wrong place.
`_text_range` trims the trailing mark, and every edit goes through it. This is
the sort of thing that produces a plausible-looking document with every
insertion off by one paragraph.

**`RPC_E_CALL_REJECTED` is real but not constant.** 300 consecutive reads on
an idle Word raised none. It fires when Word is busy — the user typing, a menu
open, a modal dialog — which is exactly when this tool is most likely to be
used, so `_retrying` wraps every call regardless. `DisplayAlerts` is switched
off on attach so Word does not raise modals of its own.

**Refs are guarded, not trusted.** `doc.Paragraphs` is re-indexed by any
insertion or deletion — including one the *user* makes while the agent works,
which is the normal case for a document you are both looking at.
`outline_fingerprint` records the shape at outline time and an edit refuses on
a mismatch. Preferring `find_replace` over index-addressed edits is the real
answer; the fingerprint is what makes the fallback safe rather than merely
likely. And per `BROWSER_SNAPSHOT_FIX_PLAN.md` §9, the refs and their
descriptions come from **one** pass over the collection, never two that ought
to agree.
"""

from __future__ import annotations

import atexit
import hashlib
import queue
import threading
import time
from typing import Any, Callable, Optional

#: The applications this module can attach to. Word is the only one with tools
#: today; the ProgID is a parameter from the start so Excel and PowerPoint cost
#: a tool set rather than a rewrite of the threading model.
PROGIDS = {
    "word": "Word.Application",
    "excel": "Excel.Application",
    "powerpoint": "PowerPoint.Application",
}

#: `wdNoProtection`. Anything else means the document is restricted and this
#: module reports the restriction rather than trying to remove it.
WD_NO_PROTECTION = -1
WD_ALERTS_NONE = 0
WD_REPLACE_ALL = 2
WD_FIND_STOP = 0

#: Paragraphs listed by one outline. Past this it stops being something a
#: model reads.
MAX_OUTLINE_PARAGRAPHS = 120

#: Characters of one paragraph shown in the outline, and returned by a read.
OUTLINE_SNIPPET = 90
MAX_READ_CHARS = 20_000

#: How long a COM call may take before the wait is abandoned. Word blocks for
#: real — a modal dialog stops COM until a human dismisses it — so this exists
#: to stop the *turn* hanging, not to stop Word.
CALL_TIMEOUT_S = 30.0

#: `RPC_E_CALL_REJECTED` / `RPC_E_SERVERCALL_RETRYLATER`. Word is busy; the
#: call never reached it, so retrying is safe — it is not a partial edit.
_BUSY_HRESULTS = (-2147418111, -2146777998, -2147417846)
_BUSY_RETRIES = 6
_BUSY_BACKOFF_S = 0.25

EDIT_ACTIONS = ("replace", "insert_after", "insert_before", "delete",
                "style", "find_replace", "insert_equation", "equation")

#: Word's Find is capped at 255 characters per argument. Over it, the call
#: raises "String parameter too long" -- seen three times in one live session,
#: each time on a paragraph the model was legitimately trying to rewrite. Past
#: this length `_op_edit` rewrites the paragraph directly instead of refusing.
MAX_FIND_CHARS = 255

#: Built-in styles by `wdBuiltinStyle` constant rather than by name.
#:
#: Style names are localised. On a Ukrainian Word, -1, -2 and -63 are
#: "Звичайний", "Заголовок 1" and "Назва", so `paragraph.Style = "Heading 1"`
#: raises "Елемент з указаним ім'ям не існує" — measured. The constants are
#: identical in every locale, so a model may keep writing "Heading 1" and it
#: resolves on a Word in any language. Anything not listed is passed through
#: as a literal name, which is what a custom style needs.
_BUILTIN_STYLES = {
    "normal": -1, "title": -63,
    **{f"heading {n}": -(n + 1) for n in range(1, 10)},
    **{f"h{n}": -(n + 1) for n in range(1, 10)},
}


class OfficeInterrupted(Exception):
    """Esc was pressed while a call was still on the COM thread."""


class OfficeBusy(Exception):
    """Word rejected the call for longer than `_BUSY_RETRIES` allowed."""


# ---------------------------------------------------------------------------
# The COM thread
# ---------------------------------------------------------------------------

class _ComThread:
    """One thread with COM initialised, for the life of the process.

    Deliberately a plain worker-and-queue rather than an event loop. There is
    nothing asynchronous about COM automation — every call blocks — so the
    machinery `core/browser.py` needs for awaitables would be dead weight here,
    and mixing an STA into an asyncio loop is a known way to produce hangs
    nobody can reproduce.
    """

    def __init__(self) -> None:
        self._jobs: "queue.Queue[tuple]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def ensure(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._serve, name="tomas-office", daemon=True)
            self._thread.start()

    def _serve(self) -> None:
        import pythoncom
        pythoncom.CoInitialize()
        try:
            while True:
                job, box, done = self._jobs.get()
                if job is None:
                    return
                try:
                    box.append(("ok", job()))
                except BaseException as exc:      # noqa: BLE001 - relayed
                    box.append(("error", exc))
                finally:
                    done.set()
        finally:
            pythoncom.CoUninitialize()

    def submit(self, job: Callable[[], Any], timeout: float = CALL_TIMEOUT_S,
               interrupt: Optional[Callable[[], bool]] = None) -> Any:
        """Run `job` on the COM thread, polling for Esc while it runs.

        The job is abandoned, never killed — the same rule as MCP calls and
        browser calls, and for a stronger reason here: the application belongs
        to the user and closing it would discard unsaved work.
        """
        self.ensure()
        box: list = []
        done = threading.Event()
        self._jobs.put((job, box, done))
        deadline = time.monotonic() + timeout
        while not done.wait(timeout=0.25):
            if interrupt is not None and interrupt():
                raise OfficeInterrupted()
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Word did not answer within {timeout:.0f}s — it may be "
                    f"showing a dialog that needs a click")
        status, payload = box[0]
        if status == "error":
            raise payload
        return payload


_COM = _ComThread()


class _Session:
    """What survives between two tool calls."""

    app: Any = None
    progid: str = ""
    doc: Any = None
    outline_fingerprint: str = ""
    outline_taken_at: float = 0.0


_STATE = _Session()


# ---------------------------------------------------------------------------
# Attaching
# ---------------------------------------------------------------------------

def _hresult(exc: BaseException) -> Optional[int]:
    args = getattr(exc, "args", None)
    return args[0] if args and isinstance(args[0], int) else None


def _is_busy(exc: BaseException) -> bool:
    return _hresult(exc) in _BUSY_HRESULTS


def _retrying(call: Callable[[], Any]) -> Any:
    """Run a COM call, absorbing "the callee is busy" for a bounded time.

    Safe to retry because a rejected call never reached Word: it is a refusal
    to dispatch, not a half-applied edit.
    """
    last: Optional[BaseException] = None
    for attempt in range(_BUSY_RETRIES):
        try:
            return call()
        except Exception as exc:                  # noqa: BLE001 - inspected
            if not _is_busy(exc):
                raise
            last = exc
            time.sleep(_BUSY_BACKOFF_S * (attempt + 1))
    raise OfficeBusy(str(last))


def not_running_message(app: str = "word") -> str:
    """Why there is no document, and how to get one."""
    name = {"word": "Word", "excel": "Excel",
            "powerpoint": "PowerPoint"}.get(app, app)
    return (
        f"Error: {name} is not running, or is running at a different "
        f"elevation than this agent.\n\n"
        f"Open {name} and a document, or call doc_list with "
        f"start_app=true.\n\n"
        f"If {name} is already open, check whether it was started **as "
        f"administrator** while this agent was not (or the reverse): the two "
        f"do not share a Running Object Table, so the attach cannot see it. "
        f"Matching the elevation is the fix."
    )


def _attach(progid_key: str, start: bool) -> Any:
    """Return the running application object, optionally starting one."""
    import win32com.client as com

    progid = PROGIDS.get(progid_key, PROGIDS["word"])
    if (_STATE.app is not None and _STATE.progid == progid):
        try:
            _ = _STATE.app.Documents.Count if progid_key == "word" else _STATE.app.Name
            return _STATE.app
        except Exception:
            _STATE.app = None
            _STATE.doc = None

    try:
        app = com.GetActiveObject(progid)
    except Exception:
        if not start:
            raise
        # Dispatch attaches to a running instance or starts one. Only reached
        # with start=True, so it can never silently launch a second Word
        # behind the user's back.
        app = com.Dispatch(progid)
        app.Visible = True

    try:
        app.Visible = True
        app.DisplayAlerts = WD_ALERTS_NONE
    except Exception:
        pass                                     # not every app has both
    _STATE.app = app
    _STATE.progid = progid
    return app


def _documents(app: Any) -> list:
    return [app.Documents(i + 1) for i in range(app.Documents.Count)]


def _ensure_doc(app: Any) -> Any:
    current = _STATE.doc
    if current is not None:
        try:
            _ = current.Name
            return current
        except Exception:
            _STATE.doc = None
    if app.Documents.Count == 0:
        raise RuntimeError(
            "Word is running but has no document open. Open one, or call "
            "doc_list with new_document=true.")
    _STATE.doc = app.ActiveDocument
    return _STATE.doc


# ---------------------------------------------------------------------------
# Ranges — the paragraph mark problem
# ---------------------------------------------------------------------------

def _text_range(doc: Any, paragraph: Any) -> Any:
    """A paragraph's range *without* its paragraph mark.

    `Paragraph.Range` includes the trailing ¶, so `InsertAfter` on it lands at
    the start of the next paragraph — measured in Phase 0, where two
    insertions both appeared one paragraph too late. Every edit goes through
    this; nothing calls `Paragraph.Range` directly to write.
    """
    rng = paragraph.Range
    end = max(rng.Start, rng.End - 1)
    return doc.Range(rng.Start, end)


def _clean(text: str, limit: int = OUTLINE_SNIPPET) -> str:
    """Word terminates paragraphs with \\r and marks cells with \\x07."""
    flat = " ".join((text or "").replace("\x07", " ").split())
    return flat[:limit]


# ---------------------------------------------------------------------------
# Equations
# ---------------------------------------------------------------------------
#
# A built-up Word equation reads back as rubble. Measured: the equation
# `E = ∫_0^T P(t)dt` comes out of `Range.Text` as
# `'𝐸 = \r0\r𝑇\r𝑃\r𝑡\r𝑑𝑡'` -- the carriage returns are structural
# boundaries (limits, numerator/denominator) flattened onto one line, and the
# letters are math-italic codepoints rather than ASCII. That is exactly what a
# live session saw: five formulas rendered as `𝐸= 0 𝑇 𝑃(𝑡)𝑑𝑡` and no way to
# edit any of them.
#
# `OMath.Linearize()` converts the equation back to its UnicodeMath source and
# `BuildUp()` restores it, byte-identical -- verified round-trip. That pair is
# how an equation is read and written here.

#: Word's own maths autocorrect table: 780 entries mapping `\int` to ∫, `\sum`
#: to ∑, `\le` to ≤ and so on. `BuildUp` does NOT apply these -- measured,
#: `\int_0^T` builds with a literal backslash and no integral sign, while
#: `∫_0^T` builds correctly. Reading Word's table rather than shipping our own
#: means the vocabulary matches what the user's Word accepts, in their build.
_AUTOCORRECT: dict = {}


def _autocorrect_table(app: Any) -> dict:
    r"""The `
ame` -> symbol map, read from Word once per session."""
    if _AUTOCORRECT:
        return _AUTOCORRECT
    try:
        entries = app.OMathAutoCorrect.Entries
        for i in range(1, entries.Count + 1):
            entry = entries(i)
            name, value = str(entry.Name), str(entry.Value)
            if name.startswith("\\") and value:
                _AUTOCORRECT[name] = value
    except Exception:
        pass
    return _AUTOCORRECT


def expand_math(text: str, table: dict) -> str:
    r"""Turn LaTeX-shaped maths into the UnicodeMath `BuildUp` understands.

    A model asked for an equation writes LaTeX, because that is what maths
    looks like in text. Word does not read LaTeX. Measured, building each of
    these directly:

        \int_0^T P(t)dt     ->  literal backslash, no integral sign
        \sum_{i=1}^{N} P_i  ->  ∑_({i=1}_i^{N}P)   -- structurally wrong
        ∑_(i=1)^N P_i       ->  correct

    So two conversions happen before `BuildUp`. Grouping braces become
    parentheses, which is what UnicodeMath uses; and `\name` control words are
    resolved through Word's own 780-entry table, so the vocabulary is whatever
    the user's Word accepts rather than a list we would have to maintain.

    Pure, and testable without Word — the table is an argument.

    Only braces that *group* are touched: those after `_` or `^`, and the two
    arguments of `\frac`. A brace anywhere else is left alone, because in
    UnicodeMath it is a literal brace and a set is not a subscript.
    """
    if not text:
        return ""
    import re

    # The LaTeX constructs whose *shape* differs, handled before the table
    # turns their names into symbols. `\sqrt` is in Word's table and becomes √,
    # but the brace group after it is not a subscript so the `_{}`/`^{}` rule
    # below never reaches it — measured, `\sqrt{x}` built as `√({x} )`.
    text = re.sub(r"\\d?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}", r"(\1)/(\2)", text)
    text = re.sub(r"\\sqrt\s*\{([^{}]*)\}", r"√(\1)", text)

    for name in sorted(table or {}, key=len, reverse=True):
        if name in text:
            text = text.replace(name, table[name])

    # _{x} -> _(x) and ^{x} -> ^(x); repeated so nested groups collapse too.
    for _ in range(3):
        replaced = re.sub(r"([_^])\s*\{([^{}]*)\}", r"\1(\2)", text)
        if replaced == text:
            break
        text = replaced
    return text


def readable_math(text: str) -> str:
    """Math-italic codepoints back to ASCII, for something a model can read.

    `NFKC` maps 𝐸 to E, 𝑇 to T and leaves ∫, ∑, ≤ alone -- so the operators
    survive and the variable names stop being unreadable escapes.
    """
    import unicodedata
    return unicodedata.normalize("NFKC", text or "")


def _linear_source(omath: Any) -> str:
    """The editable UnicodeMath source of one equation.

    Linearize/BuildUp mutates and restores, so `doc.Saved` is put back: reading
    an equation must not leave the document looking edited. The two undo steps
    it costs cancel each other out.
    """
    try:
        raw = str(getattr(omath.Range, "Text", "") or "")
    except Exception:
        raw = ""

    try:
        omath.Linearize()
    except Exception:
        return readable_math(raw)          # never linearised; nothing to undo

    # From here the equation is FLAT in the user's document and must be built
    # back up whatever happens. Reading its text is the part that can throw,
    # and without the `finally` a failed read would leave the equation
    # permanently destroyed by what the user asked to be a read.
    try:
        source = str(omath.Range.Text or "")
    except Exception:
        source = raw
    finally:
        try:
            omath.BuildUp()
        except Exception:
            pass
    return readable_math(source).replace(chr(13), " ").strip()


def _read_equations(doc: Any) -> list[dict]:
    """Every equation, with the paragraph it sits in and its linear source.

    The paragraph is found by asking each paragraph whether it holds an
    equation, not by converting the equation's character offset into a
    paragraph number. The offset arithmetic —
    `doc.Range(0, omath.Range.Start).Paragraphs.Count` — is off by one at a
    paragraph boundary, which put every equation one paragraph early and made
    the outline list the same equation twice: once as itself and once as the
    rubble its own paragraph reads as. `Range.OMaths.Count` per paragraph is
    exact, and costs one pass.
    """
    equations: list[dict] = []
    try:
        if not doc.OMaths.Count:
            return equations
    except Exception:
        return equations

    was_saved = bool(getattr(doc, "Saved", True))
    seen = 0
    for index in range(1, doc.Paragraphs.Count + 1):
        try:
            holder = doc.Paragraphs(index).Range.OMaths
            for k in range(1, holder.Count + 1):
                seen += 1
                omath = holder(k)
                equations.append({
                    "index": seen,
                    "paragraph": index,
                    "display": int(getattr(omath, "Type", 0)) == 0,
                    "source": _linear_source(omath),
                })
        except Exception:
            continue
    try:
        doc.Saved = was_saved
    except Exception:
        pass
    return equations


# ---------------------------------------------------------------------------
# Outline
# ---------------------------------------------------------------------------

def fingerprint(descriptors: list[dict]) -> str:
    """A cheap identity for the document's shape.

    Count plus the head of every paragraph: an insertion, a deletion or a
    rewrite all change it, while re-reading an untouched document does not.
    Pure, so the guard is testable without Word.
    """
    payload = "|".join(
        f"{d.get('index')}:{(d.get('text') or '')[:40]}" for d in descriptors)
    digest = hashlib.sha1(payload.encode("utf-8", "replace")).hexdigest()
    return f"{len(descriptors)}:{digest[:16]}"


def format_outline(descriptors: list[dict], tables: list[dict], header: dict,
                   equations: Optional[list[dict]] = None,
                   max_paragraphs: int = MAX_OUTLINE_PARAGRAPHS) -> str:
    """Render the outline the model reads. Pure, and the ref is a raw index.

    The ref is the paragraph's own 1-based position in `doc.Paragraphs`, not a
    counter over what was displayed: it has to address the same paragraph when
    handed back, and a counter that skipped empty ones would drift by exactly
    the number of them.
    """
    lines = [
        f"Document: {header.get('name') or '(unnamed)'}"
        + (f"  ({header['open_count']} open, this one active)"
           if header.get("open_count", 0) > 1 else ""),
    ]
    if header.get("path"):
        lines.append(f"Path: {header['path']}"
                     + ("  — unsaved changes" if not header.get("saved", True)
                        else ""))
    else:
        lines.append("Path: (never saved)")
    notes = []
    if header.get("track_revisions"):
        notes.append("track changes is ON — edits will appear as revisions")
    if header.get("protection", WD_NO_PROTECTION) != WD_NO_PROTECTION:
        notes.append(f"document is protected (type "
                     f"{header['protection']}) — edits may be refused")
    for note in notes:
        lines.append(f"! {note}")
    lines.append("")

    # A paragraph that *is* a display equation is listed as the equation
    # rather than as the rubble its text reads as: `[p21] Звичайний
    # "𝐸= 0 𝑇 𝑃(𝑡)𝑑𝑡"` told a live session nothing it could act on.
    #
    # An *inline* equation is a different case and replacing the line there
    # loses the sentence around it. Measured: "The energy is E=a^2 where a is
    # amplitude..." was listed as just `=a^2`, so the prose the model needed
    # to edit had disappeared from the outline entirely. Inline equations are
    # therefore annotated onto the paragraph, never substituted for it.
    display_by_paragraph: dict = {}
    inline_by_paragraph: dict = {}
    for eq in (equations or []):
        target = (display_by_paragraph if eq.get("display")
                  else inline_by_paragraph)
        target.setdefault(eq["paragraph"], []).append(eq)

    shown = skipped_empty = truncated = 0
    for descriptor in descriptors:
        text = descriptor.get("text") or ""
        index = descriptor["index"]
        display_here = display_by_paragraph.get(index) or []
        inline_here = inline_by_paragraph.get(index) or []
        if not text.strip() and not display_here and not inline_here:
            skipped_empty += 1
            continue
        if shown >= max_paragraphs:
            truncated += 1
            continue
        if display_here:
            for equation in display_here:
                lines.append(
                    f'[p{index}] [eq{equation["index"]}]  display eq  '
                    f'{equation.get("source") or "(unreadable)"}')
            shown += 1
            continue
        if inline_here:
            # The sentence first — it is what the model is usually asked to
            # change — then the equations it carries, by ref.
            lines.append(f'[p{index}]  {(descriptor.get("style") or "Normal"):<12} '
                         f'"{_clean(text)}"')
            for equation in inline_here:
                lines.append(
                    f'       [eq{equation["index"]}]  inline eq   '
                    f'{equation.get("source") or "(unreadable)"}')
            shown += 1
            continue
        style = descriptor.get("style") or "Normal"
        snippet = _clean(text)
        length = descriptor.get("length", len(text))
        tail = f"  ({length} chars)" if length > OUTLINE_SNIPPET else ""
        lines.append(f'[p{descriptor["index"]}]  {style:<12} "{snippet}"{tail}')
        shown += 1

    for table in tables:
        lines.append(f'[t{table["index"]}]  Table        '
                     f'{table.get("rows", "?")}x{table.get("columns", "?")}')

    if not shown and not tables:
        lines.append("(the document is empty)")
        return "\n".join(lines)

    lines.append("")
    footer = [f"{shown} paragraph(s)"]
    if truncated:
        footer.append(f"{truncated} more not listed (cap {max_paragraphs})")
    if skipped_empty:
        footer.append(f"{skipped_empty} empty")
    if tables:
        footer.append(f"{len(tables)} table(s)")
    if equations:
        footer.append(f"{len(equations)} equation(s)")
    lines.append("; ".join(footer) + ".")
    lines.append(
        "Address these by ref, e.g. doc_edit action=replace ref=p3. Refs are "
        "void after an edit that adds or removes a paragraph — and after the "
        "user types. Prefer action=find_replace, which needs no ref.")
    if equations:
        lines.append(
            "Equations are shown as their editable source. Rewrite one with "
            "doc_edit action=equation ref=eq1 text=..., or add one with "
            "action=insert_equation. Write maths as ∫_0^T P(t)dt or as "
            "\\int_0^T P(t)dt — Word's own \\name table is applied.")
    return "\n".join(lines)


def _read_outline(doc: Any) -> tuple[list[dict], list[dict], dict]:
    """One pass over the collection, producing refs and descriptions together.

    Not two passes that ought to agree — see `BROWSER_SNAPSHOT_FIX_PLAN.md`
    §9, where exactly that assumption made `tab_snapshot` impossible on any
    shadow-DOM page. Here the second pass would be even less defensible: the
    user can type between them.
    """
    descriptors = []
    count = doc.Paragraphs.Count
    for i in range(1, count + 1):
        paragraph = doc.Paragraphs(i)
        text = paragraph.Range.Text or ""
        try:
            style = str(paragraph.Style.NameLocal)
        except Exception:
            style = "Normal"
        descriptors.append({"index": i, "text": text.rstrip("\r\x07"),
                            "style": style, "length": len(text.rstrip("\r"))})

    tables = []
    try:
        for i in range(1, doc.Tables.Count + 1):
            table = doc.Tables(i)
            tables.append({"index": i, "rows": table.Rows.Count,
                           "columns": table.Columns.Count})
    except Exception:
        pass

    try:
        path = str(doc.Path or "")
        full = str(doc.FullName) if path else ""
    except Exception:
        full = ""
    header = {
        "name": str(doc.Name),
        "path": full,
        "saved": bool(doc.Saved),
        "open_count": int(_STATE.app.Documents.Count) if _STATE.app else 1,
        "track_revisions": bool(getattr(doc, "TrackRevisions", False)),
        "protection": int(getattr(doc, "ProtectionType", WD_NO_PROTECTION)),
    }
    return descriptors, tables, header


# ---------------------------------------------------------------------------
# Operations (run on the COM thread)
# ---------------------------------------------------------------------------

def _op_list(app_key: str, select: Optional[int], new_document: bool,
             start: bool) -> str:
    app = _attach(app_key, start=start or new_document)

    if new_document:
        doc = _retrying(lambda: app.Documents.Add())
        _STATE.doc = doc
        _STATE.outline_fingerprint = ""
        return f"Created and attached to a new document: {doc.Name}"

    docs = _retrying(lambda: _documents(app))
    if not docs:
        return ("Word is running but has no document open. Open one, or call "
                "doc_list with new_document=true.")

    if select is not None:
        if not 1 <= select <= len(docs):
            return (f"Error: no document {select}. There are {len(docs)}, "
                    f"numbered 1..{len(docs)}.")
        _STATE.doc = docs[select - 1]
        _STATE.outline_fingerprint = ""
        return f"Attached to document {select}: {_STATE.doc.Name}"

    active = _ensure_doc(app)
    lines = ["Open documents (the attached one is marked ▸):", ""]
    for i, doc in enumerate(docs, start=1):
        marker = "▸" if doc.Name == active.Name else " "
        dirty = "" if doc.Saved else "  — unsaved changes"
        lines.append(f"{marker} [{i}] {doc.Name}{dirty}")
        try:
            if doc.Path:
                lines.append(f"      {doc.FullName}")
        except Exception:
            pass
    lines.append("")
    lines.append("Switch with doc_list select=<n>.")
    return "\n".join(lines)


def _op_outline(app_key: str) -> str:
    app = _attach(app_key, start=False)
    doc = _ensure_doc(app)
    descriptors, tables, header = _retrying(lambda: _read_outline(doc))
    equations = _retrying(lambda: _read_equations(doc))
    _STATE.outline_fingerprint = fingerprint(descriptors)
    _STATE.outline_taken_at = time.time()
    return format_outline(descriptors, tables, header, equations)


def _op_read(app_key: str, ref: Optional[str], max_chars: int) -> str:
    app = _attach(app_key, start=False)
    doc = _ensure_doc(app)

    if ref:
        index = _parse_ref(ref, "p")
        if index is None:
            return _unknown_ref(ref)
        count = doc.Paragraphs.Count
        if not 1 <= index <= count:
            return (f"Error: no paragraph {index}; the document has {count}. "
                    f"Call doc_outline again.")
        text = doc.Paragraphs(index).Range.Text or ""
        origin = f"{ref} of {doc.Name}"
    else:
        text = doc.Range().Text or ""
        origin = doc.Name

    text = text.replace("\r", "\n").replace("\x07", "\t").strip()
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n\n[clipped at {max_chars} chars]"
    return f"{origin}\n\n{text or '(empty)'}"


def _op_find(app_key: str, text: str, max_hits: int) -> str:
    app = _attach(app_key, start=False)
    doc = _ensure_doc(app)
    needle = (text or "").strip()
    if not needle:
        return "Error: doc_find needs text to look for."

    hits = []
    count = doc.Paragraphs.Count
    lowered = needle.lower()
    for i in range(1, count + 1):
        body = (doc.Paragraphs(i).Range.Text or "").rstrip("\r")
        if lowered in body.lower():
            hits.append((i, _clean(body)))
            if len(hits) >= max_hits:
                break

    if not hits:
        return (f"No paragraph contains {needle!r}. The search is literal and "
                f"case-insensitive; try a shorter fragment.")
    lines = [f"{len(hits)} paragraph(s) containing {needle!r}:", ""]
    lines += [f'[p{i}]  "{snippet}"' for i, snippet in hits]
    lines.append("")
    lines.append("Edit one with doc_edit ref=pN, or change every occurrence "
                 "at once with doc_edit action=find_replace.")
    return "\n".join(lines)


def _guard_unchanged(doc: Any) -> Optional[str]:
    """Refuse an index-addressed edit if the document moved under us."""
    if not _STATE.outline_fingerprint:
        return ("Error: no outline has been taken, so a paragraph ref "
                "addresses nothing. Call doc_outline first.")
    descriptors, _tables, _header = _read_outline(doc)
    current = fingerprint(descriptors)
    if current != _STATE.outline_fingerprint:
        _STATE.outline_fingerprint = ""
        return ("Error: the document has changed since doc_outline was called "
                "— the user may have typed, or an earlier edit moved the "
                "paragraphs. Refs would now address the wrong text, so this "
                "edit was NOT applied. Call doc_outline again.")
    return None


def _replace_long(doc: Any, find: str, replacement: str) -> str:
    """Find/replace for strings Word's own Find refuses to take.

    Scans paragraphs and rewrites the ones that contain `find`. Slower than
    Word's Find and exact rather than case-insensitive, which is the honest
    trade for handling the case Find cannot: at 256 characters it does not
    degrade, it raises.
    """
    changed = []
    for i in range(1, doc.Paragraphs.Count + 1):
        paragraph = doc.Paragraphs(i)
        body = paragraph.Range.Text or ""
        stripped = body.rstrip(chr(13))
        if find not in stripped:
            continue

        # Only the matched span is rewritten, not the paragraph. Setting the
        # whole paragraph's `.Text` was measured to flatten it: bold runs, and
        # any inline equation, are lost across the parts that never matched.
        # Word's own Find does not do that, and a fallback for a long string
        # must not be worse than the thing it stands in for.
        #
        # Backwards, so that replacing one occurrence does not move the
        # offsets of the ones still to come.
        start = paragraph.Range.Start
        offsets = []
        at = stripped.find(find)
        while at != -1:
            offsets.append(at)
            at = stripped.find(find, at + len(find))
        for at in reversed(offsets):
            doc.Range(start + at, start + at + len(find)).Text = replacement
        changed.append(i)
    _STATE.outline_fingerprint = ""
    if not changed:
        return (f"Nothing matched that {len(find)}-character string, so "
                f"nothing changed. Over {MAX_FIND_CHARS} characters the match "
                f"is exact, including case and punctuation — use doc_find with "
                f"a short fragment to see the text as Word holds it.")
    refs = ", ".join(f"p{i}" for i in changed)
    return (f"Replaced a {len(find)}-character string in {refs} of "
            f"{doc.Name}.\nWord's Find caps arguments at {MAX_FIND_CHARS} "
            f"characters, so this went paragraph by paragraph instead.\n"
            f"The outline is void; call doc_outline. Ctrl+Z in Word undoes "
            f"this — once per paragraph.")


def _op_equation(app: Any, doc: Any, action: str, ref: Optional[str],
                 text: Optional[str]) -> str:
    """Insert a new equation, or rewrite an existing one.

    Both go through `BuildUp`, which is what turns linear source into a real
    Word equation object rather than a line of maths-looking text.
    """
    if not text:
        return (f"Error: action={action} needs text — the equation in linear "
                f"form, e.g. 'E = ∫_0^T P(t)dt' or 'x^2 + y^2 = z^2'.")

    source = expand_math(text, _autocorrect_table(app))

    if action == "equation":
        number = _parse_ref(ref or "", "eq")
        if number is None:
            return (f"Error: '{ref}' is not an equation ref. They look like "
                    f"'eq1' and come from doc_outline.")
        try:
            total = doc.OMaths.Count
        except Exception:
            total = 0
        if not 1 <= number <= total:
            return (f"Error: no equation {number}; the document has {total}. "
                    f"Call doc_outline again.")
        omath = doc.OMaths(number)
        was = _linear_source(omath)
        omath.Linearize()
        omath.Range.Text = source
        doc.OMaths(number).BuildUp()
        _STATE.outline_fingerprint = ""
        return (f"Rewrote eq{number} of {doc.Name}.\n"
                f"  was: {was}\n  now: {readable_math(source)}\n"
                f"The outline is void; call doc_outline. Ctrl+Z undoes this.")

    # insert_equation
    if ref:
        index = _parse_ref(ref, "p")
        if index is None:
            return _unknown_ref(ref)
        if not 1 <= index <= doc.Paragraphs.Count:
            return f"Error: no paragraph {index}."
        doc.Paragraphs(index).Range.InsertParagraphAfter()
        target_index = index + 1
    else:
        doc.Content.InsertParagraphAfter()
        target_index = doc.Paragraphs.Count

    target = _text_range(doc, doc.Paragraphs(target_index))
    target.Text = source
    target.OMaths.Add(target)
    target.OMaths.BuildUp()
    _STATE.outline_fingerprint = ""
    return (f"Inserted an equation as paragraph {target_index} of "
            f"{doc.Name}:\n  {readable_math(source)}\n"
            f"The outline is void; call doc_outline. Ctrl+Z undoes this.")


def _op_edit(app_key: str, action: str, ref: Optional[str], text: Optional[str],
             find: Optional[str], style: Optional[str]) -> str:
    app = _attach(app_key, start=False)
    doc = _ensure_doc(app)

    if action not in EDIT_ACTIONS:
        return (f"Error: unknown action '{action}'. Use one of: "
                f"{', '.join(EDIT_ACTIONS)}.")

    if int(getattr(doc, "ProtectionType", WD_NO_PROTECTION)) != WD_NO_PROTECTION:
        return ("Error: this document is protected, so Word will not accept "
                "edits. Remove the restriction in Word first — this tool will "
                "not do it for you.")

    # ── equations, which are objects rather than text ──
    if action in ("insert_equation", "equation"):
        return _op_equation(app, doc, action, ref, text)

    # ── find_replace needs no ref, and is the preferred path ──
    if action == "find_replace":
        if not find:
            return "Error: action=find_replace needs `find`."

        # Word's Find takes at most 255 characters per argument and raises
        # "String parameter too long" above it. A live session hit this three
        # times in a row rewriting whole paragraphs. Falling back to a direct
        # paragraph rewrite does what was asked instead of refusing it.
        if len(find) > MAX_FIND_CHARS or len(text or "") > MAX_FIND_CHARS:
            return _replace_long(doc, find, text or "")

        before_text = doc.Range().Text or ""

        def replace_all() -> None:
            rng = doc.Content
            rng.Find.ClearFormatting()
            rng.Find.Replacement.ClearFormatting()
            # POSITIONAL, not keyword. Word's Find.Execute through pywin32
            # late binding accepts keyword arguments, returns True, and
            # changes nothing — measured: the document was byte-identical
            # afterwards while the call reported success. Announcing a
            # replacement that did not happen is worse than failing outright,
            # so the order is spelled out and the result verified below.
            # (FindText, MatchCase, MatchWholeWord, MatchWildcards,
            #  MatchSoundsLike, MatchAllWordForms, Forward, Wrap, Format,
            #  ReplaceWith, Replace)
            rng.Find.Execute(find, False, False, False, False, False,
                             True, WD_FIND_STOP, False,
                             (text or ""), WD_REPLACE_ALL)

        _retrying(replace_all)
        _STATE.outline_fingerprint = ""
        if (doc.Range().Text or "") == before_text:
            return (f"Nothing matched {find!r}, so nothing changed. The "
                    f"search is literal and case-insensitive — check spacing "
                    f"and punctuation, or use doc_find to see the text as "
                    f"Word actually holds it.")
        return (f"Replaced {find!r} with {(text or '')!r} throughout "
                f"{doc.Name}.\nThe outline is void; call doc_outline. "
                f"Ctrl+Z in Word undoes this.")

    index = _parse_ref(ref or "", "p")
    if index is None:
        return _unknown_ref(ref or "")

    problem = _guard_unchanged(doc)
    if problem:
        return problem

    count = doc.Paragraphs.Count
    if not 1 <= index <= count:
        return (f"Error: no paragraph {index}; the document has {count}.")

    paragraph = doc.Paragraphs(index)
    before = _clean((paragraph.Range.Text or "").rstrip("\r"), 60)

    def apply() -> None:
        if action == "replace":
            _text_range(doc, paragraph).Text = text or ""
        elif action == "insert_after":
            _text_range(doc, paragraph).InsertAfter(text or "")
        elif action == "insert_before":
            paragraph.Range.InsertBefore(text or "")
        elif action == "delete":
            paragraph.Range.Delete()
        elif action == "style":
            wanted = (style or text or "Normal")
            builtin = _BUILTIN_STYLES.get(wanted.strip().lower())
            paragraph.Style = (doc.Styles(builtin) if builtin is not None
                               else wanted)

    if action == "style" and not (style or text):
        return "Error: action=style needs a style name, e.g. 'Heading 1'."
    if action in ("replace", "insert_after", "insert_before") and text is None:
        return f"Error: action={action} needs text."

    try:
        _retrying(apply)
    except OfficeBusy:
        return ("Error: Word stayed busy and refused the edit — it may be "
                "showing a dialog, or a menu may be open. Nothing was "
                "changed. Try again once Word is idle.")
    except Exception as exc:
        if action == "style":
            try:
                known = ", ".join(
                    f"{name} (= {str(doc.Styles(const).NameLocal)})"
                    for name, const in (("Normal", -1), ("Title", -63),
                                        ("Heading 1", -2), ("Heading 2", -3)))
            except Exception:
                known = "Normal, Title, Heading 1..Heading 9"
            return (f"Error: no style {(style or text)!r} in this document. "
                    f"Built-in styles are addressed in English whatever "
                    f"language Word runs in: {known}. A custom style must be "
                    f"spelled exactly as it appears in Word.")
        return f"Error: {action} on {ref} failed: {str(exc).splitlines()[0]}"

    # Only an edit that adds or removes a paragraph re-indexes the rest. A
    # `replace` or a `style` leaves every ref pointing where it did, so the
    # fingerprint is *recomputed* rather than cleared and the next edit can go
    # straight through. Clearing it unconditionally cost a live session about
    # twenty round-trips: every one of its edits was followed by a doc_outline
    # it did not need, each one re-reading the whole document.
    #
    # This is still safe against the case the guard exists for. The new
    # fingerprint describes the document as it stands *after* this edit, so a
    # human typing before the next call still mismatches and is still refused.
    if doc.Paragraphs.Count == count:
        _STATE.outline_fingerprint = fingerprint(_read_outline(doc)[0])
        tail = ("Paragraph refs are still valid — this edit changed no "
                "paragraph count. Ctrl+Z in Word undoes it.")
    else:
        _STATE.outline_fingerprint = ""
        tail = ("Paragraphs were added or removed, so every ref has shifted; "
                "call doc_outline before the next one. Ctrl+Z undoes this.")

    # A style change leaves the text alone, so reporting was/now would print
    # the same string twice and read as "nothing happened". Report what
    # actually changed: the style, in this Word's own language.
    if action == "style":
        try:
            now_style = str(doc.Paragraphs(index).Style.NameLocal)
        except Exception:
            now_style = str(style or text)
        return (f"Did: style on {ref} of {doc.Name}.\n"
                f'  "{before}" is now styled {now_style}\n{tail}')

    after = _clean((doc.Paragraphs(min(index, doc.Paragraphs.Count))
                    .Range.Text or "").rstrip("\r"), 60)
    return (f"Did: {action} on {ref} of {doc.Name}.\n"
            f'  was: "{before}"\n  now: "{after}"\n{tail}')


def _op_save(app_key: str, path: Optional[str]) -> str:
    app = _attach(app_key, start=False)
    doc = _ensure_doc(app)
    if path:
        _retrying(lambda: doc.SaveAs2(str(path)))
        return f"Saved as {doc.FullName}."
    try:
        never_saved = not str(doc.Path or "")
    except Exception:
        never_saved = True
    if never_saved:
        return ("Error: this document has never been saved, so there is no "
                "path to save to. Give doc_save a `path`.")
    _retrying(lambda: doc.Save())
    return (f"Saved {doc.FullName}.\nNote: saving is the one action Ctrl+Z "
            f"does not undo.")


# ---------------------------------------------------------------------------
# Refs
# ---------------------------------------------------------------------------

def _parse_ref(ref: str, prefix: str) -> Optional[int]:
    ref = (ref or "").strip().lower()
    if not ref.startswith(prefix):
        return None
    try:
        return int(ref[len(prefix):])
    except ValueError:
        return None


def _unknown_ref(ref: str) -> str:
    return (f"Error: '{ref}' is not a paragraph ref. They look like 'p3' and "
            f"come from doc_outline or doc_find.")


# ---------------------------------------------------------------------------
# The synchronous surface the tool handlers call
# ---------------------------------------------------------------------------

def _run(job: Callable[[], str], interrupt: Optional[Callable[[], bool]],
         app_key: str = "word") -> str:
    """Submit one operation and turn every failure into a readable line."""
    import importlib.util
    if importlib.util.find_spec("win32com") is None:
        return ("Error: pywin32 is not installed, so no Office application "
                "can be reached. pip install pywin32")
    try:
        return _COM.submit(job, interrupt=interrupt)
    except OfficeInterrupted:
        return ("[interrupted] Word was still working when Esc was pressed. "
                "The document keeps whatever state it reached; this result "
                "was not collected.")
    except OfficeBusy:
        return ("Error: Word stayed busy through every retry — it is most "
                "likely showing a dialog that needs a click.")
    except TimeoutError as exc:
        return f"Error: {exc}"
    except Exception as exc:
        code = _hresult(exc)
        if code in (-2147221021, -2147221164, -2146959355):
            return not_running_message(app_key)
        message = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
        return f"Error: {message}"


def documents(select: Optional[int] = None, new_document: bool = False,
              start_app: bool = False, app: str = "word",
              interrupt: Optional[Callable[[], bool]] = None) -> str:
    return _run(lambda: _op_list(app, select, new_document, start_app),
                interrupt, app)


def outline(app: str = "word",
            interrupt: Optional[Callable[[], bool]] = None) -> str:
    return _run(lambda: _op_outline(app), interrupt, app)


def read(ref: Optional[str] = None, max_chars: int = MAX_READ_CHARS,
         app: str = "word",
         interrupt: Optional[Callable[[], bool]] = None) -> str:
    return _run(lambda: _op_read(app, ref, max_chars), interrupt, app)


def find(text: str = "", max_hits: int = 20, app: str = "word",
         interrupt: Optional[Callable[[], bool]] = None) -> str:
    return _run(lambda: _op_find(app, text, max_hits), interrupt, app)


def edit(action: str = "", ref: Optional[str] = None,
         text: Optional[str] = None, find: Optional[str] = None,
         style: Optional[str] = None, app: str = "word",
         interrupt: Optional[Callable[[], bool]] = None) -> str:
    return _run(lambda: _op_edit(app, action, ref, text, find, style),
                interrupt, app)


def save(path: Optional[str] = None, app: str = "word",
         interrupt: Optional[Callable[[], bool]] = None) -> str:
    return _run(lambda: _op_save(app, path), interrupt, app)


def shutdown() -> None:
    """Drop the references without touching the user's application.

    Nothing is closed and nothing is saved. This module attached to a process
    it does not own; closing Word here would discard work the user can see on
    their screen.
    """
    _STATE.app = None
    _STATE.doc = None
    _STATE.outline_fingerprint = ""


atexit.register(shutdown)
