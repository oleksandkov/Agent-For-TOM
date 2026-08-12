"""Context-window accounting and compaction policy.

Pure by construction: no I/O, no printing, no provider lookups. The host
resolves *what* the window is and passes the number in; this module decides
what to do with it. That split is the point — compaction used to be decided
inside the REPL, so a front end that drove `core.loop.run_turn` directly (the
simulation harness, any future adapter) never compacted at all, and the cost
of a session could not be measured by anything that was not the REPL.

The same reasoning `needs_continuation_approval()` is built on: policy the
core owns is testable without a front end and cannot be got wrong per-adapter.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

#: Where a window size came from, most trustworthy first. Kept as data rather
#: than a bool because "we measured 200,000" and "we know nothing and assumed
#: 200,000" are different facts and the user is entitled to tell them apart.
WINDOW_SOURCES = ("override", "probed", "known", "default")

#: Shown when the number is a guess rather than a measurement.
UNTRUSTED_SOURCE = "default"


@dataclass(frozen=True)
class ContextWindow:
    """A context window size together with the provenance of the number."""

    tokens: int
    source: str
    model: str = ""

    @property
    def trusted(self) -> bool:
        """False when nothing was measured or known and a default was used."""
        return self.source != UNTRUSTED_SOURCE

    def describe(self) -> str:
        """`"1,000,000 tokens (probed)"` — for status lines and logs."""
        return f"{self.tokens:,} tokens ({self.source})"


# ── Compaction policy ──────────────────────────────────────────────────
# Two separate questions used to share one number:
#
#   "does this still fit?"      — a property of the model's window
#   "is it worth paying for?"   — a cost policy, unrelated to the window
#
# `min(window * 0.75, 120_000)` answered both with the smaller, so a model with
# a 1,000,000-token window compacted as if it had 120,000: the window the user
# chose the model for was discarded silently. Keeping them apart lets the cost
# rule scale with the window instead of capping it.

#: Compact once the history exceeds this fraction of the window. This is the
#: "does it fit" rule, and on a small window it is the binding one.
DEFAULT_FIT_FRACTION = 0.75

#: Default cost ceiling, as a fraction of the real window. Re-reading history
#: is paid on every turn, so past roughly a quarter of the window the early
#: part costs more than it is worth — but a quarter *of the actual window*,
#: not a flat number that ignores it.
DEFAULT_COST_FRACTION = 0.25

#: …never below this, or a small-window model would compact almost every turn.
#: Together with the fraction this reproduces the old flat 120,000 exactly for
#: every window up to 480,000, and only changes behaviour above it.
COST_FLOOR = 120_000

#: Passed as the cost limit to disable the cost rule entirely, leaving only
#: the fit rule. `AGENT_COMPACT_AT=0` selects this.
COST_LIMIT_DISABLED = 0


def default_cost_limit(window_tokens: int,
                       fraction: float = DEFAULT_COST_FRACTION,
                       floor: int = COST_FLOOR) -> int:
    """The cost ceiling to use when the user has not set one."""
    if window_tokens <= 0:
        return floor
    return max(floor, int(window_tokens * fraction))


@dataclass(frozen=True)
class CompactionPlan:
    """Whether to compact, and the arithmetic that decided it."""

    needed: bool
    used: int
    trigger: int
    #: "fits" when nothing is needed; otherwise which rule bound —
    #: "window" (it would not fit) or "cost" (it fits but is not worth it).
    reason: str
    window: int = 0

    @property
    def headroom(self) -> int:
        """Tokens left before compaction fires. Negative once it has."""
        return self.trigger - self.used


def compaction_plan(used_tokens: int,
                    window_tokens: int,
                    reserve_tokens: int = 0,
                    fit_fraction: float = DEFAULT_FIT_FRACTION,
                    cost_limit: Optional[int] = None) -> CompactionPlan:
    """Decide whether the conversation should be compacted.

    `used_tokens` is the transcript; `reserve_tokens` is everything else that
    has to fit alongside it in the same request (system prompt, tool schemas,
    the output budget). `cost_limit` of None derives the default from the
    window; 0 disables the cost rule and leaves only the fit rule.
    """
    fit_trigger = int(window_tokens * fit_fraction)
    if cost_limit is None:
        cost_limit = default_cost_limit(window_tokens)

    if cost_limit > COST_LIMIT_DISABLED and cost_limit < fit_trigger:
        trigger, bound = cost_limit, "cost"
    else:
        trigger, bound = fit_trigger, "window"

    total = used_tokens + reserve_tokens
    needed = total >= trigger
    return CompactionPlan(
        needed=needed,
        used=total,
        trigger=trigger,
        reason=bound if needed else "fits",
        window=window_tokens,
    )
