"""
The learned-knowledge store.

One hierarchy replaces the four overlapping stores (memory/, self-improve/,
self-notes/, plus the session transcripts):

    ~/.tomas/learned/
      global/
        facts.jsonl          durable facts about the USER, all projects
        skills/*.md          learned skills, real frontmatter, discoverable
      projects/<sha1>/
        facts.jsonl          facts about THIS codebase
        meta.json            {"path": "C:\\...", "last_seen": ...}
      reflection-log.jsonl   shadow-mode reflection output
      tombstones.json        facts the user forgot; never re-learned

Scoping rule: facts about *the user* ("prefers PowerShell", "wants short
answers") are global. Facts about *a codebase* ("tests live in tests/") are
project-scoped, so one project's knowledge cannot leak into another's prompt.

Nothing here may raise into the user's turn — callers wrap, and the write
paths swallow their own I/O errors.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Iterable, Optional

from .text import extract_keywords, similarity

TOMAS_DIR = Path.home() / ".tomas"
LEARNED_DIR = TOMAS_DIR / "learned"
GLOBAL_DIR = LEARNED_DIR / "global"
PROJECTS_DIR = LEARNED_DIR / "projects"
SKILLS_DIR = GLOBAL_DIR / "skills"
REFLECTION_LOG = LEARNED_DIR / "reflection-log.jsonl"
TOMBSTONES_PATH = LEARNED_DIR / "tombstones.json"

# Legacy stores, imported once on first run.
LEGACY_MEMORY_DIR = TOMAS_DIR / "memory"
LEGACY_NOTES_DIR = TOMAS_DIR / "self-notes"

STATUS_OBSERVED = "observed"
STATUS_CANDIDATE = "candidate"
STATUS_ACTIVE = "active"

# ── Kinds ──────────────────────────────────────────────────────────────
# `directive` is the one kind retrieval must never own. The rest are beliefs
# about the user or the code — conditional, topical, correctly selected by
# relevance. A directive is unconditional ("always end with X"), so scoring it
# against the current message is a category error: the message it is relevant
# to is *every* message. Directives bypass recall entirely and are injected
# whole, every turn, in the imperative section of the prompt.
KIND_DIRECTIVE = "directive"
KIND_EXPLICIT = "explicit"

# A directive costs prompt budget on every single turn, so the cap is small and
# hard. Oldest-first eviction with a visible notice — silently dropping a rule
# the user set is the failure mode this whole change exists to remove.
MAX_DIRECTIVES = 10
MAX_DIRECTIVE_CHARS = 800

# Unconditional phrasing, in the three languages this agent actually sees.
# A false positive costs one prompt line; a false negative costs a rule that is
# silently never followed. The asymmetry is why this leans permissive.
_DIRECTIVE_PATTERNS = [
    re.compile(r"(?i)\b(?:always|never|every\s+(?:time|turn|reply|response|message)"
               r"|each\s+(?:reply|response|report|message)|from\s+now\s+on"
               r"|in\s+all\s+(?:replies|responses)|do\s+not\s+ever|don'?t\s+ever)\b"),
    re.compile(r"(?i)\b(?:завжди|ніколи|кожн\w*|віднині|надалі|обов'?язково"
               r"|у\s+кожн\w+\s+відповід\w+)\b"),
    re.compile(r"(?i)\b(?:всегда|никогда|кажд\w*|отныне|впредь|обязательно"
               r"|в\s+кажд\w+\s+ответ\w*)\b"),
]


# ── Directive hygiene ──────────────────────────────────────────────────
# A rule is not a fact: it stays in force until removed, so a rule that has
# quietly become wrong keeps being obeyed. The two ways that happens are a rule
# that names a specific date ("always append 2026-08-05" — correct for exactly
# one day) and two rules that contradict each other, where whichever the model
# reads last wins and the user cannot tell which that is.

_DATE_RE = re.compile(
    r"\b(\d{4})-(\d{2})-(\d{2})\b"                    # 2026-08-05
    r"|\b(\d{2})[./](\d{2})[./](\d{4})\b"             # 05.08.2026
)

_POSITIVE_RE = re.compile(r"(?i)\b(?:always|завжди|всегда|must|обов'?язково)\b")
_NEGATIVE_RE = re.compile(r"(?i)\b(?:never|ніколи|никогда|do not|don'?t|не\s+)\b")


def dates_in(text: str) -> list[str]:
    """ISO and DD.MM.YYYY dates mentioned in a rule, as ISO strings."""
    found = []
    for match in _DATE_RE.finditer(text or ""):
        if match.group(1):
            found.append(f"{match.group(1)}-{match.group(2)}-{match.group(3)}")
        else:
            found.append(f"{match.group(6)}-{match.group(5)}-{match.group(4)}")
    return found


def stale_dates(text: str, today: Optional[str] = None) -> list[str]:
    """Dates in this rule that are not today — i.e. the rule has expired."""
    today = today or time.strftime("%Y-%m-%d")
    return [d for d in dates_in(text) if d != today]


def find_conflicts(directives: list[dict]) -> list[tuple[str, str]]:
    """Pairs of rules that look like they contradict each other.

    Deliberately narrow. Full semantic contradiction needs a model; what is
    detectable cheaply is the common case — two rules about the same subject
    where one says "always" and the other "never". A false positive costs a
    warning line, so this only ever warns and never edits or drops a rule.
    """
    conflicts = []
    for i, a in enumerate(directives):
        text_a = a.get("fact") or ""
        for b in directives[i + 1:]:
            text_b = b.get("fact") or ""
            polar = ((_POSITIVE_RE.search(text_a) and _NEGATIVE_RE.search(text_b))
                     or (_NEGATIVE_RE.search(text_a) and _POSITIVE_RE.search(text_b)))
            if not polar:
                continue
            words_a = set(extract_keywords(text_a, max_keywords=20))
            words_b = set(extract_keywords(text_b, max_keywords=20))
            if not words_a or not words_b:
                continue
            overlap = len(words_a & words_b) / min(len(words_a), len(words_b))
            if overlap >= 0.6:
                conflicts.append((a.get("id", ""), b.get("id", "")))
    return conflicts


def looks_like_directive(text: str) -> bool:
    """True when the text reads as a standing rule rather than a preference.

    "Always end every reply with the date" is a directive — it governs output
    the user has not asked about yet. "The user prefers PowerShell" is not; it
    only applies when the topic comes up, which is exactly what retrieval is
    good at. Getting this split right is what decides whether a rule is obeyed
    0% or 100% of the time.
    """
    if not text:
        return False
    return any(p.search(text) for p in _DIRECTIVE_PATTERNS)


# ═══════════════════════════════════════════════════════════════════════
#  Project scoping
# ═══════════════════════════════════════════════════════════════════════

_current_project: Optional[Path] = None


def set_project(path) -> None:
    """Point project-scoped reads and writes at this directory."""
    global _current_project
    _current_project = Path(path).resolve() if path else None


def current_project() -> Path:
    return _current_project or Path.cwd()


@contextlib.contextmanager
def use_project(path):
    """Temporarily scope to another project (tests, multi-repo tooling)."""
    global _current_project
    previous = _current_project
    set_project(path)
    try:
        yield
    finally:
        _current_project = previous


def project_key(path=None) -> str:
    resolved = str(Path(path).resolve() if path else current_project()).lower()
    return hashlib.sha1(resolved.encode("utf-8")).hexdigest()[:16]


def scope_dir(scope: str) -> Path:
    if scope == "global":
        return GLOBAL_DIR
    return PROJECTS_DIR / project_key()


def facts_path(scope: str) -> Path:
    return scope_dir(scope) / "facts.jsonl"


# ═══════════════════════════════════════════════════════════════════════
#  Redaction — never persist a credential
# ═══════════════════════════════════════════════════════════════════════

_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),                       # OpenAI / Anthropic
    re.compile(r"gh[pousr]_[A-Za-z0-9]{16,}"),                  # GitHub
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"AIza[A-Za-z0-9_\-]{20,}"),                     # Google
    re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"),               # Slack
    re.compile(r"(?i)\b(?:bearer|api[_-]?key|secret|password|token)\b"
               r"\s*[:=]\s*[^\s,;]{8,}"),
    re.compile(r"\b[A-Za-z0-9+/]{40,}={0,2}\b"),                # long opaque blobs
]

REDACTED = "[redacted]"


def redact(text: str) -> str:
    """Strip anything that looks like a credential.

    log_user_message used to persist raw user content forever; anything that
    reaches long-term storage goes through here first.
    """
    if not text:
        return text
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(REDACTED, text)
    return text


# ═══════════════════════════════════════════════════════════════════════
#  JSONL I/O
# ═══════════════════════════════════════════════════════════════════════

def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        return []
    return out


def _write_jsonl(path: Path, rows: Iterable[dict]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
            encoding="utf-8",
        )
    except OSError:
        pass


def append_jsonl(path: Path, row: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


#: `recall()` and `directives_for_prompt()` each read both scopes independently
#: every turn — four full reads and JSON-parses of facts.jsonl per turn with
#: nothing else changing it in between. Keyed on mtime/size rather than a
#: timer, same reasoning as agent._stable_fingerprint(): a fact written this
#: process is visible on the very next read. Values are cached rows; callers
#: always get their own copy (see load_facts) because promotion.py mutates a
#: returned fact dict in place before saving — a shared reference would let
#: that mutation leak into other readers ahead of the save.
#:
#: Keyed on the resolved path, not on `scope` — "project" resolves through
#: `project_key()`, which changes when `set_project()` switches the active
#: project. Keying on the scope name alone would let a fact store from the
#: *previous* project answer for the new one until something else happened
#: to invalidate it.
_facts_cache: dict[str, tuple] = {}


def _facts_sig(path: Path) -> tuple:
    try:
        st = path.stat()
        return (int(st.st_mtime_ns), st.st_size)
    except OSError:
        return (0, -1)


def load_facts(scope: str) -> list[dict]:
    path = facts_path(scope)
    key = str(path)
    sig = _facts_sig(path)
    cached = _facts_cache.get(key)
    if cached is not None and cached[0] == sig:
        return [dict(f) for f in cached[1]]
    rows = _read_jsonl(path)
    _facts_cache[key] = (sig, rows)
    return [dict(f) for f in rows]


def save_facts(scope: str, facts: list[dict]) -> None:
    path = facts_path(scope)
    _write_jsonl(path, facts)
    # Recorded from our own write rather than left to the next load to
    # discover, so a save immediately followed by a load in the same process
    # cannot lose to mtime resolution (some filesystems round to ~1s).
    _facts_cache[str(path)] = (_facts_sig(path), [dict(f) for f in facts])
    if scope != "global":
        _write_project_meta()


def _write_project_meta() -> None:
    try:
        meta = scope_dir("project") / "meta.json"
        meta.parent.mkdir(parents=True, exist_ok=True)
        meta.write_text(json.dumps(
            {"path": str(current_project()), "last_seen": time.time()},
            indent=2), encoding="utf-8")
    except OSError:
        pass


def load_active_facts(scopes: tuple = ("global", "project"),
                      exclude_kinds: tuple = ()) -> list[dict]:
    """Only `active` facts are ever eligible to enter a prompt.

    `exclude_kinds` keeps directives out of retrieval — they are injected
    unconditionally elsewhere, and letting them compete for the top-k as well
    would spend the retrieval budget restating what the prompt already says.
    """
    out = []
    for scope in scopes:
        for fact in load_facts(scope):
            if fact.get("status") != STATUS_ACTIVE:
                continue
            if fact.get("kind") in exclude_kinds:
                continue
            fact = dict(fact)
            fact["scope"] = scope
            out.append(fact)
    return out


def load_directives(scopes: tuple = ("global", "project"),
                    limit: int = MAX_DIRECTIVES,
                    max_chars: int = MAX_DIRECTIVE_CHARS) -> list[dict]:
    """Every active standing rule, unscored, newest first.

    No relevance scoring and no `MIN_SCORE` gate: a rule that applies to every
    turn cannot be selected by how well it matches this turn. The only limits
    are the two budgets, and hitting either is reported rather than silently
    truncating — see `over_budget` on the returned records.
    """
    found = []
    for scope in scopes:
        for fact in load_facts(scope):
            if (fact.get("status") == STATUS_ACTIVE
                    and fact.get("kind") == KIND_DIRECTIVE):
                fact = dict(fact)
                fact["scope"] = scope
                found.append(fact)

    # Newest first: the most recent instruction wins the budget, because a rule
    # the user set thirty seconds ago is the one they are watching for.
    found.sort(key=lambda f: -f.get("last_seen", 0))

    kept, used = [], 0
    for fact in found[:limit]:
        text = (fact.get("fact") or "").strip()
        if used + len(text) > max_chars and kept:
            break
        kept.append(fact)
        used += len(text)
    for fact in kept:
        fact["over_budget"] = len(found) - len(kept)
    return kept


# ═══════════════════════════════════════════════════════════════════════
#  Fact records
# ═══════════════════════════════════════════════════════════════════════

def new_fact(kind: str, fact: str, evidence: str = "",
             status: str = STATUS_OBSERVED) -> dict:
    now = time.time()
    fact = redact(fact)
    return {
        "id": hashlib.sha1(f"{kind}:{fact}:{now}".encode()).hexdigest()[:12],
        "kind": kind,
        "fact": fact,
        "status": status,
        "evidence_count": 1,
        "evidence": [redact(evidence)] if evidence else [],
        "keywords": extract_keywords(fact),
        "first_seen": now,
        "last_seen": now,
    }


def find_similar(facts: list[dict], fact: str, threshold: float = 0.75) -> Optional[dict]:
    """The same belief phrased differently must reinforce, not duplicate."""
    for existing in facts:
        if existing.get("fact", "").strip().lower() == fact.strip().lower():
            return existing
        if similarity(existing.get("fact", ""), fact) >= threshold:
            return existing
    return None


# ═══════════════════════════════════════════════════════════════════════
#  Tombstones — /forget must stick
# ═══════════════════════════════════════════════════════════════════════

def _fingerprint(fact: str) -> str:
    words = sorted(set(extract_keywords(fact, max_keywords=20)))
    return hashlib.md5(" ".join(words).encode()).hexdigest()


def load_tombstones() -> list[str]:
    if not TOMBSTONES_PATH.exists():
        return []
    try:
        return json.loads(TOMBSTONES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


def is_tombstoned(fact: str) -> bool:
    return _fingerprint(fact) in set(load_tombstones())


def add_tombstone(fact: str) -> None:
    stones = load_tombstones()
    fp = _fingerprint(fact)
    if fp not in stones:
        stones.append(fp)
    try:
        TOMBSTONES_PATH.parent.mkdir(parents=True, exist_ok=True)
        TOMBSTONES_PATH.write_text(json.dumps(stones, indent=2), encoding="utf-8")
    except OSError:
        pass


def forget(fact_id: str, scopes: tuple = ("global", "project")) -> Optional[dict]:
    """Remove a fact and tombstone it so reflection cannot re-learn it."""
    for scope in scopes:
        facts = load_facts(scope)
        for i, fact in enumerate(facts):
            if fact.get("id") == fact_id:
                removed = facts.pop(i)
                save_facts(scope, facts)
                add_tombstone(removed.get("fact", ""))
                return removed
    return None


# ═══════════════════════════════════════════════════════════════════════
#  Migration from the pre-Phase-3 stores
# ═══════════════════════════════════════════════════════════════════════

_MIGRATION_MARKER = LEARNED_DIR / ".migrated"
_REPAIR_MARKER = LEARNED_DIR / ".repaired-frontmatter"

# Facts written by the self-note bridge before it was fixed carry the note's
# whole YAML frontmatter inside the fact text.
_FRONTMATTER_IN_FACT = re.compile(
    r"^(?P<title>.*?):\s*---\s*\n.*?\n---\s*\n+(?P<body>.*)$",
    re.DOTALL,
)


def repair_frontmatter_facts() -> dict:
    """Re-derive facts that were stored with a note's frontmatter baked in.

    Those rows are unusable in three separate ways: the keyword list is all
    `created_at` / `auto_generated` / note-id so the fact cannot be retrieved by
    its own subject, the 240-char render cap cuts the text off before the
    sentence the user actually wrote, and what does reach the prompt is a wall
    of metadata spending budget to say nothing.

    The same pass reclassifies pre-existing facts that are standing rules but
    were stored as `explicit`, because they predate the `directive` kind. That
    is not cosmetic: those are precisely the rules that were being injected as
    background trivia and ignored.

    Runs once. Returns a small report so the caller can say what it did.
    """
    report = {"repaired": 0, "promoted": 0, "reclassified": 0, "dropped": 0}
    if _REPAIR_MARKER.exists():
        return report
    try:
        for scope in ("global", "project"):
            facts = load_facts(scope)
            if not facts:
                continue
            kept, changed = [], False
            for fact in facts:
                text = fact.get("fact") or ""
                match = _FRONTMATTER_IN_FACT.match(text)
                if not match:
                    if (fact.get("kind") == KIND_EXPLICIT
                            and looks_like_directive(text)):
                        fact["kind"] = KIND_DIRECTIVE
                        fact["status"] = STATUS_ACTIVE
                        report["reclassified"] += 1
                        changed = True
                    kept.append(fact)
                    continue
                changed = True
                title = match.group("title").strip()
                body = match.group("body").strip()
                if not body:
                    # Nothing survived but metadata — there is no fact here.
                    report["dropped"] += 1
                    continue
                fact["fact"] = redact(f"{title}: {body}" if title else body)
                fact["keywords"] = extract_keywords(fact["fact"])
                if looks_like_directive(body):
                    fact["kind"] = KIND_DIRECTIVE
                    if fact.get("status") != STATUS_ACTIVE:
                        fact["status"] = STATUS_ACTIVE
                        report["promoted"] += 1
                report["repaired"] += 1
                kept.append(fact)
            if changed:
                save_facts(scope, kept)

        LEARNED_DIR.mkdir(parents=True, exist_ok=True)
        _REPAIR_MARKER.write_text(
            json.dumps({"at": time.time(), **report}), encoding="utf-8")
    except Exception:
        return report
    return report


# Evidence strings the harness stamps on anything it writes, so its own rows
# can be found and removed again. A test that leaves facts in the real store
# spends the user's prompt budget on "written by tests/simulate.py" forever.
HARNESS_EVIDENCE_TAG = "[harness-probe]"


def purge_harness_probes(scopes: tuple = ("global", "project")) -> int:
    """Remove rows this repo's own test harness wrote. Called by the harness.

    Deliberately not part of `repair_frontmatter_facts`: repairing malformed
    data is safe to do behind the user's back, deleting rows from their store is
    not. The harness made these, so the harness cleans them up.
    """
    removed = 0
    for scope in scopes:
        facts = load_facts(scope)
        kept = [f for f in facts
                if HARNESS_EVIDENCE_TAG not in " ".join(f.get("evidence") or [])]
        if len(kept) != len(facts):
            removed += len(facts) - len(kept)
            save_facts(scope, kept)
    return removed


def migrate_legacy_stores() -> int:
    """Import ~/.tomas/memory/*.md as active explicit facts and self-notes as
    observations. Runs once.

    Generated template skills and tips are deliberately not imported — they
    contain no information derived from any actual interaction.
    """
    if _MIGRATION_MARKER.exists():
        return 0
    imported = 0
    try:
        LEARNED_DIR.mkdir(parents=True, exist_ok=True)
        facts = load_facts("global")

        for path in sorted(LEGACY_MEMORY_DIR.glob("*.md")):
            if path.name == "MEMORY.md":
                continue
            try:
                raw = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            body = re.sub(r"^---.*?---\s*", "", raw, flags=re.DOTALL).strip()
            if not body or find_similar(facts, body):
                continue
            record = new_fact("explicit", body,
                              evidence=f"migrated from {path.name}",
                              status=STATUS_ACTIVE)
            record["evidence_count"] = 3  # user said it outright; already earned
            facts.append(record)
            imported += 1

        notes_index = LEGACY_NOTES_DIR / "index.json"
        if notes_index.exists():
            try:
                index = json.loads(notes_index.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                index = {"notes": []}
            for note in index.get("notes", []):
                if note.get("auto_generated"):
                    continue
                title = note.get("title", "").strip()
                if not title or find_similar(facts, title):
                    continue
                facts.append(new_fact("note", title,
                                      evidence="migrated from self-notes"))
                imported += 1

        save_facts("global", facts)
        _MIGRATION_MARKER.write_text(
            json.dumps({"at": time.time(), "imported": imported}),
            encoding="utf-8")
    except Exception:
        return imported
    return imported
