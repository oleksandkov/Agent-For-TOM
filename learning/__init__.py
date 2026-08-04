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
from .promotion import PROMOTE_AT, decay, record_observation, remember
from .reflect import run_session_reflection, write_learned_skill
from .retrieval import recall
from .store import (
    LEARNED_DIR,
    SKILLS_DIR,
    forget,
    load_active_facts,
    load_facts,
    migrate_legacy_stores,
    redact,
    set_project,
    use_project,
)

__all__ = [
    "remember", "recall", "forget", "record_observation", "decay",
    "detect_correction_signals", "run_session_reflection", "write_learned_skill",
    "load_facts", "load_active_facts", "migrate_legacy_stores", "redact",
    "set_project", "use_project", "is_enabled", "set_enabled",
    "PROMOTE_AT", "LEARNED_DIR", "SKILLS_DIR",
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
