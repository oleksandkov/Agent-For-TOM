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
                "style", "find_replace")

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

    shown = skipped_empty = truncated = 0
    for descriptor in descriptors:
        text = descriptor.get("text") or ""
        if not text.strip():
            skipped_empty += 1
            continue
        if shown >= max_paragraphs:
            truncated += 1
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
    lines.append("; ".join(footer) + ".")
    lines.append(
        "Address these by ref, e.g. doc_edit action=replace ref=p3. Refs are "
        "void after any edit that adds or removes a paragraph — and after the "
        "user types. Prefer action=find_replace, which needs no ref.")
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
    _STATE.outline_fingerprint = fingerprint(descriptors)
    _STATE.outline_taken_at = time.time()
    return format_outline(descriptors, tables, header)


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

    # ── find_replace needs no ref, and is the preferred path ──
    if action == "find_replace":
        if not find:
            return "Error: action=find_replace needs `find`."
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

    # Anything that adds or removes a paragraph re-indexes the rest.
    _STATE.outline_fingerprint = ""
    tail = ("The outline is void; call doc_outline before the next ref. "
            "Ctrl+Z in Word undoes this.")

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
