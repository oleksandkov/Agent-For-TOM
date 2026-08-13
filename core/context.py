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

#: What a user may set the fit rule to, as a percentage of the window.
#: Bounded at both ends on purpose. Below the floor the agent compacts so often
#: it never accumulates the context that makes it useful; above the ceiling
#: there is no room left for the reply that compaction is supposed to protect,
#: so the first turn past the line fails outright instead of being summarised.
MIN_FIT_PERCENT = 40
MAX_FIT_PERCENT = 95

#: `compact_at_percent = 0` means "never automatically" — the transcript grows
#: until the user runs /compact. Legitimate, and not the same as a very high
#: threshold: a user who has turned it off wants no model call spent on
#: summarising without being asked, at any size.
AUTO_COMPACTION_OFF = 0


def fit_fraction_from_percent(percent: Optional[int]) -> Optional[float]:
    """Turn a stored percentage into a fraction, or None for "never".

    None in, default out — an unset setting is not the same as a disabled one,
    and conflating them is how "off" ends up meaning "75%".
    """
    if percent is None:
        return DEFAULT_FIT_FRACTION
    if percent <= AUTO_COMPACTION_OFF:
        return None
    bounded = max(MIN_FIT_PERCENT, min(MAX_FIT_PERCENT, int(percent)))
    return bounded / 100.0

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

#: What a compacted transcript still costs: the summary, the two framing
#: messages around it, and the last four turns kept verbatim (see
#: `agent.maybe_compact`). Compaction cannot go below this, which is the fact
#: the old rule ignored.
POST_COMPACTION_FLOOR = 2_000


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
    #: "window" (it would not fit), "cost" (it fits but is not worth it),
    #: "overhead" (it does not fit and compacting cannot change that), or
    #: "disabled" (the user turned automatic compaction off).
    reason: str
    window: int = 0
    #: System prompt + tool schemas + output budget. Carried so a caller can
    #: tell "the conversation grew" from "the conversation never had room".
    reserve: int = 0

    @property
    def headroom(self) -> int:
        """Tokens left before compaction fires. Negative once it has."""
        return self.trigger - self.used

    @property
    def transcript(self) -> int:
        """The part of `used` that compaction can actually shrink."""
        return max(0, self.used - self.reserve)

    @property
    def can_help(self) -> bool:
        """Whether compacting could bring the request under the trigger.

        Compaction only ever shrinks the transcript, and it cannot shrink it
        below `POST_COMPACTION_FLOOR`. So once the fixed overhead is itself
        within that distance of the trigger, every compaction is a wasted
        model call: it summarises, the total barely moves, and the next turn
        fires again. Measured on a 32,768-token model carrying 64 MCP tools —
        29,625 of fixed cost against a 24,576 trigger — this fired on the
        first message, with a five-character transcript, and on every message
        after it. The user paid two full local inferences per turn to compact
        a conversation that was never the problem.
        """
        return self.reserve + POST_COMPACTION_FLOOR < self.trigger


def compaction_plan(used_tokens: int,
                    window_tokens: int,
                    reserve_tokens: int = 0,
                    fit_fraction: Optional[float] = DEFAULT_FIT_FRACTION,
                    cost_limit: Optional[int] = None) -> CompactionPlan:
    """Decide whether the conversation should be compacted.

    `used_tokens` is the transcript; `reserve_tokens` is everything else that
    has to fit alongside it in the same request (system prompt, tool schemas,
    the output budget). `cost_limit` of None derives the default from the
    window; 0 disables the cost rule and leaves only the fit rule.

    `fit_fraction` of None turns automatic compaction off entirely — both
    rules, not just the fit one. A user who has said "do not compact without
    asking me" has not asked for the cost rule to keep doing it, and leaving
    that one live is how "off" would quietly mean "less often". The plan still
    reports `used` and `trigger` so the status line can show how full the
    window is; it simply never sets `needed`.
    """
    if fit_fraction is None:
        total = used_tokens + reserve_tokens
        return CompactionPlan(
            needed=False, used=total,
            trigger=int(window_tokens * DEFAULT_FIT_FRACTION),
            reason="disabled", window=window_tokens, reserve=reserve_tokens)

    fit_trigger = int(window_tokens * fit_fraction)
    if cost_limit is None:
        cost_limit = default_cost_limit(window_tokens)

    if cost_limit > COST_LIMIT_DISABLED and cost_limit < fit_trigger:
        trigger, bound = cost_limit, "cost"
    else:
        trigger, bound = fit_trigger, "window"

    total = used_tokens + reserve_tokens
    over = total >= trigger
    # Two questions again, and the second one is new: *would compacting help?*
    # The rule used to stop at "is it over the line", which is only half a
    # decision — a request can be over the line for a reason compaction cannot
    # touch. See `CompactionPlan.can_help`.
    room_to_gain = reserve_tokens + POST_COMPACTION_FLOOR < trigger
    if over and not room_to_gain:
        reason = "overhead"
        needed = False
    else:
        reason = bound if over else "fits"
        needed = over
    return CompactionPlan(
        needed=needed,
        used=total,
        trigger=trigger,
        reason=reason,
        window=window_tokens,
        reserve=reserve_tokens,
    )
