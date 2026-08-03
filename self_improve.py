"""
Interaction log and session-purpose analysis.

This module used to detect "patterns" by counting keywords and then fill in
Markdown templates from them ("when starting with read_file, consider
following up with read_file"). That generator was deleted in Phase 6: nothing
consumed its output after Phase 3 removed the prompt injection point, and its
28 template files were crowding real skills out of the context budget.

What remains is the raw signal and a cheap session summary. Actual learning
is `learning/` — reflection over transcripts by a model, not by a counter.

Data is stored under ~/.tomas/self-improve/:
  - interactions.jsonl   — every user message + tool call, timestamped
                           (read by learning/)
  - session.json         — current session purpose analysis
"""

from __future__ import annotations

import collections
import json
import os
import re
import time
import hashlib
from pathlib import Path
from typing import Any, Optional

# ── Constants ──────────────────────────────────────────────────────────

SELF_IMPROVE_DIR = Path.home() / ".tomas" / "self-improve"
INTERACTIONS_FILE = SELF_IMPROVE_DIR / "interactions.jsonl"
PATTERNS_FILE = SELF_IMPROVE_DIR / "patterns.json"
TIPS_FILE = SELF_IMPROVE_DIR / "tips.json"
SESSION_FILE = SELF_IMPROVE_DIR / "session.json"
SKILLS_DIR = SELF_IMPROVE_DIR / "skills"
TIPS_DIR = SELF_IMPROVE_DIR / "tips"

# How many recent interactions session analysis considers — bounds per-call
# cost independent of how long interactions.jsonl has grown.
ANALYSIS_WINDOW = 500

# Rotate interactions.jsonl once it crosses this size, keeping the log
# tailable in O(window) instead of O(total history).
ROTATE_AT_BYTES = 5 * 1024 * 1024
ROTATE_KEEP = 3

# Common stop words for keyword extraction
STOP_WORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "dare", "ought",
    "used", "to", "of", "in", "for", "on", "with", "at", "by", "from",
    "as", "into", "through", "during", "before", "after", "above", "below",
    "between", "out", "off", "over", "under", "again", "further", "then",
    "once", "here", "there", "when", "where", "why", "how", "all", "each",
    "every", "both", "few", "more", "most", "other", "some", "such", "no",
    "nor", "not", "only", "own", "same", "so", "than", "too", "very",
    "just", "because", "but", "and", "or", "if", "while", "that", "this",
    "these", "those", "it", "its", "what", "which", "who", "whom",
    "about", "up", "down",
}


# ═══════════════════════════════════════════════════════════════════════
#  Data I/O
# ═══════════════════════════════════════════════════════════════════════

def _ensure_dirs() -> None:
    """Create the directories this module still writes to.

    SKILLS_DIR and TIPS_DIR are deliberately not recreated — the generator
    that filled them is gone, and recreating them would undo the migration on
    the next startup.
    """
    SELF_IMPROVE_DIR.mkdir(parents=True, exist_ok=True)


def _load_json(path: Path, default: Any = None) -> Any:
    """Load a JSON file safely."""
    if default is None:
        default = {}
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def _save_json(path: Path, data: Any) -> None:
    """Save data as indented JSON."""
    _ensure_dirs()
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _rotate_if_large(path: Path) -> None:
    """Archive a .jsonl file once it crosses ROTATE_AT_BYTES, keeping the
    last ROTATE_KEEP archives. Deep history has little value here — Phase 3
    mines sessions, not raw interaction logs."""
    try:
        if not path.exists() or path.stat().st_size <= ROTATE_AT_BYTES:
            return
        path.rename(path.with_suffix(f".{int(time.time())}.jsonl"))
        archives = sorted(
            path.parent.glob(f"{path.stem}.*.jsonl"), key=lambda p: p.stat().st_mtime
        )
        for old in archives[:-ROTATE_KEEP]:
            old.unlink(missing_ok=True)
    except OSError:
        pass


def _append_jsonl(path: Path, entry: dict) -> None:
    """Append a JSON line to a .jsonl file."""
    _ensure_dirs()
    _rotate_if_large(path)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _read_jsonl(path: Path) -> list[dict]:
    """Read all JSON lines from a .jsonl file."""
    if not path.exists():
        return []
    lines = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                lines.append(json.loads(line))
    except (json.JSONDecodeError, OSError):
        pass
    return lines


# ═══════════════════════════════════════════════════════════════════════
#  Text utilities
# ═══════════════════════════════════════════════════════════════════════

def _extract_keywords(text: str, max_keywords: int = 8) -> list[str]:
    """Extract meaningful keywords from text (simple frequency-based)."""
    text = text.lower()
    # Remove code blocks
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    # Remove URLs
    text = re.sub(r"https?://\S+", "", text)
    # Remove punctuation and split
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9_\-']{1,}", text)
    # Filter stop words and short words
    words = [w for w in words if w not in STOP_WORDS and len(w) > 2]
    # Count frequency
    freq: dict[str, int] = {}
    for w in words:
        freq[w] = freq.get(w, 0) + 1
    # Sort by frequency
    sorted_words = sorted(freq.items(), key=lambda x: -x[1])
    return [w for w, _ in sorted_words[:max_keywords]]


def _compute_fingerprint(text: str) -> str:
    """Compute a similarity fingerprint for a text fragment."""
    words = sorted(set(_extract_keywords(text, max_keywords=20)))
    return hashlib.md5(" ".join(words).encode()).hexdigest()


# ═══════════════════════════════════════════════════════════════════════
#  Interaction Tracking
# ═══════════════════════════════════════════════════════════════════════

def log_user_message(content: str, msg_type: str = "text") -> dict:
    """
    Log a user message for pattern analysis.
    Returns the logged entry.
    """
    entry = {
        "timestamp": time.time(),
        "type": "user_message",
        "msg_type": msg_type,
        "content": content[:2000],  # truncate for storage
        "keywords": _extract_keywords(content),
        "fingerprint": _compute_fingerprint(content),
    }
    _append_jsonl(INTERACTIONS_FILE, entry)
    return entry


def log_tool_call(tool_name: str, args: dict, result_preview: str = "") -> dict:
    """
    Log a tool call for pattern analysis.
    """
    entry = {
        "timestamp": time.time(),
        "type": "tool_call",
        "tool_name": tool_name,
        "args_keys": list(args.keys()),
        "result_preview": result_preview[:500],
    }
    _append_jsonl(INTERACTIONS_FILE, entry)
    return entry


def get_recent_interactions(n: int = ANALYSIS_WINDOW) -> list[dict]:
    """Return the most recent N interactions, tailing the file instead of
    reading it in full — cost is O(n), not O(total history)."""
    if not INTERACTIONS_FILE.exists():
        return []
    with INTERACTIONS_FILE.open("r", encoding="utf-8") as f:
        tail = collections.deque(f, maxlen=n)
    out = []
    for line in tail:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def get_all_interactions() -> list[dict]:
    """Return all logged interactions."""
    return _read_jsonl(INTERACTIONS_FILE)


# ═══════════════════════════════════════════════════════════════════════
#  Session Analysis
# ═══════════════════════════════════════════════════════════════════════

def analyze_session_purpose(interactions: list[dict] | None = None) -> dict:
    """
    Analyze the current session to determine the user's purpose and goals.
    Uses keyword analysis and pattern matching (no API call needed).

    Returns a session analysis dict with:
      - purpose: inferred user goal
      - keywords: extracted topics
      - complexity: estimated task complexity
      - tools_needed: which tools are likely needed
      - stage: current stage of the task
    """
    if interactions is None:
        interactions = get_recent_interactions(ANALYSIS_WINDOW)

    user_msgs = [
        e for e in interactions
        if e.get("type") == "user_message"
    ]

    if not user_msgs:
        return {
            "purpose": "unknown",
            "keywords": [],
            "complexity": "unknown",
            "tools_needed": [],
            "stage": "initiation",
            "message_count": 0,
        }

    # Collect all keywords
    all_keywords: list[str] = []
    for msg in user_msgs[:5]:  # Focus on first few messages
        all_keywords.extend(msg.get("keywords", []))

    # Determine purpose from keywords
    purpose = _infer_purpose(all_keywords, user_msgs)

    # Determine complexity
    complexity = _infer_complexity(interactions)

    # Determine tools needed
    tools_needed = _infer_tools_needed(all_keywords)

    # Determine stage
    stage = _infer_stage(interactions)

    return {
        "purpose": purpose,
        "keywords": list(set(all_keywords))[:15],
        "complexity": complexity,
        "tools_needed": tools_needed,
        "stage": stage,
        "message_count": len(user_msgs),
        "total_interactions": len(interactions),
        "analyzed_at": time.time(),
    }


PURPOSE_SIGNALS: dict[str, list[str]] = {
    "coding": {
        "code", "function", "class", "implement", "write", "create", "build",
        "refactor", "fix", "bug", "error", "compile", "syntax", "test",
        "debug", "feature", "module", "api", "endpoint", "database",
    },
    "data_analysis": {
        "data", "csv", "json", "analyze", "statistics", "chart", "plot",
        "visualize", "report", "metrics", "dashboard", "pandas", "sql",
        "query", "table",
    },
    "research": {
        "search", "find", "research", "lookup", "documentation", "docs",
        "learn", "understand", "explain", "what is", "how to", "compare",
        "versus", "difference",
    },
    "devops": {
        "deploy", "docker", "kubernetes", "aks", "azure", "aws", "cloud",
        "pipeline", "ci/cd", "infrastructure", "config", "environment",
        "server", "container", "cluster",
    },
    "writing": {
        "write", "document", "readme", "docstring", "comment", "explain",
        "describe", "summarize", "outline", "draft",
    },
    "maintenance": {
        "update", "upgrade", "migrate", "move", "rename", "refactor",
        "clean", "remove", "delete", "organize", "restructure",
    },
    "learning": {
        "tutorial", "guide", "example", "practice", "exercise", "learn",
        "understand", "concept", "basics", "introduction",
    },
}


def _infer_purpose(keywords: list[str], messages: list[dict]) -> str:
    """Infer the user's purpose from keywords and messages."""
    kw_set = set(keywords)

    # Score each purpose category
    scores: dict[str, int] = {}
    for purpose, signals in PURPOSE_SIGNALS.items():
        scores[purpose] = len(kw_set & signals)

    if not scores or max(scores.values()) == 0:
        return "general_assistance"

    best = max(scores, key=scores.get)
    if scores[best] < 2:
        return "general_assistance"
    return best


def _infer_complexity(interactions: list[dict]) -> str:
    """Estimate task complexity based on interaction patterns."""
    tool_calls = [e for e in interactions if e.get("type") == "tool_call"]
    user_msgs = [e for e in interactions if e.get("type") == "user_message"]

    if len(tool_calls) > 20 or len(user_msgs) > 10:
        return "complex"
    elif len(tool_calls) > 8 or len(user_msgs) > 4:
        return "moderate"
    else:
        return "simple"


def _infer_tools_needed(keywords: list[str]) -> list[str]:
    """Infer which tools are likely needed based on keywords."""
    tools = []
    kw_set = set(keywords)

    # Check for tool triggers
    tool_triggers = {
        "read_file": {"read", "view", "show", "open", "file", "content"},
        "write_file": {"create", "write", "save", "new file"},
        "edit_file": {"edit", "change", "modify", "update", "fix"},
        "run_command": {"run", "execute", "install", "build", "test"},
        "search_code": {"search", "find", "locate", "where"},
        "fetch_url": {"fetch", "download", "url", "http", "api"},
        "search_web": {"search web", "google", "lookup", "find online"},
    }

    for tool, triggers in tool_triggers.items():
        if kw_set & triggers:
            tools.append(tool)

    return tools


STAGE_SIGNALS: dict[str, list[str]] = {
    "initiation": {"start", "begin", "new", "create", "set up", "initial"},
    "exploration": {"read", "look", "find", "search", "check", "explore"},
    "execution": {"implement", "write", "edit", "change", "build", "run"},
    "verification": {"test", "verify", "check", "validate", "review"},
    "refinement": {"fix", "improve", "optimize", "refactor", "clean"},
    "completion": {"done", "finish", "complete", "summarize", "report"},
}


def _infer_stage(interactions: list[dict]) -> str:
    """Infer the current stage of the user's task."""
    recent = interactions[-10:] if len(interactions) >= 10 else interactions
    recent_keywords: list[str] = []
    for entry in recent:
        if entry.get("type") == "user_message":
            recent_keywords.extend(entry.get("keywords", []))

    kw_set = set(recent_keywords)
    # Check stages from most advanced to least
    for stage in ["completion", "refinement", "verification", "execution", "exploration"]:
        signals = STAGE_SIGNALS.get(stage, set())
        if kw_set & signals:
            return stage

    return "initiation"


def get_session_analysis() -> dict:
    """
    Get or compute the current session analysis.
    Cached in session.json for efficiency.
    """
    data = _load_json(SESSION_FILE, {"purpose": "unknown"})
    return data


def update_session_analysis() -> dict:
    """Force re-analysis and save."""
    analysis = analyze_session_purpose()
    _save_json(SESSION_FILE, analysis)
    return analysis


# ═══════════════════════════════════════════════════════════════════════
#  Public API — called by agent.py
# ═══════════════════════════════════════════════════════════════════════

def init() -> None:
    """Initialise the self-improvement system."""
    _ensure_dirs()
    # One-shot: clear the deleted generator's output off this user's disk.
    try:
        migrate_remove_generated()
    except Exception:
        pass
    # Create initial session analysis
    if not SESSION_FILE.exists():
        update_session_analysis()


def record_user_message(content: str, msg_type: str = "text") -> None:
    """Log a user message. The log is the input to learning/; nothing is
    derived from it on this hot path."""
    log_user_message(content, msg_type)


def record_tool_call(tool_name: str, args: dict, result_preview: str = "") -> None:
    """Record a tool call."""
    log_tool_call(tool_name, args, result_preview)


def maybe_analyze_after_turn() -> None:
    """Call once per user turn, after the reply has been delivered.

    The pattern/skill/tip generator that used to run here was deleted in
    Phase 6. It counted keywords and filled in templates — "when starting
    with read_file, consider following up with read_file" — writing 28 such
    files to disk and crowding real skills out of the prompt budget. Nothing
    consumed its output after Phase 3 removed the injection point.

    Session purpose analysis is cheap and still read by /self-improve, so it
    stays. Genuine learning lives in learning/ (reflection over transcripts).
    """
    update_session_analysis()


def get_self_improve_status() -> str:
    """Return a human-readable status of the self-improvement system."""
    interactions = get_all_interactions()
    session = get_session_analysis()

    lines = [
        f'  {BOLD}Self-Improvement System{RESET}',
        f'  {DIM}{"─" * 46}{RESET}',
        f'  {CYAN}◈{RESET}  Interactions: {len(interactions)}',
        f'  {DIM}   Learning lives in learning/ — see /self-improve facts{RESET}',
        '',
        f'  {BOLD}Session Analysis{RESET}',
        f'  {DIM}Purpose:{RESET}   {session.get("purpose", "unknown")}',
        f'  {DIM}Complexity:{RESET} {session.get("complexity", "unknown")}',
        f'  {DIM}Stage:{RESET}     {session.get("stage", "unknown")}',
        f'  {DIM}Keywords:{RESET}  {", ".join(session.get("keywords", [])[:6])}',
    ]

    return '\n'.join(lines)


# ═══════════════════════════════════════════════════════════════════════
#  Migration — remove the deleted generator's output
# ═══════════════════════════════════════════════════════════════════════

def migrate_remove_generated() -> list[str]:
    """Delete the pattern generator's leftovers from the user's disk.

    The generator is gone from the code, but its 28 skill files and its
    tips.json are still in ~/.tomas on every machine that ran an older
    build. `interactions.jsonl` is kept — learning/ reads it.

    Returns a list of what was removed. Safe to call repeatedly.
    """
    import shutil
    removed: list[str] = []
    if SKILLS_DIR.is_dir():
        n = len(list(SKILLS_DIR.glob("*.md")))
        shutil.rmtree(SKILLS_DIR, ignore_errors=True)
        removed.append(f"{SKILLS_DIR} ({n} generated skills)")
    for path in (TIPS_FILE, PATTERNS_FILE, SELF_IMPROVE_DIR / "skill-registry.json"):
        if path.exists():
            try:
                path.unlink()
                removed.append(str(path))
            except OSError:
                pass
    if TIPS_DIR.is_dir():
        shutil.rmtree(TIPS_DIR, ignore_errors=True)
        removed.append(str(TIPS_DIR))
    return removed


# Patch for ANSI codes
try:
    from agent import GREEN, DIM, RESET, CYAN, YELLOW, BOLD, RED
except ImportError:
    GREEN = '\033[92m'
    DIM = '\033[2m'
    RESET = '\033[0m'
    CYAN = '\033[96m'
    YELLOW = '\033[93m'
    BOLD = '\033[1m'
    RED = '\033[91m'
