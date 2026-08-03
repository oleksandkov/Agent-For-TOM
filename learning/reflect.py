"""
Reflection — the model is the learner.

A keyword counter cannot produce an insight: it has no access to what was
said, what went wrong, or what the user actually wanted. A language model —
which is already in the loop — can read the transcript and say exactly that.

One cheap call per session, at session end, off the hot path. Every failure
mode returns {}: learning must never break or delay the user's work.

Modes (TOMAS_REFLECT):
    shadow   run and log what WOULD be learned; write nothing. The default,
             so you can read real output before trusting it.
    active   also record observations, subject to the evidence gate.
    off      do not run at all.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Callable, Optional

from .corrections import detect_correction_signals, render_signals
from .promotion import record_observation
from .store import REFLECTION_LOG, SKILLS_DIR, append_jsonl, redact

MIN_MESSAGES = 4
MAX_TRANSCRIPT_CHARS = 20_000
MIN_CONFIDENCE = 0.5

REFLECTION_PROMPT = """You are reviewing a completed session between a user and
an AI agent, to learn how to serve THIS user better next time.

Report only what the transcript actually supports. Do not invent preferences
from a single ambiguous exchange. An empty list is the correct answer when
nothing was learned — most sessions teach nothing, and that is fine.

Return JSON only:
{
  "user_preferences": [
    {"fact": "<durable, specific, about the user or their environment>",
     "confidence": 0.0-1.0,
     "evidence": "<what in the transcript supports this>"}
  ],
  "corrections": [
    {"what_i_did": "...", "what_was_wanted": "...", "lesson": "<one actionable sentence>"}
  ],
  "skill_candidates": [
    {"name": "kebab-case-name",
     "trigger": "<when this should apply>",
     "body": "<concrete guidance, specific to this user's actual workflow>"}
  ],
  "project_notes": [
    {"fact": "<true of this codebase, not of the user>", "evidence": "..."}
  ]
}"""


def mode() -> str:
    value = os.environ.get("TOMAS_REFLECT", "shadow").strip().lower()
    return value if value in ("shadow", "active", "off") else "shadow"


def cheapest_available_model(current_model: str) -> str:
    """Reflection is a summarisation task — it does not need the session model.

    An unknown family reuses the session model rather than guessing a name
    the provider may not serve. A wrong guess is harmless anyway (the call
    fails and reflection returns {}), but pointless.
    """
    explicit = os.environ.get("TOMAS_REFLECTION_MODEL", "").strip()
    if explicit:
        return explicit
    name = (current_model or "").lower()
    if "claude" in name:
        return "claude-haiku-4-5"
    if "gemini" in name:
        return "gemini-3.5-flash-lite"
    return current_model


def render_transcript(messages: list, max_chars: int = MAX_TRANSCRIPT_CHARS) -> str:
    """Flatten a transcript to plain text, secrets stripped.

    Keeps the tail: the end of a session is where corrections and conclusions
    live, and it is what the reflection is about.
    """
    lines = []
    for msg in messages:
        role = msg.get("role", "?")
        content = msg.get("content")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "tool_result":
                        inner = block.get("content")
                        parts.append(f"[tool result] {inner if isinstance(inner, str) else ''}")
                    elif block.get("type") == "text":
                        parts.append(block.get("text", ""))
                else:
                    btype = getattr(block, "type", "")
                    if btype == "text":
                        parts.append(getattr(block, "text", ""))
                    elif btype == "tool_use":
                        parts.append(f"[calls {getattr(block, 'name', '?')}]")
            text = "\n".join(p for p in parts if p)
        else:
            text = ""
        if text.strip():
            lines.append(f"{role.upper()}: {text.strip()}")

    transcript = "\n\n".join(lines)
    if len(transcript) > max_chars:
        transcript = "[...earlier turns omitted...]\n\n" + transcript[-max_chars:]
    return redact(transcript)


def extract_json(text: str) -> str:
    """Pull the JSON object out of a reply that may be fenced or prefaced."""
    if not text:
        return "{}"
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        return fenced.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        return text[start:end + 1]
    return "{}"


def reflect_on_session(messages: list,
                       call_model: Optional[Callable] = None,
                       model: Optional[str] = None) -> dict:
    """One cheap API call per session. Returns {} on any failure."""
    if not messages or len(messages) < MIN_MESSAGES or call_model is None:
        return {}
    try:
        transcript = render_transcript(messages)
        if not transcript.strip():
            return {}

        signals = detect_correction_signals(messages)
        hint = render_signals(signals)
        user_content = f"{hint}\n\n---\n\n{transcript}" if hint else transcript

        reply = call_model(
            model=model or cheapest_available_model(os.environ.get("AGENT_MODEL", "")),
            system=REFLECTION_PROMPT,
            messages=[{"role": "user", "content": user_content}],
            max_tokens=1500,
        )
        parsed = json.loads(extract_json(reply))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


# ═══════════════════════════════════════════════════════════════════════
#  Applying what was learned
# ═══════════════════════════════════════════════════════════════════════

def _skill_body(name: str, trigger: str, body: str) -> str:
    return f"# {name}\n\n**Apply when:** {trigger}\n\n{body}\n"


def write_learned_skill(name: str, trigger: str, body: str) -> Optional[str]:
    """Write — or improve — a skill the agent learned.

    Uses the one skill format (`skills_manager.write_skill`), so installing a
    skill, generating one, and improving one already present are the same
    code path. A skill that already exists is *extended* with a version bump
    rather than overwritten, which is what keeps provenance across sessions.
    """
    safe = re.sub(r"[^a-z0-9\-]+", "-", (name or "").lower()).strip("-")
    if not safe:
        return None
    trigger, body = redact(trigger), redact(body)
    try:
        import skills_manager
    except Exception:
        return None

    triggers = [w for w in re.findall(r"[a-z0-9\-]{3,}", trigger.lower())][:6]
    path = SKILLS_DIR / f"{safe}.md"
    try:
        if path.exists():
            improved = skills_manager.improve_skill(
                safe, body, description=trigger, triggers=triggers)
            return str(improved) if improved else None
        SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        return str(skills_manager.write_skill(
            path,
            {"name": safe, "description": trigger, "triggers": triggers,
             "source": "learned", "version": 1},
            _skill_body(safe, trigger, body)))
    except OSError:
        return None


def apply_reflection(result: dict) -> list[str]:
    """Record what reflection found. Returns summaries of anything promoted.

    Everything goes through the evidence gate, so a single session can never
    make a permanent rule — including skill candidates, which only become real
    skill files once the same candidate has recurred.
    """
    promoted: list[str] = []
    if not isinstance(result, dict):
        return promoted

    for item in result.get("user_preferences") or []:
        if not isinstance(item, dict):
            continue
        if float(item.get("confidence") or 0) < MIN_CONFIDENCE:
            continue
        _, was_promoted = record_observation(
            "preference", item.get("fact", ""), item.get("evidence", ""), "global")
        if was_promoted:
            promoted.append(item.get("fact", ""))

    for item in result.get("corrections") or []:
        if not isinstance(item, dict):
            continue
        lesson = item.get("lesson", "")
        evidence = f"did: {item.get('what_i_did', '')} / wanted: {item.get('what_was_wanted', '')}"
        _, was_promoted = record_observation("correction", lesson, evidence, "global")
        if was_promoted:
            promoted.append(lesson)

    for item in result.get("project_notes") or []:
        if not isinstance(item, dict):
            continue
        _, was_promoted = record_observation(
            "project", item.get("fact", ""), item.get("evidence", ""), "project")
        if was_promoted:
            promoted.append(item.get("fact", ""))

    for item in result.get("skill_candidates") or []:
        if not isinstance(item, dict):
            continue
        name = item.get("name", "")
        trigger = item.get("trigger", "")
        if not name or not trigger:
            continue
        _, was_promoted = record_observation(
            "skill_candidate", f"{name}: {trigger}",
            evidence="proposed by reflection", scope="global",
            extra={"skill": {"name": name, "trigger": trigger,
                             "body": item.get("body", "")}})
        if was_promoted:
            if write_learned_skill(name, trigger, item.get("body", "")):
                promoted.append(f"skill: {name}")

    return promoted


def run_session_reflection(messages: list,
                           call_model: Optional[Callable] = None,
                           model: Optional[str] = None) -> dict:
    """Session-end entry point. Never raises, never blocks the user."""
    current = mode()
    if current == "off":
        return {}
    try:
        result = reflect_on_session(messages, call_model=call_model, model=model)
        if not result:
            return {}

        promoted = apply_reflection(result) if current == "active" else []
        append_jsonl(REFLECTION_LOG, {
            "at": time.time(),
            "mode": current,
            "messages": len(messages),
            "signals": len(detect_correction_signals(messages)),
            "result": result,
            "promoted": promoted,
        })
        return {"result": result, "promoted": promoted, "mode": current}
    except Exception:
        return {}
