"""
The learning system: what the agent notices, remembers with evidence, and
applies invisibly.

Two functions are the whole public write/read API — every subsystem goes
through them:

    remember(kind, fact, evidence="", scope="global")
    recall(query, k=5, scopes=("global", "project"))

Everything in here is best-effort by design. Nothing in the learning path may
raise into the user's turn.
"""

from .corrections import detect_correction_signals
from .promotion import (
    PROMOTE_AT,
    decay,
    promote_corrections,
    record_observation,
    remember,
)
from .reflect import mode as reflect_mode
from .reflect import run_session_reflection, write_learned_skill
from .retrieval import directives_for_prompt, recall
from .store import (
    HARNESS_EVIDENCE_TAG,
    KIND_DIRECTIVE,
    KIND_EXPLICIT,
    LEARNED_DIR,
    MAX_DIRECTIVES,
    SKILLS_DIR,
    forget,
    load_active_facts,
    load_directives,
    load_facts,
    looks_like_directive,
    find_conflicts,
    migrate_legacy_stores,
    purge_harness_probes,
    redact,
    repair_frontmatter_facts,
    set_project,
    stale_dates,
    use_project,
)

__all__ = [
    "remember", "recall", "forget", "record_observation", "decay",
    "detect_correction_signals", "run_session_reflection", "write_learned_skill",
    "reflect_mode", "promote_corrections", "find_conflicts", "stale_dates",
    "load_facts", "load_active_facts", "migrate_legacy_stores", "redact",
    "set_project", "use_project", "is_enabled", "set_enabled",
    "directives_for_prompt", "load_directives", "looks_like_directive",
    "repair_frontmatter_facts", "purge_harness_probes", "HARNESS_EVIDENCE_TAG",
    "PROMOTE_AT", "LEARNED_DIR", "SKILLS_DIR",
    "KIND_DIRECTIVE", "KIND_EXPLICIT", "MAX_DIRECTIVES",
]

# ── Incognito ──────────────────────────────────────────────────────────
# Nothing is logged or reflected on while learning is disabled. Set by
# `/private` or --no-learn.

_enabled = True


def is_enabled() -> bool:
    return _enabled


def set_enabled(value: bool) -> None:
    global _enabled
    _enabled = bool(value)
