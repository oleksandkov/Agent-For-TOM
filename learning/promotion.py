"""
The evidence gate.

Never let one session write a permanent rule. Reflection will occasionally
infer a preference from an ambiguous exchange; without a gate that becomes a
permanent false belief about the user, silently applied forever.

    observed (seen once) → candidate (2 sessions) → active (3+, confirmed)
                                  │
                                  └─ not re-confirmed in 30 days → decays out

Only `active` facts are ever eligible to enter a prompt (see retrieval.recall).
"""

from __future__ import annotations

import time
from typing import Optional

from .store import (
    KIND_DIRECTIVE,
    KIND_EXPLICIT,
    STATUS_ACTIVE,
    STATUS_CANDIDATE,
    STATUS_OBSERVED,
    find_similar,
    is_tombstoned,
    load_facts,
    new_fact,
    redact,
    save_facts,
)

# Kinds the user authored on purpose. The gate exists to stop *reflection*
# inventing permanent beliefs from one ambiguous exchange — it was never meant
# to sit between the user typing a rule and the agent following it. Something
# said outright is already confirmed; making it wait three sessions is how a
# `/note` ended up invisible for its entire useful life.
UNGATED_KINDS = (KIND_EXPLICIT, KIND_DIRECTIVE)

PROMOTE_AT = 3        # sessions of supporting evidence before a fact goes active
DECAY_DAYS = 30
MAX_EVIDENCE_KEPT = 5


def _status_for(evidence_count: int) -> str:
    if evidence_count >= PROMOTE_AT:
        return STATUS_ACTIVE
    if evidence_count >= 2:
        return STATUS_CANDIDATE
    return STATUS_OBSERVED


def record_observation(kind: str, fact: str, evidence: str = "",
                       scope: str = "global",
                       extra: Optional[dict] = None) -> tuple[Optional[dict], bool]:
    """Merge an observation into the store, or reinforce an existing one.

    `extra` is merged into the record — used to carry a skill candidate's body
    until it has earned enough evidence to be written out as a real skill.

    Returns (record, newly_promoted). `newly_promoted` is True only on the
    transition into `active`, which is the moment worth announcing.
    """
    fact = (fact or "").strip()
    if not fact:
        return None, False
    if is_tombstoned(fact):
        return None, False   # the user forgot this; do not re-learn it

    facts = load_facts(scope)
    existing = find_similar(facts, fact)

    if existing:
        was_active = existing.get("status") == STATUS_ACTIVE
        existing["evidence_count"] = existing.get("evidence_count", 1) + 1
        existing["last_seen"] = time.time()
        if evidence:
            existing.setdefault("evidence", []).append(redact(evidence))
            existing["evidence"] = existing["evidence"][-MAX_EVIDENCE_KEPT:]
        if extra:
            existing.update(extra)
        existing["status"] = _status_for(existing["evidence_count"])
        save_facts(scope, facts)
        return existing, (not was_active and existing["status"] == STATUS_ACTIVE)

    record = new_fact(kind, fact, evidence)
    if extra:
        record.update(extra)
    facts.append(record)
    save_facts(scope, facts)
    return record, False


def remember(kind: str, fact: str, evidence: str = "",
             scope: str = "global") -> Optional[dict]:
    """Public write API.

    `explicit` and `directive` mean the user said it outright — no inference
    involved, so they skip the gate and are active immediately. Everything else
    must earn its way up through repeated evidence.
    """
    fact = (fact or "").strip()
    if not fact:
        return None
    if kind not in UNGATED_KINDS:
        record, _ = record_observation(kind, fact, evidence, scope)
        return record

    facts = load_facts(scope)
    existing = find_similar(facts, fact)
    if existing:
        existing["status"] = STATUS_ACTIVE
        existing["evidence_count"] = max(existing.get("evidence_count", 1), PROMOTE_AT)
        existing["last_seen"] = time.time()
        # Restating a preference in unconditional terms promotes it to a
        # directive. The reverse never happens automatically: demoting a rule
        # the user is relying on has to be a deliberate act (/forget).
        if kind == KIND_DIRECTIVE:
            existing["kind"] = KIND_DIRECTIVE
        if evidence:
            existing.setdefault("evidence", []).append(redact(evidence))
            existing["evidence"] = existing["evidence"][-MAX_EVIDENCE_KEPT:]
        save_facts(scope, facts)
        return existing

    record = new_fact(kind, fact, evidence, status=STATUS_ACTIVE)
    record["evidence_count"] = PROMOTE_AT
    facts.append(record)
    save_facts(scope, facts)
    return record


MAX_CORRECTION_CHARS = 300


def promote_corrections(messages: list) -> list[dict]:
    """Turn repeated corrections into standing rules.

    Being corrected is the strongest signal in a session: the user has told the
    agent, in their own words, that it did the wrong thing. Until now those
    signals were detected, handed to reflection, and — because reflection
    shipped disabled — discarded entirely.

    Only corrections phrased as a rule are eligible. "no, not like that" says
    something went wrong but not what to do instead; "no, I said always use
    type hints" is a directive the user is repeating because it was not
    followed. The second is actionable without a model in the loop.

    These go through the evidence gate rather than around it, unlike a rule the
    user states deliberately. An inference drawn from an annoyed message is
    exactly the kind of guess the gate exists to hold back — but a correction
    the user makes three times is not a guess any more.

    Returns the records touched, newest first. Never raises.
    """
    from .corrections import detect_correction_signals
    from .store import looks_like_directive

    touched: list[dict] = []
    try:
        signals = detect_correction_signals(messages)
    except Exception:
        return touched

    seen: set[str] = set()
    for signal in signals:
        if signal.get("kind") != "explicit_correction":
            continue
        text = (signal.get("text") or "").strip()[:MAX_CORRECTION_CHARS]
        if not text or text.lower() in seen:
            continue
        seen.add(text.lower())
        if not looks_like_directive(text):
            continue
        try:
            record, promoted = record_observation(
                KIND_DIRECTIVE, text,
                evidence="user corrected the agent on this",
                scope="global")
        except Exception:
            continue
        if record:
            record = dict(record)
            record["newly_promoted"] = promoted
            touched.append(record)
    return touched


def decay(scope: str = "global") -> int:
    """Age out beliefs that stopped being reinforced. Returns how many went.

    Well-established facts (double the promotion threshold) are kept
    regardless — those have earned permanence.
    """
    cutoff = time.time() - DECAY_DAYS * 86400
    facts = load_facts(scope)
    kept = [f for f in facts
            if f.get("last_seen", 0) > cutoff
            or f.get("evidence_count", 0) >= PROMOTE_AT * 2]
    if len(kept) != len(facts):
        save_facts(scope, kept)
    return len(facts) - len(kept)
