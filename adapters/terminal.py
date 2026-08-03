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

from .ansi import BOLD, DIM, GREEN, MAGENTA, RED, RESET, YELLOW


class TerminalAdapter:
    """Drives a run_turn generator, printing as it goes."""

    def __init__(self, interactive: bool = True):
        self.interactive = interactive
        self._streaming_header_shown = False

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
        for event in gen:
            self.render(event)
            if isinstance(event, TurnFinished):
                reply = event.reply
        return reply

    # ── Rendering ──────────────────────────────────────────────────

    def render(self, event) -> None:
        if isinstance(event, TextDelta):
            if not self._streaming_header_shown:
                print(f'  {MAGENTA}{BOLD}▌ TOMAS{RESET}')
                print('  ', end='', flush=True)
                self._streaming_header_shown = True
            sys.stdout.write(event.text)
            sys.stdout.flush()

        elif isinstance(event, AssistantMessage):
            self._end_stream_line()
            print(f'  {MAGENTA}{BOLD}▌ TOMAS{RESET}')
            print(f'  {event.text}')

        elif isinstance(event, TurnFinished):
            self._end_stream_line()

        elif isinstance(event, ToolStarted):
            self._end_stream_line()
            args_str = json.dumps(event.args, default=str)[:120]
            origin = f'{DIM}[{event.origin}]{RESET}' if event.origin else ''
            print(f'    {YELLOW}⚡{RESET} {BOLD}{event.name}{RESET} '
                  f'{origin}({DIM}{args_str}...{RESET})')

        elif isinstance(event, ToolFinished):
            result = event.result
            if isinstance(result, str) and result.strip():
                lines = result.strip().splitlines()
                shown = lines[0][:160] if lines else ""
                if len(lines) > 1 or len(result) > 160:
                    shown += f' {DIM}…{RESET}'
                mark = f'{GREEN}↳{RESET}' if event.ok else f'{RED}↳{RESET}'
                print(f'    {mark} {DIM}{shown}{RESET}')

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
            print()
            self._streaming_header_shown = False

    # ── Answering the core's questions ─────────────────────────────

    def ask(self, event) -> Decision:
        risk_colors = {"low": GREEN, "medium": YELLOW, "high": RED}
        risk_color = risk_colors.get(event.risk, RED)
        print(f'\n  {risk_color}{BOLD}⚠ Permission ({event.risk.upper()} risk){RESET}')
        print(f'  {DIM}Tool:{RESET} {BOLD}{event.name}{RESET}')
        for k, v in (event.args or {}).items():
            display = str(v)[:200]
            if len(str(v)) > 200:
                display += "..."
            print(f'  {DIM}{k}:{RESET} {display}')
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
