"""Act in the browser the user already has open — their tabs, their logins.

`agent.handle_fetch_url_with_browser` launches a *new* headless Chrome per
call. That is the right tool for reading a page and the wrong one for working
in a session: the launched browser is anonymous, logged out, and dead by the
time the handler returns. This module does the opposite — it attaches over
CDP to a Chrome that is already running, so `browser.contexts[0].pages` are
the tabs the user is looking at, cookies and all. Nothing here launches a
browser to *read* with; that is what the fetch tools are for.

Three decisions carry the module.

**One event loop, on a thread, for the whole session.** Every other browser
path in this project wraps `asyncio.run(...)` around one call, which builds an
event loop and destroys it again — and a Playwright object created inside a
dead loop is unusable in the next call, so the connection, the attached page
and the element map would all evaporate between two tool calls that are meant
to be consecutive steps of the same job. `_Loop` owns a single loop running
forever on a daemon thread and every call is submitted into it with
`run_coroutine_threadsafe`, which is the only arrangement in which "click the
button I found in the last snapshot" can mean anything.

**Waiting is interruptible; the browser is never torn down.** Submitting to a
loop we do not block on gives Esc a cancellation point for free, and the
handlers pass `agent._CURRENT_INTERRUPT` down as `interrupt`. The wait is
abandoned, not the browser — for exactly the reason
`_call_mcp_tool_interruptibly` abandons rather than kills: this connection is
one long-lived thing for the whole session, and it is attached to a window
the *user* owns. Killing it on a slow `wait_for_selector` would close tabs
they were using.

**Elements are named by ref, never by selector.** Handing a model raw HTML
and asking for CSS costs thousands of tokens and still has it guessing.
`snapshot()` returns a numbered outline of the visible interactive elements
(`[e7] button "Send"`) and keeps the aligned ElementHandles, so `act()` takes
`ref="e7"`.

The handles and the descriptions come from **one** traversal, never from two
queries that ought to agree. They used to: `page.query_selector_all(SEL)` for
the handles and `document.querySelectorAll(SEL)` inside `evaluate` for the
descriptions, on the stated assumption that both walk the same document in the
same order. They do not. Playwright's CSS engine pierces open shadow roots and
`document.querySelectorAll` does not, so on any page built from web components
the two lists differ by exactly the number of matches inside shadow roots —
measured live on gemini.google.com, 48 against 42, from 12 open shadow hosts,
identically on every call. The length check caught it, but only after the
outline was already impossible, and its message advised a retry that could
never succeed. `_WALK_JS` now returns the node list once and `_DESCRIBE_JS`
describes that same array, so alignment holds by construction and there is no
second query left to disagree with. The walk also recurses into open shadow
roots, which was the more serious half of the bug: had the counts happened to
match, the outline would have silently omitted precisely the controls a modern
app keeps in its components.

The JS still never filters — it reports `visible` and lets Python drop the
pair — because the ref is an index into the unfiltered list. A stale handle is
reported as stale rather than silently retried: the honest answer is
"re-snapshot", and a re-clicked button on a page that has since navigated is
the kind of mistake this tool must not make quietly.

Known limits, stated because a tool that returns less than it was asked for
has to say so: the snapshot covers the main frame only, so controls inside an
iframe are invisible to it; a shadow root opened with `mode: "closed"` is
unreachable by any means, Playwright included; and Chrome 136+ ignores
`--remote-debugging-port` unless `--user-data-dir` names something other than
the default profile, which is why `launch_argv` always passes one.
"""

from __future__ import annotations

import asyncio
import atexit
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from concurrent.futures import TimeoutError as _FutureTimeout
from pathlib import Path
from typing import Any, Callable, Optional

#: Where the attached browser is expected to be listening. Overridable
#: because a user may already run Chrome on another port for other tooling.
#:
#: `localhost` and not `127.0.0.1`, which is not a style preference. Chrome
#: validates the Host header on the DevTools HTTP endpoint and answers the
#: literal IP with a bare 404 — measured on Chrome 152.0.7977.65: the port is
#: LISTENING, the TCP connection succeeds, `/json/version` over
#: `http://localhost:9222` returns 200 and the identical request over
#: `http://127.0.0.1:9222` returns 404. Every path in this module is a
#: "browser is not running" message away from that mistake, so the IP forms
#: are rewritten rather than trusted. See `_normalise_cdp_url`.
DEFAULT_CDP_URL = os.environ.get("TOMAS_CDP_URL", "http://localhost:9222")

#: The profile the launcher uses. Deliberately *not* the user's default one:
#: since Chrome 136 the remote-debugging flag is ignored when the default
#: user-data-dir is in play, so a launcher that reused it would appear to work
#: and then never answer on the port. The user logs into their sites once
#: here and the profile persists like any other.
PROFILE_DIR = Path.home() / ".tomas" / "chrome-profile"
SCREENSHOT_DIR = Path.home() / ".tomas" / "screenshots"
DEBUG_PORT = int(os.environ.get("TOMAS_CDP_PORT", "9222"))

#: Elements listed by one snapshot. A search-results page runs to several
#: hundred; past this the outline stops being something a model reads and
#: starts being something it skims, at ~12 tokens a line.
MAX_SNAPSHOT_ELEMENTS = 120

#: Page text returned by one `read`. Matches the spirit of read_file's cap:
#: clip and say so, never refuse.
MAX_READ_CHARS = 20_000

#: How long one Playwright action may take before it reports failure, and how
#: long this module waits on the loop thread before giving up on it. The outer
#: number is larger on purpose: the inner timeout produces a *diagnosis*
#: ("element not visible"), the outer one produces only "it hung".
ACTION_TIMEOUT_MS = 15_000
CALL_TIMEOUT_S = 45.0
NAVIGATION_TIMEOUT_MS = 30_000

#: Everything a person can operate. `[onclick]` and the ARIA roles catch the
#: div-as-button that real sites are built from; without them the outline of a
#: modern web app is empty and the tool looks broken.
INTERACTIVE_SELECTOR = (
    'a[href], button, input, select, textarea, summary, '
    '[role="button"], [role="link"], [role="checkbox"], [role="radio"], '
    '[role="tab"], [role="menuitem"], [role="menuitemcheckbox"], '
    '[role="switch"], [role="combobox"], [role="textbox"], [role="option"], '
    '[role="searchbox"], [contenteditable="true"], [onclick]'
)

ACTIONS = ("click", "double_click", "type", "press", "select", "hover",
           "check", "uncheck", "clear", "scroll")


class BrowserInterrupted(Exception):
    """Esc was pressed while a call was still on the loop thread."""


# ---------------------------------------------------------------------------
# The loop thread
# ---------------------------------------------------------------------------

class _Loop:
    """One asyncio loop, owned for the life of the process.

    `ensure()` is idempotent and cheap, so every entry point can call it
    without anyone having to remember an init step.
    """

    def __init__(self) -> None:
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def ensure(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if (self._loop is not None and self._thread is not None
                    and self._thread.is_alive()):
                return self._loop
            loop = asyncio.new_event_loop()
            thread = threading.Thread(
                target=self._serve, args=(loop,),
                name="tomas-browser", daemon=True,
            )
            thread.start()
            self._loop, self._thread = loop, thread
            return loop

    @staticmethod
    def _serve(loop: asyncio.AbstractEventLoop) -> None:
        asyncio.set_event_loop(loop)
        loop.run_forever()

    def submit(self, coro, timeout: float = CALL_TIMEOUT_S,
               interrupt: Optional[Callable[[], bool]] = None) -> Any:
        """Run `coro` on the loop thread, polling for Esc while it runs.

        The poll interval is the same 0.25 s `_call_mcp_tool_interruptibly`
        uses — fast enough that Esc feels immediate, slow enough that a
        multi-second page load costs a handful of wakeups.
        """
        loop = self.ensure()
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        deadline = time.monotonic() + timeout
        while True:
            if interrupt is not None and interrupt():
                future.cancel()
                raise BrowserInterrupted()
            try:
                return future.result(timeout=0.25)
            except _FutureTimeout:
                if time.monotonic() >= deadline:
                    future.cancel()
                    raise TimeoutError(
                        f"the browser did not answer within {timeout:.0f}s"
                    )


_LOOP = _Loop()


class _Session:
    """What survives between two tool calls. Module-level by design."""

    playwright: Any = None
    browser: Any = None
    page: Any = None
    cdp_url: str = ""
    refs: dict[str, Any] = {}
    refs_url: str = ""
    refs_taken_at: float = 0.0


_STATE = _Session()


# ---------------------------------------------------------------------------
# Finding and starting a browser
# ---------------------------------------------------------------------------

#: Where Chrome and Edge actually install on Windows. `shutil.which` finds
#: neither by default — Chrome is not on PATH in a standard install.
_CHROME_CANDIDATES = (
    r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
    r"%LocalAppData%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
)

_POSIX_CANDIDATES = (
    "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
    "microsoft-edge",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
)


def find_browser_executable() -> Optional[str]:
    """The Chromium-family browser to attach to, or None."""
    override = os.environ.get("TOMAS_BROWSER_PATH")
    if override and Path(override).exists():
        return override
    if sys.platform == "win32":
        for candidate in _CHROME_CANDIDATES:
            path = Path(os.path.expandvars(candidate))
            if path.exists():
                return str(path)
        return None
    for candidate in _POSIX_CANDIDATES:
        found = shutil.which(candidate) if "/" not in candidate else (
            candidate if Path(candidate).exists() else None)
        if found:
            return found
    return None


def launch_argv(executable: str, port: int = DEBUG_PORT,
                profile: Path = PROFILE_DIR) -> list[str]:
    """The command that starts a debuggable browser.

    `--user-data-dir` is not optional and not a preference: Chrome 136 and
    later ignore `--remote-debugging-port` entirely when the default profile
    directory is in use, which fails by *silence* — the browser opens, the
    port never answers, and nothing says why.
    """
    return [
        executable,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
    ]


def _normalise_cdp_url(url: str) -> str:
    """Point a CDP URL at `localhost`, whatever loopback spelling it arrived in.

    Chrome answers `127.0.0.1` and `[::1]` with 404 on the DevTools endpoint
    while answering `localhost` with 200 (see DEFAULT_CDP_URL). A user who
    sets TOMAS_CDP_URL to the IP — or copies one out of another tool's docs —
    would otherwise be told, at length, that no browser is running while it
    sits there listening.
    """
    return (url or DEFAULT_CDP_URL).replace("//127.0.0.1:", "//localhost:")                                    .replace("//[::1]:", "//localhost:")


def cdp_version(url: str = "", timeout: float = 1.5) -> Optional[dict]:
    """What is listening on the CDP endpoint, or None if nothing is.

    Asked over plain HTTP rather than by attempting a Playwright connection:
    the answer is needed before every call, and `connect_over_cdp` against a
    dead port costs seconds and raises an exception whose text is about
    websockets rather than about a browser that is not running.
    """
    endpoint = _normalise_cdp_url(url).rstrip("/") + "/json/version"
    try:
        with urllib.request.urlopen(endpoint, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", "replace"))
    except Exception:
        return None


def start_browser(port: int = DEBUG_PORT, wait_s: float = 25.0) -> str:
    """Start a debuggable browser and wait for the port to answer.

    Detached on purpose: the browser must outlive the tool call that started
    it, and on Windows a child in the same process group dies with the agent.
    """
    already = cdp_version()
    if already:
        return f"Already running: {already.get('Browser', 'a browser')}."

    executable = find_browser_executable()
    if not executable:
        return ("Error: no Chrome or Edge found. Set TOMAS_BROWSER_PATH to "
                "the executable, or start the browser yourself with "
                f"--remote-debugging-port={port} and a --user-data-dir "
                "other than your default profile.")

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    argv = launch_argv(executable, port)
    kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen(argv, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, **kwargs)
    except Exception as exc:
        return f"Error: could not start {executable}: {exc}"

    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        version = cdp_version()
        if version:
            return (f"Started {version.get('Browser', 'browser')} on port "
                    f"{port} with profile {PROFILE_DIR}. It has its own "
                    f"cookie jar — sign in once in this window and the "
                    f"profile keeps you signed in.")
        time.sleep(0.5)
    return (f"Error: the browser started but nothing answered on port {port} "
            f"within {wait_s:.0f}s. If it opened with your normal profile, "
            f"Chrome 136+ will have ignored the debugging flag — close every "
            f"Chrome window and try again.")


def not_connected_message(port: int = DEBUG_PORT) -> str:
    """Why there is no browser, and the two ways to get one."""
    executable = find_browser_executable() or "chrome.exe"
    command = " ".join(
        f'"{part}"' if " " in part else part
        for part in launch_argv(executable, port)
    )
    return (
        f"Error: no debuggable browser is listening on {DEFAULT_CDP_URL}.\n\n"
        f"Start one with `tab_list` and start_browser=true, or by hand:\n"
        f"  {command}\n\n"
        f"The separate --user-data-dir is required, not cosmetic: Chrome 136 "
        f"and later ignore --remote-debugging-port on the default profile, so "
        f"a browser started without it opens normally and never answers. Sign "
        f"in to your sites once in that window; {PROFILE_DIR} persists."
    )


# ---------------------------------------------------------------------------
# Connecting and choosing a tab
# ---------------------------------------------------------------------------

async def _connect(cdp_url: str) -> None:
    """Attach to the running browser, reusing the connection if it is live."""
    browser = _STATE.browser
    if browser is not None:
        try:
            if browser.is_connected():
                return
        except Exception:
            pass
        _STATE.browser = None
        _STATE.page = None

    from playwright.async_api import async_playwright  # deferred; ~105 ms

    if _STATE.playwright is None:
        _STATE.playwright = await async_playwright().start()
    _STATE.browser = await _STATE.playwright.chromium.connect_over_cdp(cdp_url)
    _STATE.cdp_url = cdp_url
    _STATE.page = None


def _pages() -> list[Any]:
    """Every open tab, across every context, in the browser's own order."""
    pages: list[Any] = []
    for context in (_STATE.browser.contexts if _STATE.browser else []):
        pages.extend(context.pages)
    return [p for p in pages if not p.is_closed()]


async def _active_page(pages: list[Any]) -> Any:
    """The tab the user is actually looking at.

    CDP does not report which tab is foregrounded, but the page does:
    `document.visibilityState` is "visible" only for the front tab of a
    window. It answers "hidden" everywhere if the window is minimised, so
    this falls back to the last-opened tab rather than failing.
    """
    for page in reversed(pages):
        try:
            state = await page.evaluate("() => document.visibilityState")
            if state == "visible":
                return page
        except Exception:
            continue
    return pages[-1]


async def _ensure_page(cdp_url: str) -> Any:
    await _connect(cdp_url)
    pages = _pages()
    if not pages:
        raise RuntimeError(
            "the browser is running but has no open tab — open one, or call "
            "tab_list with new_tab set to a URL."
        )
    current = _STATE.page
    if current is not None and not current.is_closed() and current in pages:
        return current
    _STATE.page = await _active_page(pages)
    return _STATE.page


def _invalidate_refs(reason: str = "") -> None:
    _STATE.refs = {}
    _STATE.refs_url = ""


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------

#: Collects the node list, once, recursing into open shadow roots.
#:
#: `document.querySelectorAll` cannot see into a shadow root, which is where a
#: web-component app keeps its real controls; walking `'*'` per root and
#: testing `matches` reaches them. Returned as a live array so `_DESCRIBE_JS`
#: can describe *these* nodes rather than re-querying for them — see the module
#: docstring for what re-querying cost.
#:
#: `mode: "closed"` roots have no `.shadowRoot` and are unreachable here, by
#: Playwright, and by anything else. That is a property of the page, not a gap
#: in this walker.
_WALK_JS = """
(sel) => {
  const out = [];
  const visit = (root) => {
    for (const el of root.querySelectorAll('*')) {
      if (el.matches(sel)) out.push(el);
      if (el.shadowRoot) visit(el.shadowRoot);
    }
  };
  visit(document);
  return out;
}
"""

#: Describes the array `_WALK_JS` returned. Reports every node, filtering
#: nothing: the ref is an index into this list, so dropping a hidden element
#: here would shift every later ref onto the wrong element.
_DESCRIBE_JS = """
(nodes) => {
  const clean = (s) => (s || '').replace(/\\s+/g, ' ').trim().slice(0, 90);
  return nodes.map((el) => {
    const tag = el.tagName.toLowerCase();
    let visible = true;
    try {
      const rect = el.getBoundingClientRect();
      const style = getComputedStyle(el);
      visible = (rect.width > 0 || rect.height > 0)
        && style.visibility !== 'hidden'
        && style.display !== 'none'
        && Number(style.opacity) > 0.05;
    } catch (e) { visible = false; }
    let role = el.getAttribute('role') || '';
    if (!role) {
      if (tag === 'a') role = 'link';
      else if (tag === 'button' || tag === 'summary') role = 'button';
      else if (tag === 'select') role = 'combobox';
      else if (tag === 'textarea') role = 'textbox';
      else if (tag === 'input') {
        const t = (el.getAttribute('type') || 'text').toLowerCase();
        role = ({checkbox: 'checkbox', radio: 'radio', submit: 'button',
                 button: 'button', reset: 'button', file: 'file',
                 search: 'searchbox', range: 'slider'})[t] || 'textbox';
      } else role = 'clickable';
    }
    // contenteditable counts as a control: its text is its *content*, so
    // letting innerText become its name would rename the element to whatever
    // the model last typed into it.
    const isControl = tag === 'input' || tag === 'select' || tag === 'textarea'
      || el.isContentEditable === true;
    const type = tag === 'input'
      ? (el.getAttribute('type') || 'text').toLowerCase() : '';
    const toggle = type === 'checkbox' || type === 'radio';
    // A checkbox's `value` is the string it submits -- "on" by default, which
    // says nothing about its state and reads as content. `checked` is the
    // state, and it is reported separately.
    let value = '';
    if (tag === 'textarea' || (tag === 'input' && !toggle && type !== 'submit'
                               && type !== 'button' && type !== 'reset'))
      value = clean(el.value);
    else if (tag === 'select' && el.selectedOptions && el.selectedOptions[0])
      value = clean(el.selectedOptions[0].textContent);
    // A contenteditable has no `.value`; its content is its text. Without
    // this it reported "empty" immediately after a successful type -- measured
    // on gemini.google.com, whose composer is a contenteditable div -- and a
    // model that re-snapshots to confirm its own typing would type again,
    // and again.
    else if (el.isContentEditable === true) value = clean(el.innerText);
    // A form control's own text is not its name: a <select>'s innerText is
    // every option run together ("Small Large") and a bare checkbox has none
    // at all, while the thing a person reads is the <label> pointing at it.
    // Measured on a four-field form: label lookup named two controls that
    // innerText left as "(unnamed)" or worse.
    const labelled = el.labels && el.labels[0]
      ? clean(el.labels[0].textContent) : '';
    const name = clean(el.getAttribute('aria-label'))
      || labelled
      || clean(el.getAttribute('placeholder'))
      || (isControl ? '' : clean(el.innerText))
      || clean(el.getAttribute('title'))
      || clean(el.getAttribute('alt'))
      || clean(el.value)
      || clean(el.getAttribute('name'))
      || clean(el.getAttribute('href'));
    return {
      role: role,
      name: name,
      value: value,
      visible: visible,
      disabled: !!(el.disabled || el.getAttribute('aria-disabled') === 'true'),
      checked: el.checked === true || el.getAttribute('aria-checked') === 'true',
      // A checkbox is not an empty text field. `editable` drives the
      // "empty" note, and printing it on a tickbox invites a model to
      // type into one.
      editable: (tag === 'input' && !toggle && type !== 'submit'
                 && type !== 'button' && type !== 'reset')
                || tag === 'textarea' || el.isContentEditable === true
    };
  });
}
"""


def format_snapshot(descriptors: list[dict], title: str, url: str,
                    max_elements: int = MAX_SNAPSHOT_ELEMENTS) -> str:
    """Render the outline the model reads. Pure, so it is testable.

    The ref is the descriptor's index, not a counter over the visible ones:
    it has to address the same slot in the handle list, and a counter that
    skipped hidden elements would drift by exactly the number of them.
    """
    lines = [f"Tab: {title or '(untitled)'}", f"URL: {url}", ""]
    shown = 0
    skipped_hidden = 0
    truncated = 0

    for index, descriptor in enumerate(descriptors):
        if not descriptor.get("visible"):
            skipped_hidden += 1
            continue
        if shown >= max_elements:
            truncated += 1
            continue
        name = descriptor.get("name") or ""
        parts = [f"[e{index}]", descriptor.get("role") or "element",
                 f'"{name}"' if name else "(unnamed)"]
        notes = []
        if descriptor.get("editable"):
            value = descriptor.get("value") or ""
            notes.append(f'value="{value}"' if value else "empty")
        elif descriptor.get("value"):
            notes.append(f'value="{descriptor["value"]}"')
        if descriptor.get("disabled"):
            notes.append("disabled")
        if descriptor.get("checked"):
            notes.append("checked")
        if notes:
            parts.append("— " + ", ".join(notes))
        lines.append(" ".join(parts))
        shown += 1

    if not shown:
        lines.append("(no visible interactive elements)")
        lines.append("")
        lines.append(
            "The page may still be loading; or its controls may live in an "
            "iframe (this snapshot covers the main frame only) or in a "
            "closed shadow root, which nothing can reach. tab_read still "
            "returns the text."
        )
        return "\n".join(lines)

    footer = [f"{shown} visible element(s)"]
    if truncated:
        footer.append(f"{truncated} more not listed (cap {max_elements})")
    if skipped_hidden:
        footer.append(f"{skipped_hidden} hidden")
    lines.append("")
    lines.append("; ".join(footer) + ".")
    lines.append(
        "Act on these by ref, e.g. tab_act action=click ref=e7. Refs are "
        "valid until the page changes; re-snapshot after a navigation."
    )
    return "\n".join(lines)


def mismatch_message(handles: int, descriptions: int) -> str:
    """What to say if the one-walk invariant is ever broken.

    A module-level function so a test can assert on the exact words. The
    words matter: the message it replaced said "the page changed ... call
    tab_snapshot again", which was both a wrong diagnosis and an instruction
    to repeat a deterministic failure — the loop guard had to stop the agent
    twice in live sessions. It names a way forward instead.
    """
    return (f"Error: tab_snapshot could not read the page consistently "
            f"({handles} handles vs {descriptions} descriptions). This is a "
            f"bug in tab_snapshot rather than a problem with the page, and "
            f"repeating the call will not clear it. Use tab_read to work "
            f"with the page text instead.")


async def _snapshot(cdp_url: str) -> str:
    page = await _ensure_page(cdp_url)

    # One walk. `get_properties()` hands back a JSHandle per array slot in
    # order, and `array.evaluate` describes that same array — so the handles
    # and the descriptions cannot be lists of different things.
    array = await page.evaluate_handle(_WALK_JS, INTERACTIVE_SELECTOR)
    slots = await array.get_properties()
    handles = [slot.as_element() for slot in slots.values()]
    descriptors = await array.evaluate(_DESCRIBE_JS)

    if len(handles) != len(descriptors):
        # Now unreachable by construction, and kept precisely because it is:
        # if it ever fires again, the one-walk invariant has been broken by a
        # later edit and a ref would address the wrong element — the single
        # failure this tool must never produce silently. It does NOT advise a
        # retry: the mismatch it used to report was deterministic, so telling
        # the model to call again produced a loop that only the loop guard
        # could stop.
        return mismatch_message(len(handles), len(descriptors))

    _STATE.refs = {f"e{i}": handle for i, handle in enumerate(handles)}
    _STATE.refs_url = page.url
    _STATE.refs_taken_at = time.time()
    return format_snapshot(descriptors, await page.title(), page.url)


# ---------------------------------------------------------------------------
# The operations, as coroutines
# ---------------------------------------------------------------------------

async def _tabs(cdp_url: str, select: Optional[int], new_tab: Optional[str]) -> str:
    await _connect(cdp_url)

    if new_tab:
        context = (_STATE.browser.contexts[0] if _STATE.browser.contexts
                   else await _STATE.browser.new_context())
        page = await context.new_page()
        await page.goto(new_tab, wait_until="domcontentloaded",
                        timeout=NAVIGATION_TIMEOUT_MS)
        _STATE.page = page
        _invalidate_refs()
        return f"Opened a new tab and attached to it: {await page.title()} — {page.url}"

    pages = _pages()
    if not pages:
        return "The browser is running but has no open tab."

    if select is not None:
        if not 0 <= select < len(pages):
            return (f"Error: no tab {select}. There are {len(pages)}, "
                    f"numbered 0..{len(pages) - 1}.")
        _STATE.page = pages[select]
        _invalidate_refs()
        return (f"Attached to tab {select}: {await _STATE.page.title()} — "
                f"{_STATE.page.url}")

    attached = await _ensure_page(cdp_url)
    lines = ["Open tabs (the attached one is marked ▸):", ""]
    for index, page in enumerate(pages):
        marker = "▸" if page is attached else " "
        try:
            title = await page.title()
        except Exception:
            title = "(unreadable)"
        lines.append(f"{marker} [{index}] {title}")
        lines.append(f"      {page.url}")
    lines.append("")
    lines.append("Switch with tab_list select=<n>.")
    return "\n".join(lines)


async def _read(cdp_url: str, ref: Optional[str], max_chars: int) -> str:
    page = await _ensure_page(cdp_url)
    if ref:
        handle = _STATE.refs.get(ref)
        if handle is None:
            return _unknown_ref(ref)
        text = await handle.inner_text()
        origin = f"{ref} on {page.url}"
    else:
        text = await page.evaluate(
            "() => document.body ? document.body.innerText : ''")
        origin = page.url

    text = re.sub(r"\n{3,}", "\n\n", (text or "").strip())
    header = f"{await page.title()} — {origin}\n\n"
    if len(text) > max_chars:
        text = (text[:max_chars]
                + f"\n\n[clipped at {max_chars} chars of {len(text)}]")
    return header + (text or "(no text)")


async def _navigate(cdp_url: str, url: Optional[str], action: str) -> str:
    page = await _ensure_page(cdp_url)
    before = page.url
    if url:
        await page.goto(url, wait_until="domcontentloaded",
                        timeout=NAVIGATION_TIMEOUT_MS)
    elif action == "back":
        await page.go_back(wait_until="domcontentloaded",
                           timeout=NAVIGATION_TIMEOUT_MS)
    elif action == "forward":
        await page.go_forward(wait_until="domcontentloaded",
                              timeout=NAVIGATION_TIMEOUT_MS)
    elif action == "reload":
        await page.reload(wait_until="domcontentloaded",
                          timeout=NAVIGATION_TIMEOUT_MS)
    else:
        return ("Error: give a url, or action=back|forward|reload.")

    _invalidate_refs()
    return (f"{before}\n  → {page.url}\n{await page.title()}\n\n"
            f"Refs from any earlier snapshot are void; call tab_snapshot.")


def _unknown_ref(ref: str) -> str:
    if not _STATE.refs:
        return (f"Error: no snapshot has been taken, so '{ref}' addresses "
                f"nothing. Call tab_snapshot first.")
    return (f"Error: unknown ref '{ref}'. The last snapshot has "
            f"{len(_STATE.refs)} elements (e0..e{len(_STATE.refs) - 1}); "
            f"call tab_snapshot again if the page has changed.")


async def _act(cdp_url: str, action: str, ref: Optional[str],
               text: Optional[str], key: Optional[str],
               option: Optional[str], submit: bool, clear_first: bool) -> str:
    page = await _ensure_page(cdp_url)

    if action not in ACTIONS:
        return f"Error: unknown action '{action}'. Use one of: {', '.join(ACTIONS)}."

    handle = None
    if ref:
        handle = _STATE.refs.get(ref)
        if handle is None:
            return _unknown_ref(ref)
        if _STATE.refs_url and _STATE.refs_url != page.url:
            return (f"Error: the snapshot was taken on {_STATE.refs_url} and "
                    f"the tab is now on {page.url}. Call tab_snapshot "
                    f"again before acting.")
    elif action not in ("press", "scroll"):
        return f"Error: action '{action}' needs a ref from tab_snapshot."

    before_url = page.url
    described = f"{action} {ref}" if ref else action

    try:
        if handle is not None and action != "scroll":
            await handle.scroll_into_view_if_needed(timeout=ACTION_TIMEOUT_MS)

        if action == "click":
            await handle.click(timeout=ACTION_TIMEOUT_MS)
        elif action == "double_click":
            await handle.dblclick(timeout=ACTION_TIMEOUT_MS)
        elif action == "type":
            if text is None:
                return "Error: action=type needs text."
            if clear_first:
                await handle.fill(text, timeout=ACTION_TIMEOUT_MS)
            else:
                await handle.type(text, timeout=ACTION_TIMEOUT_MS)
            if submit:
                await handle.press("Enter", timeout=ACTION_TIMEOUT_MS)
                described += " + Enter"
        elif action == "clear":
            await handle.fill("", timeout=ACTION_TIMEOUT_MS)
        elif action == "press":
            if not key:
                return "Error: action=press needs a key, e.g. Enter or Control+A."
            if handle is not None:
                await handle.press(key, timeout=ACTION_TIMEOUT_MS)
            else:
                await page.keyboard.press(key)
            described += f" {key}"
        elif action == "select":
            if option is None:
                return "Error: action=select needs an option (its label or value)."
            try:
                await handle.select_option(label=option, timeout=ACTION_TIMEOUT_MS)
            except Exception:
                await handle.select_option(value=option, timeout=ACTION_TIMEOUT_MS)
            described += f" {option!r}"
        elif action == "hover":
            await handle.hover(timeout=ACTION_TIMEOUT_MS)
        elif action == "check":
            await handle.check(timeout=ACTION_TIMEOUT_MS)
        elif action == "uncheck":
            await handle.uncheck(timeout=ACTION_TIMEOUT_MS)
        elif action == "scroll":
            if handle is not None:
                await handle.scroll_into_view_if_needed(timeout=ACTION_TIMEOUT_MS)
            else:
                amount = int(text) if (text or "").lstrip("-").isdigit() else 600
                await page.mouse.wheel(0, amount)
                described += f" {amount}px"
    except Exception as exc:
        message = str(exc).split("\n")[0]
        if "not attached" in message or "detached" in message.lower():
            _invalidate_refs()
            return (f"Error: '{ref}' no longer exists — the page rewrote that "
                    f"part of itself. Call tab_snapshot and try again.")
        return f"Error: {described} failed: {message}"

    # Settle briefly so a click that navigates reports where it landed rather
    # than the page it left.
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=3_000)
    except Exception:
        pass

    result = [f"Did: {described}."]
    if page.url != before_url:
        _invalidate_refs()
        result.append(f"The tab navigated: {before_url} → {page.url}")
        result.append("Earlier refs are void; call tab_snapshot.")
    else:
        result.append(f"Still on {page.url}. Refs from the last snapshot may "
                      f"be stale if the page updated — re-snapshot to be sure.")
    return "\n".join(result)


async def _screenshot(cdp_url: str, path: Optional[str], full_page: bool) -> str:
    page = await _ensure_page(cdp_url)
    if path:
        target = Path(path).expanduser()
    else:
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        target = SCREENSHOT_DIR / f"tab-{time.strftime('%Y%m%d-%H%M%S')}.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    await page.screenshot(path=str(target), full_page=full_page)
    size = target.stat().st_size if target.exists() else 0
    return (f"Saved a {'full-page' if full_page else 'viewport'} screenshot of "
            f"{page.url}\n{target} ({size:,} bytes)\n"
            f"Read it with read_file to look at it.")


# ---------------------------------------------------------------------------
# The synchronous surface the tool handlers call
# ---------------------------------------------------------------------------

def _run(coro, interrupt: Optional[Callable[[], bool]],
         timeout: float = CALL_TIMEOUT_S) -> str:
    """Submit one coroutine and turn every failure into a readable line.

    Every browser tool ends here, so the failure vocabulary is defined once:
    no browser, interrupted, timed out, or the Playwright error's first line —
    the rest of a Playwright traceback is its own call log, which is noise to
    a model deciding what to do next.
    """
    try:
        return _LOOP.submit(coro, timeout=timeout, interrupt=interrupt)
    except BrowserInterrupted:
        return ("[interrupted] The browser was still working when Esc was "
                "pressed. The page keeps whatever state it reached; this "
                "result was not collected.")
    except TimeoutError as exc:
        return f"Error: {exc}"
    except Exception as exc:
        message = str(exc).split("\n")[0] or exc.__class__.__name__
        if ("connect_over_cdp" in message or "ECONNREFUSED" in message
                or "WebSocket" in message):
            return not_connected_message()
        return f"Error: {message}"


def _preflight() -> Optional[str]:
    """The two things that are wrong before any call is worth making."""
    import importlib.util
    if importlib.util.find_spec("playwright") is None:
        return ("Error: Playwright is not installed. "
                "pip install playwright && playwright install chromium")
    if _STATE.browser is None and cdp_version() is None:
        return not_connected_message()
    return None


def tabs(select: Optional[int] = None, new_tab: Optional[str] = None,
         start_browser: bool = False,
         interrupt: Optional[Callable[[], bool]] = None,
         cdp_url: str = "") -> str:
    if start_browser:
        started = start_browser_and_report()
        if started.startswith("Error"):
            return started
        prefix = started + "\n\n"
    else:
        prefix = ""
    problem = _preflight()
    if problem:
        return prefix + problem
    return prefix + _run(_tabs(_normalise_cdp_url(cdp_url), select, new_tab),
                         interrupt)


def start_browser_and_report() -> str:
    """Named apart from `start_browser` so the tool argument can keep its
    name without shadowing the function it triggers."""
    return start_browser()


def snapshot(interrupt: Optional[Callable[[], bool]] = None,
             cdp_url: str = "") -> str:
    problem = _preflight()
    if problem:
        return problem
    return _run(_snapshot(_normalise_cdp_url(cdp_url)), interrupt)


def read(ref: Optional[str] = None, max_chars: int = MAX_READ_CHARS,
         interrupt: Optional[Callable[[], bool]] = None,
         cdp_url: str = "") -> str:
    problem = _preflight()
    if problem:
        return problem
    return _run(_read(_normalise_cdp_url(cdp_url), ref, max_chars), interrupt)


def navigate(url: Optional[str] = None, action: str = "",
             interrupt: Optional[Callable[[], bool]] = None,
             cdp_url: str = "") -> str:
    problem = _preflight()
    if problem:
        return problem
    return _run(_navigate(_normalise_cdp_url(cdp_url), url, action), interrupt,
                timeout=CALL_TIMEOUT_S + 15)


def act(action: str, ref: Optional[str] = None, text: Optional[str] = None,
        key: Optional[str] = None, option: Optional[str] = None,
        submit: bool = False, clear_first: bool = True,
        interrupt: Optional[Callable[[], bool]] = None,
        cdp_url: str = "") -> str:
    problem = _preflight()
    if problem:
        return problem
    return _run(_act(_normalise_cdp_url(cdp_url), action, ref, text, key,
                     option, submit, clear_first), interrupt)


def screenshot(path: Optional[str] = None, full_page: bool = False,
               interrupt: Optional[Callable[[], bool]] = None,
               cdp_url: str = "") -> str:
    problem = _preflight()
    if problem:
        return problem
    return _run(_screenshot(_normalise_cdp_url(cdp_url), path, full_page),
                interrupt)


def shutdown() -> None:
    """Release the Playwright driver without touching the user's browser.

    Only the *connection* is closed. `connect_over_cdp` attached to a process
    this agent does not own, and closing tabs the user opened would be a side
    effect nobody asked for. Registered at exit because the driver is a node
    subprocess, which a daemon thread will not clean up on its own.
    """
    if _STATE.playwright is None:
        return

    async def _stop() -> None:
        try:
            if _STATE.browser is not None:
                await _STATE.browser.close()
        except Exception:
            pass
        try:
            await _STATE.playwright.stop()
        except Exception:
            pass

    try:
        _LOOP.submit(_stop(), timeout=5.0)
    except Exception:
        pass
    _STATE.playwright = None
    _STATE.browser = None
    _STATE.page = None
    _invalidate_refs()


atexit.register(shutdown)
