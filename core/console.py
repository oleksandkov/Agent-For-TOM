"""Who owns the keyboard, and when.

The terminal has exactly one input queue. Two readers on it is not a race that
sometimes goes wrong — it is a guarantee that keystrokes are lost, because
`msvcrt.getwch()` removes a character from the queue whichever thread calls it.

Measured on a live session, `ask_user_question` running under the adapter's
`Thinking` spinner: every 80ms the spinner thread called `_poll_esc()`
(`kbhit()` + `getwch()`) and then wrote `\\r\\033[2K…` over the line the user
was typing into. The user could not see what they typed, arrow keys dropped
half their two-byte sequence so the menu selection "disappeared", and an Esc
caught by the poller ended the whole turn instead of skipping the question.

The rule this module makes structural was already written down and already
honoured everywhere else — `TerminalAdapter.ask` stops the spinner before a
permission prompt, with the comment "never prompt underneath a spinner". The
one path that escaped it did so because it prompts from *inside* a tool, where
the adapter cannot see it. Hence both halves below: the core tells the adapter
which tools read the console, and a lock stops anything else reaching for it
regardless.

Lives in `core/` because `adapters/*` imports `core`, never `agent` — this is
the only place both sides can see.
"""
from __future__ import annotations

import threading

#: Tools that read the console themselves. An adapter must not draw a spinner
#: over these, and must not poll the keyboard while one is running.
#:
#: An allowlist rather than a risk tier, for the same reason
#: `PARALLEL_SAFE_TOOLS` is one: "may this be auto-approved?" and "does this
#: read stdin?" are different questions, and answering the second from the
#: first is how the spinner came to run over an interactive prompt.
INTERACTIVE_TOOLS = frozenset({"ask_user_question"})

#: Held by whoever is reading the console. Re-entrant: the pickers nest
#: (`ask_user_question` -> `_arrow_menu` -> the custom-answer editor) and each
#: layer takes it, on the same thread.
#:
#: Background readers must take it with `blocking=False` and give up when they
#: cannot: an Esc poller that *waits* for the menu to finish is a poller that
#: then reads the user's next keystroke, which is the same bug one turn later.
CONSOLE = threading.RLock()


def is_interactive_tool(name: str) -> bool:
    return name in INTERACTIVE_TOOLS


def console_is_busy() -> bool:
    """True when something is reading the console right now.

    For background threads deciding whether to touch the keyboard *or the
    screen*: the spinner's `\\r\\033[2K` is as destructive as a stolen
    keystroke, and both are avoided by the same check.
    """
    if CONSOLE.acquire(blocking=False):
        CONSOLE.release()
        return False
    return True
