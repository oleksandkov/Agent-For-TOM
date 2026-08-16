"""
Terminal adapter — renders core events as the ANSI chat UI, and answers the
core's questions with input().

Everything here used to live inside the agent loop as bare print() calls. The
output is deliberately identical: users should not be able to tell this phase
happened.
"""

from __future__ import annotations

import json
import sys
import threading
import time

# On a non-UTF-8 console codepage (cp1251/cp1252/cp437) the glyphs below raise
# UnicodeEncodeError. agent.py does this too at import, but the adapter must
# stand on its own: a front end that imports only this module still prints.
if sys.platform == "win32":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

from core.events import (
    AssistantMessage,
    ContinuationGranted,
    ContinuationNeeded,
    ErrorOccurred,
    LoopDetected,
    RetryScheduled,
    StreamingDisabled,
    TextDelta,
    AnnouncedWithoutActing,
    ToolCallsRecovered,
    ToolFinished,
    ToolResultTruncated,
    ToolStarted,
    TruncatedOutputDiscarded,
    TurnFinished,
)
from core.permissions import Decision
from text_display import (
    StreamWrap, display_width, shorten, strip_ansi, term_width, wrap,
)

from .ansi import BOLD, DIM, GREEN, MAGENTA, RED, RESET, YELLOW

# The argument worth showing for each built-in tool. A raw JSON dump is both
# unreadable and — before this phase — escaped: Cyrillic arguments rendered as
# `пр...`, and the 120-char cut landed mid-escape.
_HEADLINE_ARG = {
    "read_file": "file_path",
    "write_file": "file_path",
    "edit_file": "file_path",
    "list_files": "path",
    "run_command": "command",
    "search_code": "pattern",
    "save_memory": "key",
    "fetch_url": "url",
    "fetch_url_with_browser": "url",
    "search_web": "query",
    "read_mcp_resource": "uri",
}


def summarise_args(name: str, args: dict) -> str:
    """One readable line for a tool call, in the caller's own script."""
    if not args:
        return ""
    if name == "ask_user_question":
        # The full options/descriptions are about to be drawn by the
        # interactive picker itself; echoing the raw questions array here
        # (the generic JSON fallback below) would print the same content
        # twice, once as an unreadable escaped dump.
        qs = args.get("questions") or []
        n = len(qs)
        first = str((qs[0] or {}).get("question", "")) if qs else ""
        suffix = f" (+{n - 1} more)" if n > 1 else ""
        return shorten(first.replace("\n", " "), max(20, term_width() - 40)) + suffix
    key = _HEADLINE_ARG.get(name)
    if key and key in args:
        head = str(args[key])
        extra = len(args) - 1
        suffix = f" (+{extra})" if extra > 0 else ""
        return shorten(head.replace("\n", " "), max(20, term_width() - 40)) + suffix
    # Unknown tool (usually MCP): show the arguments, unescaped.
    rendered = json.dumps(args, default=str, ensure_ascii=False)
    return shorten(rendered, max(20, term_width() - 40))


def _poll_esc() -> bool:
    """True if Esc was just pressed, without blocking.

    Windows-only — matches the msvcrt-based input handling the rest of this
    codebase already uses (see the "Windows-only REPL" note in CLAUDE.md).
    Safe to call from a background thread: it only ever runs while the main
    thread is blocked inside a model/tool call, never while input() is also
    reading the console (every prompt stops the spinner first), so there is
    no concurrent reader to race with.
    """
    try:
        import msvcrt
    except ImportError:
        return False
    if not msvcrt.kbhit():
        return False
    ch = msvcrt.getwch()
    if ch == '\x1b':
        return True
    # Arrow/function keys arrive as a two-byte sequence ('\xe0' or '\x00'
    # then a scan code); swallow the second byte so it isn't left sitting in
    # the buffer to be misread as a stray character by the next real read.
    if ch in ('\xe0', '\x00') and msvcrt.kbhit():
        msvcrt.getwch()
    return False


class Thinking:
    """A one-line 'working' indicator for the wait before the first token.

    Between sending a request and the first delta there is nothing on screen —
    several seconds of apparently-dead terminal that reads as a hang. This
    occupies exactly one line, erases itself completely, and never runs when
    output is not a terminal (so transcripts and tests stay clean).

    Also doubles as the Esc-interrupt watcher: it is the one place already
    polling in a background thread throughout a turn, so a single Esc press
    is checked here rather than opening a second listener.
    """

    FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, label: str = "thinking",
                 interrupt: "threading.Event | None" = None):
        self.label = label
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._interrupt = interrupt

    def start(self) -> None:
        if self._thread is not None:
            return
        try:
            if not sys.stdout.isatty():
                return
        except Exception:
            return
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def _spin(self) -> None:
        i = 0
        started = time.monotonic()
        while not self._stop.wait(0.08):
            if self._interrupt is not None and _poll_esc():
                self._interrupt.set()
                try:
                    sys.stdout.write(f"\r\033[2K  {DIM}⎋ stopping…{RESET}")
                    sys.stdout.flush()
                except Exception:
                    pass
                return
            frame = self.FRAMES[i % len(self.FRAMES)]
            secs = time.monotonic() - started
            elapsed = f" {secs:.0f}s" if secs >= 3 else ""
            hint = "  (Esc to stop)" if self._interrupt is not None and secs >= 3 else ""
            try:
                sys.stdout.write(f"\r\033[2K  {DIM}{frame} {self.label}{elapsed}{hint}{RESET}")
                sys.stdout.flush()
            except Exception:
                return
            i += 1

    def stop(self) -> None:
        if self._thread is None:
            return
        self._stop.set()
        self._thread.join(timeout=0.3)
        self._thread = None
        try:
            sys.stdout.write("\r\033[2K")
            sys.stdout.flush()
        except Exception:
            pass


class TerminalAdapter:
    """Drives a run_turn generator, printing as it goes."""

    def __init__(self, interactive: bool = True):
        self.interactive = interactive
        self._streaming_header_shown = False
        self._non_interactive_notice_shown = False
        self._stream: StreamWrap | None = None
        self._thinking: Thinking | None = None
        # One press stops the turn — see Thinking, which polls for it, and
        # is_interrupted(), which core.state.AgentState.interrupted reads.
        self.esc_interrupt = threading.Event()

    def is_interrupted(self) -> bool:
        return self.esc_interrupt.is_set()

    # ── Driving ────────────────────────────────────────────────────

    def run(self, state, user_message: str | None = None) -> str:
        """Run a turn with this adapter as the state's responder.

        The preferred entry point: it wires the responder itself, so the
        permission prompts cannot silently be answered by something else.
        """
        from core.loop import run_turn

        state.responder = self
        return self.drive(run_turn(state, user_message))

    def drive(self, gen) -> str:
        """Render an already-built generator. Prefer run() — with drive() the
        caller is responsible for having set state.responder."""
        reply = ""
        # Started before the first `next()`, which is where the request is
        # actually sent and where all the waiting happens.
        self._thinking = Thinking(interrupt=self.esc_interrupt)
        self._thinking.start()
        try:
            for event in gen:
                self.render(event)
                if isinstance(event, TurnFinished):
                    reply = event.reply
        finally:
            self._stop_thinking()
        return reply

    def _stop_thinking(self) -> None:
        if self._thinking is not None:
            self._thinking.stop()
            self._thinking = None

    # ── Rendering ──────────────────────────────────────────────────

    def render(self, event) -> None:
        # Any output at all means the wait is over.
        if not isinstance(event, TextDelta) or not self._streaming_header_shown:
            self._stop_thinking()

        if isinstance(event, TextDelta):
            if not self._streaming_header_shown:
                print(f'  {MAGENTA}{BOLD}▌ TOMAS{RESET}')
                sys.stdout.write('  ')
                self._streaming_header_shown = True
                self._stream = StreamWrap(indent='  ')
            # Wrapped as it arrives, so a streamed reply is laid out exactly
            # like a non-streamed one instead of running off the right edge.
            sys.stdout.write(self._stream.feed(event.text))
            sys.stdout.flush()

        elif isinstance(event, AssistantMessage):
            self._end_stream_line()
            print(f'  {MAGENTA}{BOLD}▌ TOMAS{RESET}')
            print(wrap(event.text))

        elif isinstance(event, TurnFinished):
            self._end_stream_line()
            if event.interrupted:
                print(f'\n  {YELLOW}⎋{RESET}  Stopped ({event.seconds:.0f}s) — Esc was pressed.')

        elif isinstance(event, ToolStarted):
            self._end_stream_line()
            origin = f'{DIM}[{event.origin}]{RESET}' if event.origin else ''
            summary = summarise_args(event.name, event.args)
            width = term_width()
            # Right-align the origin when there is room, so the eye can scan
            # the tool column without the provenance getting in the way.
            head = f'    {YELLOW}⚡{RESET} {BOLD}{event.name}{RESET}'
            body = f'  {DIM}{summary}{RESET}' if summary else ''
            plain = display_width(f'    ⚡ {event.name}' + (f'  {summary}' if summary else ''))
            gap = width - plain - display_width(strip_ansi(origin))
            if origin and gap >= 2:
                print(f'{head}{body}{" " * gap}{origin}')
            else:
                print(f'{head}{body} {origin}'.rstrip())
            # The tool call itself is a blocking wait — a slow run_command
            # used to leave the screen dead between this line and the result,
            # which is exactly what read as "stuck".
            self._thinking = Thinking(label=event.name, interrupt=self.esc_interrupt)
            self._thinking.start()

        elif isinstance(event, ToolFinished):
            result = event.result
            if isinstance(result, str) and result.strip():
                lines = result.strip().splitlines()
                first = lines[0] if lines else ""
                room = term_width() - 8
                shown = shorten(first, room)
                if len(lines) > 1 and not shown.endswith("…"):
                    shown += ' …'
                mark = f'{GREEN}↳{RESET}' if event.ok else f'{RED}↳{RESET}'
                print(f'    {mark} {DIM}{shown}{RESET}')
            # The model is about to be called again with this result; that
            # wait is the same dead air the indicator exists for.
            self._thinking = Thinking(interrupt=self.esc_interrupt)
            self._thinking.start()

        elif isinstance(event, ToolResultTruncated):
            print(f'    {RED}⚠{RESET}  tool result truncated: '
                  f'{event.original_chars} chars → {event.kept_chars // 1000}K')

        elif isinstance(event, ContinuationNeeded):
            self._end_stream_line()
            print(f'\n  {YELLOW}⚠{RESET}  {event.calls_used} tool calls used in this turn.')

        elif isinstance(event, ContinuationGranted):
            print(f'  {GREEN}↳{RESET} {DIM}continuing (budget → {event.new_budget}){RESET}')

        elif isinstance(event, LoopDetected):
            self._end_stream_line()
            print(f'    {RED}⚠{RESET}  Stopping — {event.reason}.')

        elif isinstance(event, RetryScheduled):
            print(f'\n  {YELLOW}⚠{RESET} Transient error ({event.reason}) — retrying in '
                  f'{event.delay_s:g}s (attempt {event.attempt}/{event.max_attempts})...')

        elif isinstance(event, ToolCallsRecovered):
            # On the streamed path the JSON has already been printed token by
            # token, so this doubles as the explanation for it: what is on
            # screen was a tool call, and it is being run rather than shown.
            self._end_stream_line()
            names = ', '.join(event.names)
            print(f'  {YELLOW}↻{RESET} {DIM}the model wrote its tool call as text '
                  f'— recovered: {names}{RESET}')

        elif isinstance(event, AnnouncedWithoutActing):
            # The announcement is already on screen. Without this line the
            # user watches the model restate its plan and cannot tell that
            # the turn is still going.
            self._end_stream_line()
            print(f'  {YELLOW}↻{RESET} {DIM}the reply described the next step '
                  f'instead of taking it — asking for it once{RESET}')

        elif isinstance(event, StreamingDisabled):
            print(f'\n  {YELLOW}⚠{RESET} {DIM}streaming unavailable on this provider '
                  f'— continuing without it{RESET}')

        elif isinstance(event, TruncatedOutputDiscarded):
            # The discarded text is already on screen when streaming, so the
            # rule is drawn under it: everything above is superseded, and the
            # answer is about to start again rather than continue.
            self._end_stream_line()
            print(f'\n  {YELLOW}⚠{RESET}  {BOLD}The answer above was cut off at '
                  f'{event.previous_limit} output tokens and is being '
                  f'discarded.{RESET}')
            print(f'     {DIM}Retrying from the start with {event.new_limit} '
                  f'tokens — ignore the {event.discarded_chars} characters '
                  f'above.{RESET}')

        elif isinstance(event, ErrorOccurred):
            self._end_stream_line()
            print(f'\n  {RED}✗{RESET} {event.message}')

    def _end_stream_line(self) -> None:
        if self._streaming_header_shown:
            if self._stream is not None:
                sys.stdout.write(self._stream.flush())   # the last held word
                self._stream = None
            print()
            self._streaming_header_shown = False

    # ── Answering the core's questions ─────────────────────────────

    def ask(self, event) -> Decision:
        self._stop_thinking()   # never prompt underneath a spinner
        if not self.interactive:
            # Say it once, not once per call. Six silent denials in a row is
            # what made a turn look broken rather than unattended.
            if not self._non_interactive_notice_shown:
                self._non_interactive_notice_shown = True
                print(f'  {DIM}non-interactive — medium/high-risk tools are '
                      f'unavailable this run{RESET}')
            return "deny"

        # Same shape as `ask_user_question`: a marker line saying who is
        # asking and about what, the thing itself, then the choice. The two
        # prompts are the only places TOMAS stops and waits for a person, and
        # they used to look nothing like each other — one a bold warning
        # banner, the other a bare `input()` — so the fact that both mean
        # "you decide now" had to be learned twice.
        risk_colors = {"low": GREEN, "medium": YELLOW, "high": RED}
        risk_color = risk_colors.get(event.risk, RED)
        print(f'\n  {risk_color}{BOLD}▌{RESET} {risk_color}Permission '
              f'{DIM}· {event.risk} risk{RESET}')
        print(f'  {BOLD}{event.name}{RESET}')
        args = event.args or {}
        if args:
            width = max(len(str(k)) for k in args)
            for k, v in args.items():
                pad = " " * max(0, width - len(str(k)))
                print(f'  {DIM}{k}{pad}{RESET}  '
                      f'{shorten(str(v), term_width() - width - 8)}')
        print()
        try:
            resp = input(
                f'  {DIM}[y] allow  [n] deny  [a] always allow this exact '
                f'call{RESET}  '
            ).strip().lower()
        except EOFError:
            return "deny"
        # "always" stays accepted: it is what the prompt asked for until now,
        # and a user who types it should not silently get a denial.
        if resp in ("a", "always"):
            return "always_allow_this_call"
        return "allow" if resp in ("y", "yes") else "deny"

    def ask_continue(self, event) -> bool:
        """The turn used its budget. Ask rather than abandon the task."""
        self._stop_thinking()
        if not self.interactive:
            print(f'  {DIM}non-interactive — continuing automatically{RESET}')
            return True
        print(f'\n  {YELLOW}{BOLD}▌{RESET} {YELLOW}Budget reached{RESET}'
              f'  {DIM}· {event.calls_used} tool calls so far{RESET}')
        print(f'  {BOLD}Keep working on this task?{RESET}')
        print()
        try:
            resp = input(f'  {DIM}[Y] keep going  [n] stop and '
                         f'summarise{RESET}  ').strip().lower()
        except EOFError:
            return False
        return resp in ("", "y", "yes")
