"""
Recovering a tool call that the model *wrote* instead of *made*.

Small local models are the reason this exists. `qwen2.5-coder:3b` advertises
`"capabilities": ["completion", "tools", "insert"]`, and its Ollama template
tells it, verbatim, to answer with `<tool_call>{...}</tool_call>` and "Do not
include any backticks or ```json". It then does exactly that — measured live
against Ollama 0.30.6, every single time:

    prompt "hello"                → '```json\\n{\\n "name": "list_files", ...'
    prompt "read the file X"      → '```json\\n{"name":"read_file", ...}```'
    same prompts, streamed        → '{"name": "read_file", "arguments": {...}}'

with `tool_calls: null` on every reply. Ollama's OpenAI shim only lifts the
exact `<tool_call>` form into the tool-call channel, so the fenced and bare
variants stay in `content`. From the agent's side that is a turn which asked
for nothing: `stop_reason == "end_turn"`, no tool_use block, and the JSON
printed at the user as if it were an answer. The 3B model was not failing to
decide what to do — it decided correctly and the decision was thrown away.

So the parser is not a nicety for one model. Any model too small to hold its
output format is unusable as an agent without it, and there is no way to tell
which ones those are except by reading what comes back.

Two rules keep the recovery from becoming a hazard of its own:

* **The name must be a tool that was actually offered this turn.** Not
  cosmetic. The live probe above answered "what your name?" with
  `{"name": "TOMAS", "arguments": {}}` — correctly-shaped JSON, in a model
  that had just been shown two tools, naming neither. Matching on shape alone
  would have invented a tool call out of the model introducing itself.
* **The object may carry nothing but a call.** Keys are checked against a
  closed set, so a fenced JSON *example* — the model showing the user what a
  request body looks like — does not get executed. A payload with a `steps` or
  `description` key next to `name` is prose about a call, not a call.

This module is pure: it parses text and builds blocks, and never decides
whether to act on them. `core.loop` owns that, on both model paths.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any, Iterable, Optional

#: Where a candidate may hide. Tried in order; the first pattern that yields a
#: usable call wins, so a `<tool_call>` block is never re-read as a fence.
_TOOL_CALL_TAG_RE = re.compile(
    r"<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL | re.IGNORECASE)
_FENCE_RE = re.compile(
    r"```(?:json|tool_call|JSON)?\s*\n?(.*?)```", re.DOTALL)

#: Every key a bare tool call is allowed to carry. An object with anything
#: else in it is describing a call, not making one — see the module docstring.
_ALLOWED_KEYS = {"name", "arguments", "parameters", "input", "args",
                 "id", "type", "function", "tool", "tool_name"}

#: Where the arguments live, in the order they are tried. `arguments` is the
#: OpenAI/Qwen spelling, `input` the Anthropic one, `parameters` what models
#: reach for when they are copying a JSON Schema back at you.
_ARG_KEYS = ("arguments", "parameters", "input", "args")


class RecoveredBlock(dict):
    """A content block, readable as an object *and* as a dict.

    The same dual shape `openai_adapter._Block` has, and for the same reason:
    `core.loop` reads `b.type` / `b.name` / `b.input`, then appends the whole
    list into `state.messages`, where `zen_proxy.anthropic_to_openai` reads it
    back with `b.get("type")` and `t["name"]`. Defined here rather than
    imported so `core/` keeps depending on nothing.
    """

    def __init__(self, data: dict):
        super().__init__(data)
        self.type = data.get("type", "text")
        self.text = data.get("text", "")
        self.id = data.get("id", "")
        self.name = data.get("name", "")
        self.input = data.get("input", {})

    def model_dump(self) -> dict:
        return dict(self)

    def __repr__(self) -> str:
        return f"<RecoveredBlock {self.type} {self.name or self.text[:24]!r}>"


class RecoveredResponse:
    """A model response reassembled from text, shaped like a real one.

    `stop_reason` is `"tool_use"` because that is what the model meant. It goes
    back into `run_turn`'s ordinary tool path — permissions, loop detection,
    the budget checkpoint and the transcript all apply unchanged, which is the
    point of building a response rather than executing anything here.

    `usage` is None deliberately: the call that produced this text has already
    been billed and counted by whichever path made it. A second `_record_usage`
    against the same call would double the turn's reported input.
    """

    def __init__(self, content: list, recovered: list["RecoveredBlock"]):
        self.content = content
        self.stop_reason = "tool_use"
        self.usage = None
        #: Just the synthesised tool_use blocks, for reporting.
        self.recovered = recovered


def _new_id() -> str:
    return f"toolu_{hex(int(time.time() * 1000))[2:]}{uuid.uuid4().hex[:12]}"


def tool_names(tools: Iterable[Any]) -> set[str]:
    """The names on offer this turn, from either tool-schema shape.

    Reads `state.tools`, which is Anthropic-shaped (`{"name": ...}`) in this
    codebase but arrives OpenAI-shaped (`{"function": {"name": ...}}`) from
    anything that has already been translated. Accepting both costs four lines
    and removes a whole class of "recovery silently never fires" bug.
    """
    names: set[str] = set()
    for tool in tools or []:
        if not isinstance(tool, dict):
            name = getattr(tool, "name", None)
            if isinstance(name, str) and name:
                names.add(name)
            continue
        name = tool.get("name")
        if not name:
            function = tool.get("function")
            if isinstance(function, dict):
                name = function.get("name")
        if isinstance(name, str) and name:
            names.add(name)
    return names


def _as_args(value: Any) -> Optional[dict]:
    """Normalise an arguments payload, or None if it is not one.

    OpenAI sends arguments as a *JSON string*, and a model imitating that wire
    format sends the string too. Rejecting it would fail exactly the models
    that are copying the format most faithfully.
    """
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return None
        return parsed if isinstance(parsed, dict) else None
    if value is None:
        return {}
    return None


def _call_from_object(obj: Any, known: set[str]) -> Optional[tuple[str, dict]]:
    """(name, args) if `obj` is a call to a tool that was offered, else None."""
    if not isinstance(obj, dict) or not obj:
        return None

    # OpenAI's nested shape: {"type": "function", "function": {...}}.
    inner = obj.get("function")
    if isinstance(inner, dict):
        nested = _call_from_object(inner, known)
        if nested is not None:
            return nested

    if set(obj) - _ALLOWED_KEYS:
        return None

    name = obj.get("name") or obj.get("tool") or obj.get("tool_name")
    if not isinstance(name, str) or name not in known:
        return None

    for key in _ARG_KEYS:
        if key in obj:
            args = _as_args(obj[key])
            return (name, args) if args is not None else None
    # A no-argument tool legitimately calls with nothing at all.
    return name, {}


#: How many `{` positions the scan will attempt before giving up. Reached only
#: by text that is mostly braces and mostly unparseable; a real reply uses a
#: handful. Bounds the scan so a pathological output cannot make the parse
#: quadratic in the length of the reply.
_MAX_SCAN_ATTEMPTS = 40


def _embedded_objects(text: str) -> list[str]:
    """Every complete JSON value in `text`, as the exact substrings it spans.

    `json.loads` on the whole reply is not enough and neither is testing the
    stripped text for a leading `{`: models put a call next to other things.
    Caught live — qwen answered with a bare call *followed by* a fenced JSON
    array of filenames, and any parser that reads the whole string at once, or
    stops at the first fence, sees only the array and recovers nothing.

    `raw_decode` gives the end offset, so each value is returned as the literal
    slice that produced it — which is what `_strip_recovered` needs in order to
    take the call out of the prose without guessing at its boundaries.
    """
    decoder = json.JSONDecoder()
    found: list[str] = []
    index = 0
    attempts = 0
    while index < len(text) and attempts < _MAX_SCAN_ATTEMPTS:
        start = text.find("{", index)
        if start == -1:
            break
        attempts += 1
        try:
            _value, end = decoder.raw_decode(text, start)
        except (json.JSONDecodeError, ValueError):
            index = start + 1
            continue
        found.append(text[start:end])
        index = end
    return found


def _candidates(text: str) -> list[str]:
    """JSON-ish fragments to try, most explicit first, deduplicated.

    Every tier is collected rather than the first non-empty one winning. A
    reply can carry a call in one place and something JSON-shaped in another,
    and short-circuiting on "there were fences" means the fences decide whether
    the call is ever looked at.
    """
    seen: set[str] = set()
    ordered: list[str] = []
    for fragment in (_TOOL_CALL_TAG_RE.findall(text)
                     + _FENCE_RE.findall(text)
                     + _embedded_objects(text)):
        key = fragment.strip()
        if key and key not in seen:
            seen.add(key)
            ordered.append(fragment)
    return ordered


def _parse_calls(fragment: str, known: set[str]) -> list[tuple[str, dict]]:
    """Every call in one fragment. A list payload is several calls at once."""
    try:
        parsed = json.loads(fragment.strip())
    except (json.JSONDecodeError, ValueError):
        return []
    items = parsed if isinstance(parsed, list) else [parsed]
    calls = []
    for item in items:
        call = _call_from_object(item, known)
        if call is not None:
            calls.append(call)
    return calls


def _strip_recovered(text: str, fragments: list[str]) -> str:
    """The prose left once the fragments that became calls are removed.

    Kept rather than discarded because models routinely narrate ("I'll read
    that file:") before the JSON, and that sentence is the only thing the user
    would otherwise see explaining what is about to run.
    """
    remaining = text
    for fragment in fragments:
        start = remaining.find(fragment)
        if start == -1:
            continue
        end = start + len(fragment)
        # Take the delimiters with it: leaving a bare ``` or </tool_call>
        # behind is worse than leaving the whole block.
        before = remaining[:start].rstrip()
        for opener in ("```json", "```tool_call", "```JSON", "```",
                       "<tool_call>"):
            if before.endswith(opener):
                before = before[: -len(opener)].rstrip()
                break
        after = remaining[end:].lstrip()
        for closer in ("```", "</tool_call>"):
            if after.startswith(closer):
                after = after[len(closer):].lstrip()
                break
        remaining = f"{before}\n{after}".strip()
    return remaining.strip()


def recover(text: str, tools: Iterable[Any]) -> Optional[RecoveredResponse]:
    """A response carrying the tool calls `text` describes, or None.

    None means "this was an answer" — the overwhelmingly common case, and the
    one that must stay free. A reply with no `{` in it never reaches the JSON
    parser at all.
    """
    if not text or "{" not in text:
        return None
    known = tool_names(tools)
    if not known:
        return None

    used: list[str] = []
    calls: list[tuple[str, dict]] = []
    for fragment in _candidates(text):
        found = _parse_calls(fragment, known)
        if found:
            used.append(fragment)
            calls.extend(found)
    if not calls:
        return None

    blocks = [RecoveredBlock({"type": "tool_use", "id": _new_id(),
                              "name": name, "input": args})
              for name, args in calls]
    content: list = []
    prose = _strip_recovered(text, used)
    if prose:
        content.append(RecoveredBlock({"type": "text", "text": prose}))
    content.extend(blocks)
    return RecoveredResponse(content, blocks)
