"""What actually went over the wire, kept so it can be looked at.

The agent's normal output is a rendering: a tool call appears as one yellow
line naming the tool and its headline argument, and the request that carried
it — the system prompt, the tool schemas, the whole message history — is never
shown at all. That is the right default and it is useless for the one question
this module answers: *what did the model actually receive, and what did it
actually send back?*

Three design points, each of which is a bug avoided:

**Recording is off until it is switched on.** A session sends its entire
conversation on every turn, so capturing every payload unconditionally would
grow without bound, in memory, for the whole session — the largest thing the
program holds, kept for the case nobody asked for. `set_enabled(True)` is what
starts it, and `features.debug_view` is what calls that.

**It stores a copy, not the object.** The messages list is mutated in place
after a call (tool results are appended to it, cache breakpoints are re-cut on
it), so keeping a reference would mean every recorded request slowly became a
picture of the *current* state rather than the state it was sent with. The
copy is made through `json.dumps` at record time, which also flattens the SDK's
block objects into the shape they were serialised as.

**It never raises.** This runs on the hot path of every model call. A debug
facility that can end a turn is worse than no debug facility, so everything
here is wrapped: a payload that will not serialise is recorded as a note
saying so, and the turn carries on.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Optional

#: How many exchanges are kept. Each holds a full request, and a request holds
#: the whole conversation, so this is the memory ceiling of the whole feature:
#: roughly `MAX_ENTRIES` × the size of one turn's payload. Twenty is enough to
#: look back over the last few tool steps and small enough that a long session
#: cannot turn it into a leak.
MAX_ENTRIES = 20

#: Longest single string kept inside a payload. A `write_file` argument can
#: carry a whole document and a tool result can carry a whole file; neither is
#: worth holding twenty copies of. Truncation is marked in the text itself so
#: a reader never mistakes a clipped value for the real one.
MAX_VALUE_CHARS = 4_000


@dataclass
class Exchange:
    """One model call: what was sent, and what came back."""

    seq: int
    started: float
    model: str = ""
    request: dict = field(default_factory=dict)
    response: dict = field(default_factory=dict)
    #: "streamed" or "non-streamed" — the two paths `run_turn` can take, and
    #: the first thing worth knowing when one of them misbehaves and the other
    #: does not (see the path-parity rule in CLAUDE.md).
    path: str = ""
    error: str = ""
    elapsed_ms: int = 0

    @property
    def tool_count(self) -> int:
        tools = self.request.get("tools")
        return len(tools) if isinstance(tools, list) else 0

    @property
    def message_count(self) -> int:
        messages = self.request.get("messages")
        return len(messages) if isinstance(messages, list) else 0


_enabled = False
_entries: list[Exchange] = []
_seq = 0
#: When set, every exchange is also appended here as it happens. That file is
#: what a separate viewer window tails, which is the only way to watch a
#: session's traffic *while* using the session — the chat REPL owns the
#: console it is running in, so a live view cannot share it.
_live_path: Optional[str] = None


def set_enabled(on: bool) -> None:
    """Start or stop recording. Switching off also frees what was held."""
    global _enabled
    _enabled = bool(on)
    if not _enabled:
        clear()


def set_live_file(path: Optional[str]) -> None:
    """Mirror exchanges into `path` as they happen, or stop doing so."""
    global _live_path
    _live_path = str(path) if path else None


def live_file() -> Optional[str]:
    return _live_path


def _append_live(text: str) -> None:
    """Append to the live file, never raising.

    Opened and closed per write rather than held: a viewer may be reading it,
    the session may run for hours, and a handle kept open across that is a
    handle that outlives the reason for it. Appends are small and infrequent
    (one per model call), so the cost of reopening is irrelevant next to the
    model call it is describing.
    """
    if not _live_path:
        return
    try:
        with open(_live_path, "a", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
    except OSError:
        return


def is_enabled() -> bool:
    return _enabled


def clear() -> None:
    _entries.clear()


def entries() -> list[Exchange]:
    """Newest last. A copy, so a caller iterating cannot be surprised by a
    concurrent record from the streaming thread."""
    return list(_entries)


def latest() -> Optional[Exchange]:
    return _entries[-1] if _entries else None


def _shrink(value: Any) -> Any:
    """Recursively clip long strings, so one big argument cannot dominate."""
    if isinstance(value, str):
        if len(value) <= MAX_VALUE_CHARS:
            return value
        return (value[:MAX_VALUE_CHARS]
                + f"… [{len(value) - MAX_VALUE_CHARS:,} more chars]")
    if isinstance(value, list):
        return [_shrink(v) for v in value]
    if isinstance(value, dict):
        return {k: _shrink(v) for k, v in value.items()}
    return value


def _snapshot(payload: Any) -> Any:
    """A detached, JSON-shaped copy of `payload`.

    Round-tripped through `json.dumps(default=str)` rather than `deepcopy`
    because the values here are SDK block objects as often as they are dicts,
    and what a reader wants to see is the wire shape — the same serialisation
    the HTTP client would have produced — not a Python repr of the object that
    made it.
    """
    try:
        return _shrink(json.loads(json.dumps(payload, default=str,
                                             ensure_ascii=False)))
    except Exception as exc:
        # Deliberately broad. `default=str` means serialising calls `str()` on
        # anything unknown, so an object with a hostile `__repr__`/`__str__`
        # raises whatever it likes — not just the TypeError/ValueError json
        # itself throws. Catching only those let one bad object take the whole
        # entry down to None, and the debug view then showed nothing at all
        # for the call the user was trying to inspect.
        try:
            name = type(payload).__name__
        except Exception:            # pragma: no cover — pathological
            name = "?"
        return {"__unserialisable__": f"{name}: {exc!r}"}


def record_request(*, model: str, system: Any, tools: Any, messages: Any,
                   max_tokens: int = 0, temperature: Any = None,
                   path: str = "") -> Optional[Exchange]:
    """Capture one outgoing request. Returns the entry, or None when off.

    The caller keeps the return value and hands it back to `record_response`,
    rather than this module tracking a "current" exchange: two calls can be in
    flight at once (a streamed call that falls through to a non-streamed
    repeat), and a module-level current-entry pointer would attribute the
    second one's response to the first.
    """
    global _seq
    if not _enabled:
        return None
    try:
        _seq += 1
        entry = Exchange(
            seq=_seq,
            started=time.time(),
            model=str(model or ""),
            path=path,
            request={
                "model": str(model or ""),
                "max_tokens": int(max_tokens or 0),
                "temperature": temperature,
                "system": _snapshot(system),
                "tools": _snapshot(tools),
                "messages": _snapshot(messages),
            },
        )
        _entries.append(entry)
        del _entries[:-MAX_ENTRIES]
        _append_live(
            f"\n{'=' * 78}\n"
            f"REQUEST #{entry.seq}  [{entry.path}]  {time.strftime('%H:%M:%S')}\n"
            f"model={entry.model}  max_tokens={entry.request.get('max_tokens')}  "
            f"tools={entry.tool_count}  messages={entry.message_count}\n"
            f"{'=' * 78}\n"
            + json.dumps(entry.request, indent=2, ensure_ascii=False,
                         default=str) + "\n")
        return entry
    except Exception:
        # Nothing here is worth a turn. See the module docstring.
        return None


def record_response(entry: Optional[Exchange], response: Any = None, *,
                    error: str = "") -> None:
    """Attach what came back to the request `record_request` returned."""
    if entry is None or not _enabled:
        return
    try:
        entry.elapsed_ms = int((time.time() - entry.started) * 1000)
        if error:
            entry.error = str(error)
            _append_live(f"\n--- RESPONSE #{entry.seq} FAILED after "
                         f"{entry.elapsed_ms:,}ms ---\n{entry.error}\n")
            return
        entry.response = _snapshot({
            "stop_reason": getattr(response, "stop_reason", None),
            "content": getattr(response, "content", None),
            "usage": getattr(response, "usage", None),
        })
        _append_live(
            f"\n--- RESPONSE #{entry.seq}  {entry.elapsed_ms:,}ms ---\n"
            + json.dumps(entry.response, indent=2, ensure_ascii=False,
                         default=str) + "\n")
    except Exception:
        return


def as_json(entry: Exchange) -> str:
    """One exchange, formatted the way it would be read: as JSON."""
    return json.dumps(
        {
            "seq": entry.seq,
            "path": entry.path,
            "elapsed_ms": entry.elapsed_ms,
            "request": entry.request,
            "response": entry.response,
            **({"error": entry.error} if entry.error else {}),
        },
        indent=2, ensure_ascii=False, default=str)
