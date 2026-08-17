"""When TOMAS may offer to learn about the user, and when it must stop asking.

A fresh install knows nothing: `~/.tomas/learned/global/facts.jsonl` starts
empty, the instructions directory holds only the shipped defaults, and the
reflection pass that would eventually notice a pattern is a manual command.
So the first sessions are the ones where an offer is worth most and costs
least — and also the only ones where it is welcome.

The rule this module owns is the second half of that:

    offer during the first `PROPOSE_UNTIL_SESSION` sessions, once each,
    and never again.

Not "until dismissed" and not "occasionally" — a fixed, small number of
chances, after which `/setup` is the only way in. An assistant that keeps
suggesting its own onboarding is one the user learns to ignore, and the
ignoring generalises to every other notice it prints.

State is a single small file so the count survives restarts and, unlike a
count of session transcripts, is not reset by clearing history.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

STATE_PATH = Path.home() / ".tomas" / "onboarding.json"

#: How many sessions may carry an offer. Five is the number the user asked
#: for. It matters mainly that it is small and fixed.
PROPOSE_UNTIL_SESSION = 5


def _load() -> dict:
    try:
        with STATE_PATH.open(encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(state: dict) -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with STATE_PATH.open("w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=1)
    except OSError:
        # Losing the counter costs one extra offer, which is a far smaller
        # failure than refusing to start because a state file is unwritable.
        pass


def state() -> dict:
    """The stored record, with every field present."""
    s = _load()
    return {
        "sessions_seen": int(s.get("sessions_seen") or 0),
        "completed": bool(s.get("completed")),
        "completed_at": s.get("completed_at"),
        "last_offered_session": int(s.get("last_offered_session") or 0),
        "declined": int(s.get("declined") or 0),
    }


def note_session_start() -> int:
    """Count this session. Returns its number, 1-based."""
    s = state()
    s["sessions_seen"] += 1
    _save(s)
    return s["sessions_seen"]


def should_offer() -> bool:
    """May this session offer onboarding?

    Three ways to answer no, and they are deliberately different questions:
    the user has done it (`completed`), the window has passed
    (`sessions_seen`), or this session has already asked
    (`last_offered_session`). Only the middle one is about time — the other
    two are about not repeating oneself, which is what makes a notice worth
    reading when it does appear.
    """
    s = state()
    if s["completed"]:
        return False
    if s["sessions_seen"] > PROPOSE_UNTIL_SESSION:
        return False
    return s["last_offered_session"] < s["sessions_seen"]


def note_offered() -> None:
    """Record that this session has already made its offer."""
    s = state()
    s["last_offered_session"] = s["sessions_seen"]
    _save(s)


def note_declined() -> None:
    """The user said no. Counted, but it does not shorten the window.

    Declining once is not the same as opting out — someone busy on session 1
    may well want this on session 3. Opting out is what running out of
    sessions does, silently and on schedule.
    """
    s = state()
    s["declined"] += 1
    s["last_offered_session"] = s["sessions_seen"]
    _save(s)


def mark_completed() -> None:
    """Onboarding ran. Nothing offers it again; `/setup` still works."""
    s = state()
    s["completed"] = True
    s["completed_at"] = time.time()
    _save(s)


def offer_text() -> str:
    """The one-line offer. Deliberately a line, not a wizard.

    A modal setup flow in front of the first prompt is how a tool gets closed
    before it is used. This states what is missing and how to fix it, and
    then gets out of the way.
    """
    s = state()
    left = PROPOSE_UNTIL_SESSION - s["sessions_seen"] + 1
    tail = ("this is the last time I'll offer" if left <= 1
            else f"I'll stop offering after {left} more session"
                 f"{'s' if left > 2 else ''}")
    return (f"TOMAS does not know you yet — /setup takes a couple of minutes "
            f"and tunes replies, language and defaults to you ({tail}).")
