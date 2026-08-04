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
    ToolFinished,
    ToolResultTruncated,
    ToolStarted,
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
    key = _HEADLINE_ARG.get(name)
    if key and key in args:
        head = str(args[key])
        extra = len(args) - 1
        suffix = f" (+{extra})" if extra > 0 else ""
        return shorten(head.replace("\n", " "), max(20, term_width() - 40)) + suffix
    # Unknown tool (usually MCP): show the arguments, unescaped.
    rendered = json.dumps(args, default=str, ensure_ascii=False)
    return shorten(rendered, max(20, term_width() - 40))


class Thinking:
    """A one-line 'working' indicator for the wait before the first token.

    Between sending a request and the first delta there is nothing on screen —
    several seconds of apparently-dead terminal that reads as a hang. This
    occupies exactly one line, erases itself completely, and never runs when
    output is not a terminal (so transcripts and tests stay clean).
    """

    FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, label: str = "thinking"):
        self.label = label
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

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
            frame = self.FRAMES[i % len(self.FRAMES)]
            secs = time.monotonic() - started
            elapsed = f" {secs:.0f}s" if secs >= 3 else ""
            try:
                sys.stdout.write(f"\r\033[2K  {DIM}{frame} {self.label}{elapsed}{RESET}")
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
        self._thinking = Thinking()
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
            self._thinking = Thinking()
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

        elif isinstance(event, StreamingDisabled):
            print(f'\n  {YELLOW}⚠{RESET} {DIM}streaming unavailable on this provider '
                  f'— continuing without it{RESET}')

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

        risk_colors = {"low": GREEN, "medium": YELLOW, "high": RED}
        risk_color = risk_colors.get(event.risk, RED)
        print(f'\n  {risk_color}{BOLD}⚠ Permission ({event.risk.upper()} risk){RESET}')
        print(f'  {DIM}Tool:{RESET} {BOLD}{event.name}{RESET}')
        for k, v in (event.args or {}).items():
            print(f'  {DIM}{k}:{RESET} {shorten(str(v), term_width() - 6)}')
        try:
            resp = input(
                f'  {YELLOW}Allow?{RESET} [y/N/always for this exact call]: '
            ).strip().lower()
        except EOFError:
            return "deny"
        if resp == "always":
            return "always_allow_this_call"
        return "allow" if resp == "y" else "deny"

    def ask_continue(self, event) -> bool:
        """The turn used its budget. Ask rather than abandon the task."""
        self._stop_thinking()
        if not self.interactive:
            print(f'  {DIM}non-interactive — continuing automatically{RESET}')
            return True
        try:
            resp = input(
                f'  {YELLOW}Continue working on this task?{RESET} [Y/n]: '
            ).strip().lower()
        except EOFError:
            return False
        return resp in ("", "y", "yes")
