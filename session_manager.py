"""
Session Manager — saves, loads, lists, and continues agent sessions.

Sessions are stored under ~/.tomas/sessions/ as individual JSON files.
Each file contains the full conversation history, metadata, and a summary
so the agent can pick up where it left off.
"""

from __future__ import annotations

import json
import os
import re
import time
import hashlib
from pathlib import Path
from typing import Any, Optional

# ── Constants ──────────────────────────────────────────────────────────

TOMAS_DIR = Path.home() / ".tomas"
SESSION_DIR = TOMAS_DIR / "sessions"
MAX_SESSIONS = 50  # auto-cleanup oldest when exceeding this


# ═══════════════════════════════════════════════════════════════════════
#  JSON encoder for Anthropic SDK types
# ═══════════════════════════════════════════════════════════════════════

class SessionJSONEncoder(json.JSONEncoder):
    """Custom JSON encoder that handles Anthropic SDK types (pydantic BaseModel).

    The agent loop stores assistant response content as pydantic BaseModel
    instances (TextBlock, ToolUseBlock, ThinkingBlock, etc.) which have a
    ``model_dump()`` method returning a plain dict.
    """

    def default(self, obj: Any) -> Any:
        if hasattr(obj, "model_dump"):
            return obj.model_dump()
        # Fallback for anything else unexpected
        try:
            return repr(obj)
        except Exception:
            return f"<unserializable: {type(obj).__name__}>"


def _serialize_session_data(data: dict) -> str:
    """Serialize session data to JSON, handling Anthropic SDK types.

    Uses the custom SessionJSONEncoder and also pre-processes the
    messages list to ensure full compatibility.
    """
    return json.dumps(data, cls=SessionJSONEncoder, indent=2, ensure_ascii=False)


# ═══════════════════════════════════════════════════════════════════════
#  Path helpers
# ═══════════════════════════════════════════════════════════════════════

def get_session_dir() -> Path:
    """Return the sessions directory, creating it if needed."""
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    return SESSION_DIR


def _session_path(session_id: str) -> Path:
    """Return the full path to a session file."""
    return get_session_dir() / f"{session_id}.json"


# ═══════════════════════════════════════════════════════════════════════
#  Session data helpers
# ═══════════════════════════════════════════════════════════════════════

def _generate_session_id() -> str:
    """Generate a unique session ID based on current time."""
    ts = time.strftime("%Y%m%d_%H%M%S", time.localtime())
    rand = hashlib.md5(str(time.time_ns()).encode()).hexdigest()[:6]
    return f"{ts}_{rand}"


def _summarize_messages(messages: list) -> str:
    """Auto-generate a short summary from the conversation messages.

    Looks at the first user message and the last assistant response,
    handling both plain text and list-based content (TextBlock, ToolUseBlock).
    """
    first_user = ""
    last_assistant = ""

    for m in messages:
        role = m.get("role")
        content = m.get("content")

        if role == "user" and isinstance(content, str):
            if not first_user:
                first_user = content[:200]

        elif role == "assistant" and not last_assistant:
            if isinstance(content, str):
                last_assistant = content[:200]
            elif isinstance(content, list):
                texts = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            texts.append(block.get("text", ""))
                        elif block.get("type") == "tool_use":
                            texts.append(f"[tool: {block.get('name', '?')}]")
                    elif hasattr(block, "type"):
                        if getattr(block, "type", "") == "text":
                            texts.append(getattr(block, "text", ""))
                        elif getattr(block, "type", "") == "tool_use":
                            texts.append(f"[tool: {getattr(block, 'name', '?')}]")
                combined = " ".join(texts)[:200]
                if combined:
                    last_assistant = combined

    parts = []
    if first_user:
        parts.append(f"Started: {first_user}")
    if last_assistant:
        parts.append(f"Last: {last_assistant}")
    return " | ".join(parts) if parts else "Empty session"


# ═══════════════════════════════════════════════════════════════════════
#  CRUD operations
# ═══════════════════════════════════════════════════════════════════════

#: Phrases that promise the next step rather than report a finished one.
#: Kept in step with `core.loop._ANNOUNCEMENT_RE`, but duplicated rather than
#: imported: this module is the *record* of what happened and must be able to
#: audit a transcript it did not run, including one written by an older build.
_ANNOUNCEMENT_RE = re.compile(
    r"(?:^|[\s\"'(«])(?:"
    r"let me\s+(?!know|have)\w+|now\s+(?:i'?ll|let me|i\s+will)|"
    r"i'?ll\s+(?:now\s+)?(?!know)\w+|"
    r"i\s+will\s+now|next[,\s]+i'?ll|let'?s\s+(?:now\s+)?\w+|"
    r"зараз\s+я|тепер\s+я|далі\s+я|перейду\s+до|почну\s+з|"
    r"давайте\s+\w+|створю|побудую|запущу|сформую"
    r")\b",
    re.IGNORECASE)


def _final_text(messages: list) -> str:
    """The text of the last assistant turn, if it is text-only."""
    for message in reversed(messages):
        if message.get("role") != "assistant":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            if any(isinstance(b, dict) and b.get("type") == "tool_use"
                   for b in content):
                return ""
            return "\n".join(b.get("text", "") for b in content
                             if isinstance(b, dict) and b.get("type") == "text")
        return ""
    return ""


def audit_transcript(messages: list) -> dict:
    """Report whether a transcript actually completed.

    A user turn with no assistant reply after it means the turn produced
    nothing — the model errored, retries were exhausted, or the harness moved
    on. Saving that as an ordinary session is how eight prompts and zero
    replies came to be reported as eight turns of finished work.

    The second question is newer and cost more: a turn can end with a
    perfectly well-formed assistant reply that *announces* the next step
    instead of taking it. Measured on `hy3-free`, twice in one session —
    "I now fully understand the workflow. Let me create the target directory
    and build the content plan JSON…" — saved `complete: true`,
    `failed_turns: []`, and not one file written. Nothing here could see it,
    because the reply exists and reads like work.
    """
    roles = [m.get("role") for m in messages]
    orphaned = [
        i for i, r in enumerate(roles)
        if r == "user" and (i + 1 >= len(roles) or roles[i + 1] == "user")
    ]
    tail = _final_text(messages)
    announced = bool(tail) and bool(_ANNOUNCEMENT_RE.search(tail.strip()[-400:]))
    return {
        "complete": not orphaned and not announced,
        "orphaned_user_turns": orphaned,
        "ended_on_announcement": announced,
        "user_messages": roles.count("user"),
        "assistant_messages": roles.count("assistant"),
    }


def backfill_completeness() -> list[str]:
    """Mark pre-Phase-6 sessions that were saved without a `complete` flag.

    Sessions written before this flag existed carry no signal at all, so a
    reader cannot distinguish an abandoned run from a finished one. Files
    that audit clean are left untouched; only genuinely incomplete ones are
    annotated. Safe to call repeatedly.

    A stored `complete: true` is also re-examined, because the audit gets
    stricter over time and the corpus is the evidence later analysis is done
    on. When `ended_on_announcement` was added, one session on disk flipped:
    saved as a clean success, it had in fact ended by describing the step it
    was about to take and written no file at all. Leaving it marked finished
    would have kept that failure invisible in exactly the record used to
    find it. A stored `complete: false` is never overturned — this only ever
    demotes.
    """
    marked: list[str] = []
    for path in sorted(get_session_dir().glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("complete") is False:
            continue
        audit = audit_transcript(data.get("messages", []))
        if audit["complete"]:
            continue
        note = ("backfilled: saved before completeness was recorded"
                if "complete" not in data else
                "re-audited: saved as complete under an earlier, looser check")
        data["complete"] = False
        data["incomplete_reason"] = {
            "orphaned_user_turns": audit["orphaned_user_turns"],
            "ended_on_announcement": audit["ended_on_announcement"],
            "user_messages": audit["user_messages"],
            "assistant_messages": audit["assistant_messages"],
            "failed_turns": [],
            "note": note,
        }
        path.write_text(_serialize_session_data(data), encoding="utf-8")
        marked.append(path.stem)
    return marked


def save_session(
    messages: list,
    summary: str = "",
    model: str = "",
    token_usage: dict | None = None,
    session_id: str | None = None,
    telemetry: dict | None = None,
) -> str:
    """Save the current conversation as a session.

    Args:
        messages: The conversation messages list.
        summary: Optional short description. Auto-generated if empty.
        model: The model used during the session.
        token_usage: Dict with input/output/calls counts.
        session_id: Optional existing session ID to overwrite.
            If provided, updates the existing session file
            instead of creating a new one.
        telemetry: Optional dict with turn_metrics / tool_log / failed_turns.
            Defaults to the live session's telemetry from `agent`.

    Returns:
        The session ID string.
    """
    get_session_dir()  # ensure dir exists

    session_id = session_id or _generate_session_id()
    if not summary:
        summary = _summarize_messages(messages)

    # Get project info
    try:
        from agent import PROJECT_DIR
        project = PROJECT_DIR.name
        project_dir = str(PROJECT_DIR)
    except (ImportError, Exception):
        project = Path.cwd().name
        project_dir = str(Path.cwd())

    if not model:
        model = os.environ.get("AGENT_MODEL", "unknown")

    if token_usage is None:
        try:
            from agent import _session_tokens
            token_usage = dict(_session_tokens)
        except (ImportError, Exception):
            token_usage = {"input": 0, "output": 0, "calls": 0}

    if telemetry is None:
        try:
            from agent import session_telemetry
            telemetry = session_telemetry()
        except (ImportError, Exception):
            telemetry = {}

    audit = audit_transcript(messages)
    # A turn the agent recorded as failed also makes the session incomplete,
    # even when a later turn produced a reply.
    failed_turns = list(telemetry.get("failed_turns") or [])
    complete = audit["complete"] and not failed_turns

    session_data = {
        "id": session_id,
        "timestamp": time.time(),
        "timestamp_str": time.strftime(
            "%Y-%m-%d %H:%M:%S", time.localtime()
        ),
        "project": project,
        "project_dir": project_dir,
        "model": model,
        "message_count": len(messages),
        "complete": complete,
        "token_usage": token_usage,
        "summary": summary,
        "turn_metrics": telemetry.get("turn_metrics", {}),
        "tool_log": telemetry.get("tool_log", []),
        # Explicitly listed keys, so anything session_telemetry() grows has to
        # be added here too or it never reaches disk. context_events was
        # produced correctly for a whole sweep and silently dropped at this
        # line, which made compaction unobservable in exactly the reports it
        # was added for.
        "context_events": telemetry.get("context_events", []),
        # Recorded whether or not the session is complete. Nesting this inside
        # incomplete_reason meant a finished session threw away the fact that
        # some of its turns produced nothing.
        "failed_turns": failed_turns,
        "low_content_turns": telemetry.get("low_content_turns", []),
        "messages": messages,
    }
    if not complete:
        session_data["incomplete_reason"] = {
            "orphaned_user_turns": audit["orphaned_user_turns"],
            "ended_on_announcement": audit["ended_on_announcement"],
            "user_messages": audit["user_messages"],
            "assistant_messages": audit["assistant_messages"],
            "failed_turns": failed_turns,
        }

    path = _session_path(session_id)
    path.write_text(
        _serialize_session_data(session_data),
        encoding="utf-8",
    )

    _cleanup_old_sessions()
    return session_id


def list_sessions(limit: int = 20) -> list[dict]:
    """Return a list of session summaries, newest first.

    Each entry contains: id, timestamp_str, project, model,
    message_count, summary, token_usage.
    Full messages are NOT included for performance.
    """
    session_dir = get_session_dir()
    sessions: list[dict] = []

    for f in sorted(session_dir.glob("*.json"), reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            # Strip messages for summary view
            summary = {
                "id": data.get("id", f.stem),
                "timestamp_str": data.get("timestamp_str", "?"),
                "timestamp": data.get("timestamp", 0),
                "project": data.get("project", "?"),
                "model": data.get("model", "?"),
                "message_count": data.get("message_count", 0),
                "summary": data.get("summary", ""),
                "token_usage": data.get("token_usage", {}),
            }
            sessions.append(summary)
        except (json.JSONDecodeError, OSError):
            continue

        if len(sessions) >= limit:
            break

    return sessions


def load_session(session_id: str) -> dict | None:
    """Load a full session by ID, including messages.

    Returns None if the session doesn't exist or is corrupt.
    """
    path = _session_path(session_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def delete_session(session_id: str) -> bool:
    """Delete a session file. Returns True on success."""
    path = _session_path(session_id)
    if path.exists():
        path.unlink()
        return True
    return False


def get_session_count() -> int:
    """Return the total number of saved sessions."""
    session_dir = get_session_dir()
    if not session_dir.exists():
        return 0
    return len(list(session_dir.glob("*.json")))


# ═══════════════════════════════════════════════════════════════════════
#  Cleanup
# ═══════════════════════════════════════════════════════════════════════

def _cleanup_old_sessions(max_sessions: int = MAX_SESSIONS):
    """Remove oldest sessions if exceeding the maximum."""
    session_dir = get_session_dir()
    files = sorted(session_dir.glob("*.json"))
    while len(files) > max_sessions:
        oldest = files[0]
        try:
            oldest.unlink()
        except OSError:
            pass
        files = sorted(session_dir.glob("*.json"))


def clear_all_sessions() -> int:
    """Delete all sessions. Returns the number deleted."""
    count = 0
    for f in get_session_dir().glob("*.json"):
        try:
            f.unlink()
            count += 1
        except OSError:
            pass
    return count


# ═══════════════════════════════════════════════════════════════════════
#  Continue session helpers
# ═══════════════════════════════════════════════════════════════════════

def continue_session(session_id: str) -> list | None:
    """Load a session's messages for continuing a conversation.

    Returns the messages list, or None if session not found.
    """
    data = load_session(session_id)
    if data is None:
        return None
    return data.get("messages", [])


def get_latest_session() -> dict | None:
    """Get the most recent session summary, or None."""
    sessions = list_sessions(limit=1)
    return sessions[0] if sessions else None
