"""
Self-Improving System — learns from user interactions, detects patterns,
auto-generates skills, and creates self-improvement tips.

Inspired by the Hermes AI self-improvement approach: the agent monitors
conversations, identifies repetitive patterns, generates reusable skills,
and produces tips to improve its own behaviour over time.

Data is stored under ~/.tomas/self-improve/:
  - interactions.jsonl   — every user message + tool call, timestamped
  - patterns.json         — detected repetitive patterns with metadata
  - tips.json             — generated self-improvement tips
  - skills/              — auto-generated SKILL.md files
  - session.json          — current session purpose analysis
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

# How many repetitions of a similar pattern before we auto-generate a skill
PATTERN_TO_SKILL_THRESHOLD = 3

# How many interactions before we re-analyze patterns
ANALYZE_INTERVAL = 5

# How many recent interactions pattern analysis considers — bounds per-call
# cost independent of how long interactions.jsonl has grown.
ANALYSIS_WINDOW = 500

# Rotate interactions.jsonl once it crosses this size, keeping the log
# tailable in O(window) instead of O(total history).
ROTATE_AT_BYTES = 5 * 1024 * 1024
ROTATE_KEEP = 3

# How many patterns before we generate a tip
PATTERN_TO_TIP_THRESHOLD = 5

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
    """Create all self-improvement directories."""
    SELF_IMPROVE_DIR.mkdir(parents=True, exist_ok=True)
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    TIPS_DIR.mkdir(parents=True, exist_ok=True)


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


def _similarity_score(a: str, b: str) -> float:
    """
    Return a 0.0-1.0 score of how similar two strings are,
    based on keyword Jaccard similarity.
    """
    ka = set(_extract_keywords(a, max_keywords=15))
    kb = set(_extract_keywords(b, max_keywords=15))
    if not ka or not kb:
        return 0.0
    intersection = ka & kb
    union = ka | kb
    return len(intersection) / len(union)


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
#  Pattern Detection
# ═══════════════════════════════════════════════════════════════════════

def analyze_patterns(force: bool = False) -> dict:
    """
    Analyze logged interactions to detect repetitive patterns.
    Returns a dict of detected patterns.

    Pattern types detected:
    - tool_patterns: tools used most frequently
    - keyword_clusters: topics that appear often
    - task_patterns: sequences of tools used together
    - repetition_patterns: very similar messages
    """
    interactions = get_recent_interactions(ANALYSIS_WINDOW)
    if len(interactions) < 3:
        return {"patterns": [], "analyzed_at": time.time()}

    # ── Load existing patterns ──
    existing = _load_json(PATTERNS_FILE, {"patterns": [], "analyzed_at": 0})
    existing_patterns = existing.get("patterns", [])

    # ── Tool frequency ──
    tool_counts: dict[str, int] = {}
    for entry in interactions:
        if entry.get("type") == "tool_call":
            tool_counts[entry["tool_name"]] = tool_counts.get(entry["tool_name"], 0) + 1

    # ── Keyword clusters (topics) ──
    keyword_freq: dict[str, int] = {}
    for entry in interactions:
        if entry.get("type") == "user_message":
            for kw in entry.get("keywords", []):
                keyword_freq[kw] = keyword_freq.get(kw, 0) + 1

    # ── Task patterns (tool sequences) ──
    tool_sequence: list[str] = [
        e["tool_name"] for e in interactions
        if e.get("type") == "tool_call"
    ]
    task_patterns: dict[str, int] = {}
    for i in range(len(tool_sequence) - 1):
        pair = f"{tool_sequence[i]} → {tool_sequence[i+1]}"
        task_patterns[pair] = task_patterns.get(pair, 0) + 1

    # ── Repetition detection ──
    # Comparing every message pair is O(n²); bucket by top keyword first so
    # only messages that could plausibly overlap get compared at all.
    recent_msgs = [
        e for e in interactions
        if e.get("type") == "user_message"
    ]
    buckets: dict[str, list[int]] = {}
    for i, e in enumerate(recent_msgs):
        for kw in e.get("keywords", [])[:3]:
            buckets.setdefault(kw, []).append(i)

    repetition_groups: list[list[int]] = []
    seen_pairs: set[tuple[int, int]] = set()
    for indices in buckets.values():
        for bi in range(len(indices)):
            for bj in range(bi + 1, len(indices)):
                i, j = indices[bi], indices[bj]
                pair = (i, j)
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                score = _similarity_score(
                    recent_msgs[i].get("content", ""),
                    recent_msgs[j].get("content", ""),
                )
                if score > 0.6:  # similar enough
                    repetition_groups.append([i, j])

    # ── Build pattern entries ──
    new_patterns = []

    # Top tools used
    for tool, count in sorted(tool_counts.items(), key=lambda x: -x[1]):
        if count >= 2:
            existing_match = next(
                (p for p in existing_patterns
                 if p.get("type") == "frequent_tool" and p.get("tool") == tool),
                None,
            )
            if existing_match:
                existing_match["count"] = count
                existing_match["updated_at"] = time.time()
            else:
                new_patterns.append({
                    "type": "frequent_tool",
                    "tool": tool,
                    "count": count,
                    "detected_at": time.time(),
                    "updated_at": time.time(),
                })

    # Repeated keyword clusters (topics)
    top_keywords = sorted(keyword_freq.items(), key=lambda x: -x[1])[:10]
    for kw, count in top_keywords:
        if count >= 2:
            existing_match = next(
                (p for p in existing_patterns
                 if p.get("type") == "topic" and p.get("keyword") == kw),
                None,
            )
            if existing_match:
                existing_match["count"] = count
                existing_match["updated_at"] = time.time()
            else:
                new_patterns.append({
                    "type": "topic",
                    "keyword": kw,
                    "count": count,
                    "detected_at": time.time(),
                    "updated_at": time.time(),
                })

    # Tool sequence patterns
    for seq, count in sorted(task_patterns.items(), key=lambda x: -x[1]):
        if count >= 2:
            existing_match = next(
                (p for p in existing_patterns
                 if p.get("type") == "tool_sequence" and p.get("sequence") == seq),
                None,
            )
            if existing_match:
                existing_match["count"] = count
                existing_match["updated_at"] = time.time()
            else:
                new_patterns.append({
                    "type": "tool_sequence",
                    "sequence": seq,
                    "count": count,
                    "detected_at": time.time(),
                    "updated_at": time.time(),
                })

    # Repetition patterns
    if len(repetition_groups) >= 2:
        rep_keywords = _extract_keywords(
            recent_msgs[repetition_groups[0][0]].get("content", "")
        )
        pattern_id = "_".join(rep_keywords[:3])
        existing_match = next(
            (p for p in existing_patterns
             if p.get("type") == "repetition" and p.get("id") == pattern_id),
            None,
        )
        if existing_match:
            existing_match["count"] = len(repetition_groups)
            existing_match["updated_at"] = time.time()
        else:
            new_patterns.append({
                "type": "repetition",
                "id": pattern_id,
                "keywords": rep_keywords[:5],
                "count": len(repetition_groups),
                "detected_at": time.time(),
                "updated_at": time.time(),
            })

    # Merge new patterns with existing
    merged = existing_patterns + new_patterns
    # Deduplicate by (type, identifier)
    seen: set[str] = set()
    deduped = []
    for p in merged:
        key = _pattern_key(p)
        if key not in seen:
            seen.add(key)
            deduped.append(p)

    result = {
        "patterns": deduped,
        "analyzed_at": time.time(),
        "total_interactions": len(interactions),
        "tool_counts": tool_counts,
    }
    _save_json(PATTERNS_FILE, result)
    return result


def _pattern_key(p: dict) -> str:
    """Generate a unique key for a pattern dict."""
    t = p.get("type", "")
    if t == "frequent_tool":
        return f"tool:{p.get('tool')}"
    elif t == "topic":
        return f"topic:{p.get('keyword')}"
    elif t == "tool_sequence":
        return f"seq:{p.get('sequence')}"
    elif t == "repetition":
        return f"rep:{p.get('id')}"
    return f"other:{hashlib.md5(json.dumps(p, sort_keys=True).encode()).hexdigest()}"


def get_patterns() -> list[dict]:
    """Get all detected patterns."""
    data = _load_json(PATTERNS_FILE, {"patterns": []})
    return data.get("patterns", [])


def get_ready_to_generate_skill() -> list[dict]:
    """
    Return patterns that have crossed the threshold for skill generation.
    """
    patterns = get_patterns()
    ready = []
    for p in patterns:
        count = p.get("count", 0)
        if count >= PATTERN_TO_SKILL_THRESHOLD:
            # Check if we already generated a skill for this pattern
            skill_name = _pattern_to_skill_name(p)
            skill_path = SKILLS_DIR / f"{skill_name}.md"
            if not skill_path.exists():
                ready.append(p)
    return ready


# ═══════════════════════════════════════════════════════════════════════
#  Skill Generation
# ═══════════════════════════════════════════════════════════════════════

def _pattern_to_skill_name(pattern: dict) -> str:
    """Convert a pattern to a skill filename (kebab-case)."""
    ptype = pattern.get("type", "unknown")
    if ptype == "frequent_tool":
        return f"frequent-{pattern.get('tool', 'tool')}"
    elif ptype == "topic":
        return f"topic-{pattern.get('keyword', 'keyword')}"
    elif ptype == "tool_sequence":
        seq = pattern.get("sequence", "tool-pair")
        return f"sequence-{seq.replace(' → ', '-')}"
    elif ptype == "repetition":
        kws = pattern.get("keywords", ["pattern"])
        return f"pattern-{'-'.join(kws[:3])}"
    return f"pattern-{int(time.time())}"


def generate_skill_for_pattern(pattern: dict) -> Optional[str]:
    """
    Generate a SKILL.md file for a detected pattern.
    Returns the path to the created file, or None if skipped.
    """
    skill_name = _pattern_to_skill_name(pattern)
    skill_file = SKILLS_DIR / f"{skill_name}.md"

    if skill_file.exists():
        return None

    ptype = pattern.get("type")
    count = pattern.get("count", 0)

    if ptype == "frequent_tool":
        tool = pattern.get("tool", "unknown")
        content = f"""---
type: auto-generated-skill
name: {tool}-usage
description: Guidelines for using the {tool} tool effectively
generated: {time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
pattern: frequent_tool
count: {count}
---

# Using `{tool}` Effectively

*This skill was auto-generated because `{tool}` has been used {count} times in this session.*

## Best Practices

- Always verify the path before calling `{tool}`.
- Use `{tool}` with clear, specific parameters.
- Check the result of `{tool}` before proceeding to the next step.
"""

    elif ptype == "topic":
        kw = pattern.get("keyword", "topic")
        content = f"""---
type: auto-generated-skill
name: topic-{kw}
description: User frequently asks about {kw}
generated: {time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
pattern: topic
count: {count}
---

# Topic: {kw}

*This skill was auto-generated because the topic "{kw}" appeared {count} times.*

## Guidelines

- When the user asks about "{kw}", be thorough and specific.
- Provide concrete examples related to {kw}.
- Reference previous solutions involving {kw} when relevant.
"""

    elif ptype == "tool_sequence":
        seq = pattern.get("sequence", "tool-a → tool-b")
        content = f"""---
type: auto-generated-skill
name: sequence-{seq[:30]}
description: Common tool sequence detected: {seq}
generated: {time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
pattern: tool_sequence
count: {count}
---

# Tool Sequence: {seq}

*This skill was auto-generated because the sequence "{seq}" was used {count} times.*

## Guidelines

- When starting with `{seq.split(' → ')[0]}`, consider following up with `{seq.split(' → ')[1]}`.
- This sequence is efficient for the following scenarios:
  - (List scenarios based on context)
"""

    elif ptype == "repetition":
        kws = pattern.get("keywords", ["task"])
        content = f"""---
type: auto-generated-skill
name: pattern-{'-'.join(kws[:2])}
description: Repeated task pattern: {', '.join(kws[:4])}
generated: {time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
pattern: repetition
count: {count}
---

# Task Pattern: {', '.join(kws[:4])}

*This skill was auto-generated because this type of task was repeated {count} times.*

## Approach

1. Understand the specific requirements for this task type.
2. Follow the established pattern from previous iterations.
3. Optimise the process based on past feedback.

## Common Pitfalls

- (Detected from user corrections) — will be filled as more data is collected.
"""

    else:
        return None

    _ensure_dirs()
    skill_file.write_text(content, encoding="utf-8")

    # Also register in the skills directory so the agent can find it
    # We create a symlink or copy to the user's skills directory
    _register_skill(skill_name, skill_file)

    return str(skill_file)


def _register_skill(skill_name: str, skill_file: Path) -> None:
    """
    Bookkeeping only. The skill file itself already lives under SKILLS_DIR
    (== skills_manager.LEARNED_SKILLS_DIR), which skills_manager.SKILL_DIRS
    scans directly — that's what makes it reach build_skills_section() and
    the system prompt. This registry is a separate list (skill-registry.json)
    used only to report counts on the `/si` status screen.
    """
    registry = SELF_IMPROVE_DIR / "skill-registry.json"
    registry_data = _load_json(registry, {"skills": []})
    # Avoid duplicates
    for s in registry_data["skills"]:
        if s.get("name") == skill_name:
            s["path"] = str(skill_file)
            _save_json(registry, registry_data)
            return
    registry_data["skills"].append({
        "name": skill_name,
        "path": str(skill_file),
        "generated": time.time(),
    })
    _save_json(registry, registry_data)


def get_auto_generated_skills() -> list[dict]:
    """Return list of auto-generated skills."""
    registry = _load_json(SELF_IMPROVE_DIR / "skill-registry.json", {"skills": []})
    return registry.get("skills", [])


def generate_skills_for_all_ready_patterns() -> list[str]:
    """
    Generate skills for all patterns that have crossed the threshold.
    Returns list of created skill file paths.
    """
    ready = get_ready_to_generate_skill()
    created = []
    for pattern in ready:
        path = generate_skill_for_pattern(pattern)
        if path:
            created.append(path)
    return created


# ═══════════════════════════════════════════════════════════════════════
#  Tip Generation
# ═══════════════════════════════════════════════════════════════════════

def generate_tips() -> list[dict]:
    """
    Analyze patterns and generate self-improvement tips.
    Returns list of tip dicts.
    """
    patterns = get_patterns()
    interactions = get_recent_interactions(ANALYSIS_WINDOW)
    tips = _load_json(TIPS_FILE, {"tips": [], "generated_at": 0})

    new_tips: list[dict] = []

    # ── Tool usage tips ──
    tool_counts: dict[str, int] = {}
    for p in patterns:
        if p.get("type") == "frequent_tool":
            tool_counts[p["tool"]] = p.get("count", 0)

    if tool_counts:
        most_used = max(tool_counts, key=tool_counts.get)
        new_tips.append({
            "type": "tool_usage",
            "category": "efficiency",
            "message": f"You frequently use `{most_used}` ({tool_counts[most_used]}×). "
                       f"Consider creating shortcuts or aliases for this tool.",
            "generated_at": time.time(),
            "applied": False,
        })

    # ── Topic tips ──
    topic_patterns = [p for p in patterns if p.get("type") == "topic"]
    if len(topic_patterns) >= 3:
        topics = [p["keyword"] for p in topic_patterns[:5]]
        new_tips.append({
            "type": "topic_cluster",
            "category": "focus",
            "message": f"The user often asks about: {', '.join(topics)}. "
                       f"Keep relevant context ready for these topics.",
            "generated_at": time.time(),
            "applied": False,
        })

    # ── Repetition tips ──
    rep_patterns = [p for p in patterns if p.get("type") == "repetition"]
    if rep_patterns:
        total_reps = sum(p.get("count", 0) for p in rep_patterns)
        if total_reps >= 3:
            new_tips.append({
                "type": "repetition_warning",
                "category": "learning",
                "message": f"Detected {total_reps} similar request groups. "
                           f"Consider creating a reusable skill for this task type.",
                "generated_at": time.time(),
                "applied": False,
            })

    # ── Tool sequence optimisation tip ──
    seq_patterns = [p for p in patterns if p.get("type") == "tool_sequence"]
    if seq_patterns:
        top_seq = max(seq_patterns, key=lambda p: p.get("count", 0))
        new_tips.append({
            "type": "sequence_optimisation",
            "category": "efficiency",
            "message": f"Common workflow: {top_seq.get('sequence')} ({top_seq.get('count')}×). "
                       f"Consider batching these operations.",
            "generated_at": time.time(),
            "applied": False,
        })

    # ── Session duration tip ──
    if interactions:
        first_ts = interactions[0].get("timestamp", time.time())
        session_duration = time.time() - first_ts
        if session_duration > 1800:  # 30 minutes
            new_tips.append({
                "type": "session_duration",
                "category": "productivity",
                "message": f"Session running for {session_duration / 60:.0f} minutes. "
                           f"Consider compacting conversation or taking a summary break.",
                "generated_at": time.time(),
                "applied": False,
            })

    # Merge with existing tips
    all_tips = tips.get("tips", []) + new_tips
    # Deduplicate by message
    seen_messages: set[str] = set()
    deduped = []
    for t in all_tips:
        if t["message"] not in seen_messages:
            seen_messages.add(t["message"])
            deduped.append(t)

    # Keep only last 20 tips
    deduped = deduped[-20:]

    result = {
        "tips": deduped,
        "generated_at": time.time(),
        "patterns_analyzed": len(patterns),
        "interactions_analyzed": len(interactions),
    }
    _save_json(TIPS_FILE, result)
    return deduped


def get_tips() -> list[dict]:
    """Get all generated tips."""
    data = _load_json(TIPS_FILE, {"tips": []})
    return data.get("tips", [])


def get_active_tips() -> list[dict]:
    """Get tips that haven't been applied yet."""
    return [t for t in get_tips() if not t.get("applied")]


def mark_tip_applied(tip_index: int) -> bool:
    """Mark a tip as applied."""
    data = _load_json(TIPS_FILE, {"tips": []})
    tips = data.get("tips", [])
    if 0 <= tip_index < len(tips):
        tips[tip_index]["applied"] = True
        data["tips"] = tips
        _save_json(TIPS_FILE, data)
        return True
    return False


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
    # Create initial session analysis
    if not SESSION_FILE.exists():
        update_session_analysis()


def record_user_message(content: str, msg_type: str = "text") -> None:
    """Log a user message. Analysis is triggered separately, after the turn
    completes (see maybe_analyze_after_turn) — not on this hot path."""
    log_user_message(content, msg_type)


def record_tool_call(tool_name: str, args: dict, result_preview: str = "") -> None:
    """Record a tool call."""
    log_tool_call(tool_name, args, result_preview)


def maybe_analyze_after_turn() -> None:
    """Call once per user turn, after the reply has been delivered, so
    analysis never adds to response latency."""
    _maybe_analyze(get_recent_interactions(ANALYSIS_WINDOW))


def _maybe_analyze(interactions: list[dict]) -> None:
    """Periodically run pattern analysis and skill generation.

    Counts user turns explicitly rather than raw interaction count: the log
    also contains tool_call entries, so a turn with several tool calls could
    jump the raw count past a multiple of ANALYZE_INTERVAL and silently skip
    analysis for that stretch.
    """
    user_turns = sum(1 for i in interactions if i.get("type") == "user_message")
    if user_turns == 0 or user_turns % ANALYZE_INTERVAL != 0:
        return

    # Analyze patterns
    analyze_patterns()

    # Generate skills for patterns that crossed threshold
    created = generate_skills_for_all_ready_patterns()
    if created:
        print(f'  {GREEN}✦{RESET} Auto-generated {len(created)} skill(s):')
        for path in created:
            print(f'    {DIM}📄{RESET} {Path(path).name}')

    # Generate tips
    generate_tips()

    # Update session analysis
    update_session_analysis()


def get_self_improve_status() -> str:
    """Return a human-readable status of the self-improvement system."""
    interactions = get_all_interactions()
    patterns = get_patterns()
    tips = get_tips()
    skills = get_auto_generated_skills()
    session = get_session_analysis()

    lines = [
        f'  {BOLD}Self-Improvement System{RESET}',
        f'  {DIM}{"─" * 46}{RESET}',
        f'  {CYAN}◈{RESET}  Interactions: {len(interactions)}',
        f'  {CYAN}◈{RESET}  Patterns:     {len(patterns)}',
        f'  {CYAN}◈{RESET}  Tips:         {len(tips)} ({len(get_active_tips())} active)',
        f'  {CYAN}◈{RESET}  Auto-skills:  {len(skills)}',
        '',
        f'  {BOLD}Session Analysis{RESET}',
        f'  {DIM}Purpose:{RESET}   {session.get("purpose", "unknown")}',
        f'  {DIM}Complexity:{RESET} {session.get("complexity", "unknown")}',
        f'  {DIM}Stage:{RESET}     {session.get("stage", "unknown")}',
        f'  {DIM}Keywords:{RESET}  {", ".join(session.get("keywords", [])[:6])}',
    ]

    if patterns:
        lines.append('')
        lines.append(f'  {BOLD}Top Patterns{RESET}')
        for p in sorted(patterns, key=lambda x: -x.get("count", 0))[:5]:
            ptype = p.get("type", "?")
            pcount = p.get("count", 0)
            if ptype == "frequent_tool":
                lines.append(f'    {DIM}🔧{RESET} {p.get("tool")} ({pcount}×)')
            elif ptype == "topic":
                lines.append(f'    {DIM}📌{RESET} topic: {p.get("keyword")} ({pcount}×)')
            elif ptype == "tool_sequence":
                lines.append(f'    {DIM}🔗{RESET} {p.get("sequence")} ({pcount}×)')
            elif ptype == "repetition":
                kws = ", ".join(p.get("keywords", [])[:3])
                lines.append(f'    {DIM}🔁{RESET} repeated: {kws} ({pcount}×)')

    if tips:
        lines.append('')
        lines.append(f'  {BOLD}Active Tips{RESET}')
        active = get_active_tips()
        if active:
            for i, t in enumerate(active[:5]):
                lines.append(f'    {DIM}💡{RESET} {t.get("message", "")}')
        else:
            lines.append(f'    {DIM}No active tips.{RESET}')

    if skills:
        lines.append('')
        lines.append(f'  {BOLD}Generated Skills{RESET}')
        for s in skills:
            lines.append(f'    {DIM}📄{RESET} {s.get("name", "?")}')

    return '\n'.join(lines)


# Patch for ANSI codes (used by _maybe_analyze output)
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
