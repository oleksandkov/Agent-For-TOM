"""Feature switches — what the agent does, as opposed to what it spends.

`core/budget.py` already owns "how much of the window may this occupy". This
module owns the separate question "is this behaviour on at all", and the two
are kept apart deliberately: a budget share is a number that scales with the
model, while a feature switch is a yes/no the user sets once and expects to
survive every model change, session and update.

Pure policy, like `budget.py`: it computes and persists, it never draws. The
TUI renders `FEATURES` into a page; it does not keep its own list of what
exists. That is what stops a switch being added to the settings file and
forgotten in the menu — the failure `MODE_CYCLE` exists to prevent for modes.

**A default is a claim about what the agent is for.** `streaming`,
`status_indicator` and `context_controls` default *on* because an agent that
answers in one silent block, shows nothing while it works, and cannot be
cleared is worse at its job. `advanced_diagnostics`, `debug_view`,
`prefill_context` and `short_every_third` default *off* because each one costs
the user something real — screen space, tokens, or the length of every third
answer — and a feature that takes something away has to be asked for.

`advanced_diagnostics` is the shallow end of `debug_view`, not a duplicate of
it. Debug view records whole payloads to a file for a second window to tail;
this one changes nothing about what is recorded and only decides how much of
what already happens reaches the chat. The two questions a user actually has
— "what did the agent send?" and "why did that turn behave oddly?" — are
answered by different amounts of evidence, and making the second one cost the
first is how a diagnostic gets left switched off. What counts as essential is
`core.events.is_essential`, stated beside the events rather than here.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Optional

SETTINGS_PATH = Path.home() / ".tomas" / "features.json"

#: Default tokens of filler `prefill_context` puts in front of a new
#: conversation. Approximate by construction — measured in the same
#: `CHARS_PER_TOKEN` estimate the rest of the agent counts with, not by a
#: tokeniser, because the point is to occupy a known share of the window
#: rather than to hit an exact number. See `prefill_messages`.
PREFILL_TARGET_TOKENS = 1_000

#: What the prefill-size picker offers. Spans three orders of magnitude on
#: purpose: 1k demonstrates the mechanism, while 100k is most of a 128k model's
#: window and is how you make compaction, the budget screen and the context
#: percentage do something visible on the very first turn.
PREFILL_CHOICES: tuple[int, ...] = (1_000, 10_000, 20_000, 25_000,
                                    50_000, 100_000)

#: `short_every_third` caps the reply at this many tokens. Under 100 by
#: requirement, and not *at* 100: a provider that counts a token differently
#: than the estimate would otherwise land on 101 and make the demonstration
#: argue with itself.
SHORT_REPLY_MAX_TOKENS = 90

#: Which reply gets capped. Every third, counted per session.
SHORT_REPLY_EVERY = 3


#: Every switch, in the order the settings page draws them. `default` is
#: repeated here rather than read off the dataclass so one table describes a
#: feature completely — label, explanation and default in one place, which is
#: what a reader needs and what a menu row is built from.
FEATURES: tuple[dict, ...] = (
    {"key": "streaming", "label": "Streaming replies",
     "detail": "text appears as it is generated, not in one block at the end",
     "default": True},
    {"key": "status_indicator", "label": "Live status",
     "detail": "shows analysing / calling a tool / writing, as it happens",
     "default": True},
    {"key": "context_controls", "label": "Context controls",
     "detail": "/clear to reset the conversation, /export to save the log",
     "default": True},
    {"key": "advanced_diagnostics", "label": "Advanced diagnostics",
     "detail": "retries, limits, cache and error detail as the chat happens",
     "default": False},
    {"key": "debug_view", "label": "Debug view",
     "detail": "Ctrl+Alt+X or /debug — raw JSON requests, schemas, responses",
     "default": False},
    {"key": "prefill_context", "label": "Prefill context",
     "detail": "start every session with a block of history already in place",
     "default": False},
    {"key": "short_every_third", "label": "Cap every 3rd reply",
     "detail": f"forces reply {SHORT_REPLY_EVERY} to under {SHORT_REPLY_MAX_TOKENS} tokens, and says so",
     "default": False},
)

FEATURE_KEYS = tuple(f["key"] for f in FEATURES)

#: Settings that hold a number rather than a yes/no. Kept as a separate table
#: because a switch and a size are answered by different UI — a toggle and a
#: picker — and a menu that tried to `Enter`-toggle a token count would be
#: guessing which direction the user meant.
CHOICES: tuple[dict, ...] = (
    {"key": "prefill_tokens", "label": "Prefill size",
     "detail": "how much history 'Prefill context' inserts",
     "values": PREFILL_CHOICES, "default": PREFILL_TARGET_TOKENS,
     "unit": "tokens", "depends_on": "prefill_context"},
)

CHOICE_KEYS = tuple(c["key"] for c in CHOICES)


@dataclass
class Features:
    """The switches, with the defaults the module docstring argues for."""

    streaming: bool = True
    status_indicator: bool = True
    context_controls: bool = True
    advanced_diagnostics: bool = False
    debug_view: bool = False
    prefill_context: bool = False
    short_every_third: bool = False
    prefill_tokens: int = PREFILL_TARGET_TOKENS

    def enabled(self, key: str) -> bool:
        """One accessor, so callers never `getattr` a key that does not exist.

        An unknown key reads as False rather than raising: this is consulted
        on the turn path, and a typo in a call site must not be able to end a
        session that would otherwise have worked.
        """
        return bool(getattr(self, key, False)) if key in FEATURE_KEYS else False

    def choice(self, key: str) -> int:
        """The stored number for a value setting, or its documented default."""
        spec = next((c for c in CHOICES if c["key"] == key), None)
        if spec is None:
            return 0
        value = getattr(self, key, None)
        return value if isinstance(value, int) else spec["default"]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "Features":
        """Build from stored JSON, ignoring anything unrecognised.

        Every switch is required to *be* a bool rather than coerced with
        `bool()`: a hand-edited `"false"` is a truthy string, and reading it as
        True would turn a switch the user believes is off into one that is on.
        Numbers get the same treatment, with `bool` excluded explicitly —
        `isinstance(True, int)` is True, so a stray `true` would otherwise be
        stored as a 1-token prefill.
        """
        if not isinstance(data, dict):
            return cls()
        clean = {}
        for key in FEATURE_KEYS:
            value = data.get(key)
            if isinstance(value, bool):
                clean[key] = value
        for key in CHOICE_KEYS:
            value = data.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                clean[key] = value
        return cls(**clean)


def load(path: Optional[Path] = None) -> Features:
    """Stored switches, or the defaults. Never raises — see `budget.load_settings`."""
    target = path or SETTINGS_PATH
    try:
        return Features.from_dict(json.loads(target.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return Features()


def save(features: Features, path: Optional[Path] = None) -> None:
    """Write atomically, so a truncated write cannot lose the whole file."""
    target = path or SETTINGS_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.tmp{os.getpid()}")
    try:
        tmp.write_text(json.dumps(features.to_dict(), indent=2,
                                  ensure_ascii=False), encoding="utf-8")
        tmp.replace(target)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def toggle(features: Features, key: str) -> Features:
    """Flip one switch, returning a new value for the caller to persist."""
    if key not in FEATURE_KEYS:
        return features
    return replace(features, **{key: not features.enabled(key)})


def set_choice(features: Features, key: str, value: int) -> Features:
    """Set one numeric setting, refusing anything not on its own menu.

    Bounded to the offered values rather than clamped to a range: these are a
    picker's rows, and accepting an arbitrary number here would let the stored
    setting be one the menu can never show as selected.
    """
    spec = next((c for c in CHOICES if c["key"] == key), None)
    if spec is None or value not in spec["values"]:
        return features
    return replace(features, **{key: value})


# ══════════════════════════════════════════════════════════════════════
#  Prefill
# ══════════════════════════════════════════════════════════════════════

#: The filler itself. Written as something the agent could plausibly have
#: been told, rather than lorem ipsum, because it is a real system prompt
#: prefix for the rest of the session: a model that reads nonsense at the top
#: of its context answers slightly worse for the whole conversation, which
#: would make every other measurement taken alongside it suspect.
_PREFILL_TOPIC = (
    "Earlier in this session the user and the agent established the working "
    "context below. It is recorded here so the agent starts with the same "
    "background it would have had partway through a longer conversation.\n\n"
    "The project is a terminal-based AI coding agent written in Python. It "
    "runs an agent loop: the model is called with a system prompt, a set of "
    "tool schemas and the conversation so far; if the reply asks for a tool, "
    "the tool runs, its result is appended, and the loop repeats until the "
    "model answers in plain text. Tools are classified by risk before they "
    "run, and the permission mode decides which of them need confirmation. "
    "The context window is treated as a budget with named line items rather "
    "than one number, so the cost of tool schemas, instructions and reserved "
    "output can each be seen and changed separately. "
)


def prefill_messages(target_tokens: int = PREFILL_TARGET_TOKENS,
                     chars_per_token: int = 4) -> list[dict]:
    """A user/assistant exchange of roughly `target_tokens`, for a fresh session.

    Returned as a real exchange rather than a single user message because a
    transcript that opens with two user turns in a row is malformed for some
    providers and merely odd for the rest — and because the agent's own
    `audit_transcript` counts a user turn with no reply as an incomplete
    session, which would mark every prefilled session broken.

    The size is an estimate in the same `chars_per_token` unit the rest of the
    agent counts in (see `CHARS_PER_TOKEN_PROSE`), so what the budget screen
    reports afterwards and what this aimed for are measured the same way.
    """
    if target_tokens <= 0:
        return []
    # The assistant half is a short acknowledgement, so essentially the whole
    # budget goes to the user turn that carries the content.
    reply = "Understood — I have that context and will work from it."
    body_chars = max(0, target_tokens * chars_per_token - len(reply))
    if body_chars <= 0:
        return []
    repeats = max(1, -(-body_chars // len(_PREFILL_TOPIC)))  # ceil
    body = (_PREFILL_TOPIC * repeats)[:body_chars]
    return [
        {"role": "user", "content": body},
        {"role": "assistant", "content": reply},
    ]


# ══════════════════════════════════════════════════════════════════════
#  The every-third-reply cap
# ══════════════════════════════════════════════════════════════════════

def caps_this_reply(features: Features, replies_so_far: int) -> bool:
    """Whether reply number `replies_so_far + 1` must be capped.

    `replies_so_far` is how many replies the session has already produced, so
    the reply about to be requested is the next one. Counting that way rather
    than from a mutable "is it my turn" flag keeps the rule a pure function of
    the transcript: the same session replayed answers the same question the
    same way, which is what makes it testable without running a turn.
    """
    if not features.enabled("short_every_third"):
        return False
    return (replies_so_far + 1) % SHORT_REPLY_EVERY == 0
