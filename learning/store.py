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


def load_facts(scope: str) -> list[dict]:
    return _read_jsonl(facts_path(scope))


def save_facts(scope: str, facts: list[dict]) -> None:
    _write_jsonl(facts_path(scope), facts)
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


def load_active_facts(scopes: tuple = ("global", "project")) -> list[dict]:
    """Only `active` facts are ever eligible to enter a prompt."""
    out = []
    for scope in scopes:
        for fact in load_facts(scope):
            if fact.get("status") == STATUS_ACTIVE:
                fact = dict(fact)
                fact["scope"] = scope
                out.append(fact)
    return out


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
