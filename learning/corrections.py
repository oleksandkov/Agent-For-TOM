"""
Correction detection — mining the free signal.

When the user says "no, not like that", re-asks the same question, denies a
tool call, or the agent gets stuck retrying a failing tool, that is a labelled
training example: the agent did X, the user wanted Y. It is free, unambiguous,
and it is the most valuable data in the session.

These are heuristics that flag *candidates* only. The reflection pass decides
what the lesson actually is — feeding it these positions is what lets a small
model do the job, because it no longer has to find the interesting moments
itself.
"""

from __future__ import annotations

from typing import Optional

from .text import similarity

CORRECTION_MARKERS = [
    "no,", "not like that", "i meant", "i said", "wrong", "that's not",
    "thats not", "don't ", "dont ", "stop ", "actually,", "instead",
    "no need to", "why did you", "that's wrong", "not what i",
]

DENIAL_MARKER = "user denied this tool call"
REPEAT_THRESHOLD = 0.7
TOOL_ERROR_RUN = 2  # same tool failing this many times in a row


def _text_of(content) -> str:
    """User content may be a string or a list of blocks (tool results)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                inner = block.get("content") or block.get("text") or ""
                if isinstance(inner, str):
                    parts.append(inner)
        return "\n".join(parts)
    return ""


def previous_user_message(messages: list, index: int) -> Optional[str]:
    for j in range(index - 1, -1, -1):
        msg = messages[j]
        if msg.get("role") == "user" and isinstance(msg.get("content"), str):
            return msg["content"]
    return None


def _tool_name_for(messages: list, tool_use_id: str) -> str:
    """Walk back to the assistant turn that requested this tool_use."""
    for msg in messages:
        content = msg.get("content")
        if msg.get("role") != "assistant" or not isinstance(content, list):
            continue
        for block in content:
            block_id = getattr(block, "id", None) or (
                block.get("id") if isinstance(block, dict) else None)
            if block_id == tool_use_id:
                return (getattr(block, "name", None)
                        or (block.get("name") if isinstance(block, dict) else "")
                        or "")
    return ""


def detect_correction_signals(messages: list) -> list[dict]:
    """Flag turns where the user appears to have corrected the agent."""
    signals: list[dict] = []
    failing_streak: dict[str, int] = {}

    for i, msg in enumerate(messages):
        role = msg.get("role")
        content = msg.get("content")

        if role == "user" and isinstance(content, str):
            text = content.lower()

            if any(marker in text for marker in CORRECTION_MARKERS):
                signals.append({"kind": "explicit_correction", "index": i,
                                "text": content[:300]})

            previous = previous_user_message(messages, i)
            if previous and similarity(text, previous.lower()) > REPEAT_THRESHOLD:
                signals.append({"kind": "repeated_request", "index": i,
                                "text": content[:300]})

        # Tool results come back as a user turn carrying tool_result blocks.
        if role == "user" and isinstance(content, list):
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                result = block.get("content")
                result = result if isinstance(result, str) else _text_of(result)
                tool = _tool_name_for(messages, block.get("tool_use_id", ""))

                if DENIAL_MARKER in result.lower():
                    failing_streak.pop(tool, None)
                    signals.append({"kind": "permission_denied", "index": i,
                                    "tool": tool, "text": result[:300]})
                elif result.strip().lower().startswith("error"):
                    failing_streak[tool] = failing_streak.get(tool, 0) + 1
                    if failing_streak[tool] == TOOL_ERROR_RUN:
                        signals.append({"kind": "tool_error_loop", "index": i,
                                        "tool": tool, "text": result[:300]})
                else:
                    failing_streak.pop(tool, None)

    return signals


def render_signals(signals: list[dict], limit: int = 10) -> str:
    """Format signals as a hint for the reflection prompt."""
    if not signals:
        return ""
    lines = ["The user appears to have corrected the agent at these points — "
             "focus your analysis there:"]
    for signal in signals[:limit]:
        label = signal["kind"].replace("_", " ")
        tool = f" ({signal['tool']})" if signal.get("tool") else ""
        lines.append(f"- [{label}{tool}] {signal.get('text', '').strip()}")
    return "\n".join(lines)
