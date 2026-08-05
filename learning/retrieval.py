"""
Retrieval — the piece that makes the memory system scale.

Memory used to be *dumped*: all of MEMORY.md into every prompt, truncated when
it overflowed, so prompt cost grew with everything ever learned until entries
silently vanished. Here we retrieve instead: score what is stored against the
current message and inject only the top few.

That is what keeps the prompt flat in size as knowledge grows — the property
that makes a memory system viable over years rather than weeks.
"""

from __future__ import annotations

import time

from .store import (
    KIND_DIRECTIVE,
    load_active_facts,
    load_directives,
    stale_dates,
)
from .text import extract_keywords

MAX_FACT_CHARS = 240
# A directive is the text the model must comply with verbatim, so it gets more
# room than a background fact. Truncating "always end every reply with…" at the
# 240th character can cut off the half that says what to do.
MAX_DIRECTIVE_TEXT_CHARS = 400
RECENCY_HALF_LIFE_DAYS = 30.0
MIN_SCORE = 0.1


def recency_boost(last_seen: float) -> float:
    """1.0 for something confirmed just now, decaying smoothly with age."""
    if not last_seen:
        return 0.0
    age_days = max(0.0, (time.time() - last_seen) / 86400.0)
    return 0.5 ** (age_days / RECENCY_HALF_LIFE_DAYS)


def score_fact(fact: dict, query_keywords: set) -> float:
    keywords = set(fact.get("keywords") or [])
    overlap = (len(query_keywords & keywords) / len(query_keywords)
               if query_keywords else 0.0)
    return overlap * 0.8 + recency_boost(fact.get("last_seen", 0)) * 0.2


def render_for_prompt(facts: list[dict]) -> str:
    if not facts:
        return ""
    lines = []
    for fact in facts:
        text = (fact.get("fact") or "").strip().replace("\n", " ")
        if len(text) > MAX_FACT_CHARS:
            text = text[:MAX_FACT_CHARS].rstrip() + "…"
        scope = "project" if fact.get("scope") == "project" else "user"
        lines.append(f"- ({scope}) {text}")
    return "\n".join(lines)


def recall(query: str = "", k: int = 5,
           scopes: tuple = ("global", "project")) -> str:
    """Return the k most relevant learned items for this message, as plain text.

    v1 scores with keyword overlap plus a recency boost — good enough, and the
    signature is embedding-ready: swap `score_fact`, change nothing else.

    An empty query falls back to the most recently confirmed facts, so callers
    that have no message to retrieve against still get something sensible.

    Directives are deliberately not candidates here — see `directives_for_prompt`.
    """
    candidates = load_active_facts(scopes, exclude_kinds=(KIND_DIRECTIVE,))
    if not candidates:
        return ""

    if not query.strip():
        top = sorted(candidates, key=lambda c: -c.get("last_seen", 0))[:k]
        return render_for_prompt(top)

    query_keywords = set(extract_keywords(query))
    scored = [(score_fact(c, query_keywords), c) for c in candidates]
    scored.sort(key=lambda pair: -pair[0])
    top = [c for score, c in scored[:k] if score > MIN_SCORE]

    # A relevant-to-nothing query still deserves the user's standing
    # preferences ("answer in Ukrainian") — those apply regardless of topic.
    if not top:
        top = sorted(candidates, key=lambda c: -c.get("last_seen", 0))[:min(k, 3)]
    return render_for_prompt(top)


def directives_for_prompt(scopes: tuple = ("global", "project")) -> str:
    """Every standing rule, rendered as a numbered imperative list.

    Numbered rather than bulleted on purpose: it makes the set countable, so
    "follow all 4" is checkable by the model in a way "follow the above" is not.
    """
    directives = load_directives(scopes)
    if not directives:
        return ""
    today = time.strftime("%Y-%m-%d")
    lines = []
    for i, fact in enumerate(directives, 1):
        text = (fact.get("fact") or "").strip().replace("\n", " ")
        if len(text) > MAX_DIRECTIVE_TEXT_CHARS:
            text = text[:MAX_DIRECTIVE_TEXT_CHARS].rstrip() + "…"
        # A rule naming a specific date is correct for exactly one day, and
        # then keeps being obeyed while wrong. Say so rather than silently
        # rewriting what the user wrote — "append 2026-08-05" might genuinely
        # mean that date, and only the user knows.
        expired = stale_dates(text, today)
        if expired:
            text += (f"  [note: this rule names {', '.join(expired)}, but today "
                     f"is {today} — if it meant \"the current date\", use {today}]")
        lines.append(f"{i}. {text}")
    dropped = directives[0].get("over_budget", 0)
    if dropped:
        lines.append(f"[{dropped} older standing rule(s) not shown — over budget. "
                     f"Use /memory to review them.]")
    return "\n".join(lines)
