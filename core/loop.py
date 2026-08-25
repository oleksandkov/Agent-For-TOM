"""
The agent loop, as a generator of events.

`run_turn` keeps calling the model until it stops requesting tools, yielding a
typed event for everything that happens along the way. It never writes to the
console, never reads stdin, and never touches a keyboard API — rendering and
questions both belong to the adapter driving it.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Iterator, Optional

from .events import (
    AgentEvent,
    AnnouncedWithoutActing,
    AssistantMessage,
    ContinuationGranted,
    ContinuationNeeded,
    ErrorOccurred,
    LoopDetected,
    PermissionNeeded,
    RetryScheduled,
    StreamingDisabled,
    TextDelta,
    ThinkingStarted,
    ToolCallsRecovered,
    ToolFinished,
    ToolResultTruncated,
    ToolStarted,
    TruncatedOutputDiscarded,
    TurnFinished,
    TurnStarted,
)
from .console import is_interactive_tool
from .state import CYCLE_WINDOW, REPEATED_CALL_LIMIT, AgentState
from .toolcall_text import recover as recover_text_tool_calls

MAX_RETRIES = 3

_RETRYABLE_MARKERS = (
    "429", "rate_limit", "Too Many Requests",
    "502", "503", "504", "Bad Gateway",
    "Service Unavailable", "Gateway Timeout",
    "timeout", "timed out",
)

# A quota is not a busy signal. These say "you have spent your allowance",
# which no amount of backing off inside one turn will clear — a free-tier run
# burned 14 consecutive turns at ~38s each (the full 5/10/20s ladder, three
# times over) discovering that, then reported the turns as failures. Retrying
# these is worse than not retrying: it stalls the user and keeps hammering a
# limiter that is already refusing.
_QUOTA_MARKERS = (
    "FreeUsageLimitError",
    "insufficient_quota",
    "quota exceeded",
    "billing",
    "credit balance",
    "usage limit",
    "monthly limit",
    # OpenRouter's daily free-tier cap. Found the hard way: it says neither
    # "quota" nor "limit exceeded" in any form the list above matched, so a
    # live run burned the full 5/10/20s ladder on every remaining turn before
    # giving up — the exact waste the quota short-circuit exists to prevent.
    "free-models-per-day",
    "openrouter_free_tier_daily",
    "per-day",
    "daily limit",
    "add credits",
    "purchase credits",
)


# Providers often state the wait in the message body rather than a header:
# Gemini says "Please retry in 6.152706999s.", others emit a retryDelay field.
_RETRY_HINT_RE = re.compile(
    r"(?:please\s+)?retry(?:\s+again)?\s+(?:in|after)\s+([0-9]+(?:\.[0-9]+)?)\s*s"
    r"|retry[_-]?delay[\"'\s:]+([0-9]+(?:\.[0-9]+)?)\s*s?",
    re.IGNORECASE,
)

# A per-minute cap is a blip; a per-day one is an allowance. Google puts the
# distinction in quotaId (…PerMinute-FreeTier / …PerDay-FreeTier) while using
# identical prose for both.
_PER_MINUTE_RE = re.compile(r"per[_-]?minute", re.IGNORECASE)

# …and Google attaches a short "Please retry in 11.9s" to a *daily* cap too,
# so the retry hint cannot be the last word. An explicit per-day quota is an
# allowance: it will still be exhausted eleven seconds from now.
_PER_DAY_RE = re.compile(r"per[_-]?day|daily", re.IGNORECASE)


def retry_hint_seconds(err: Exception) -> Optional[float]:
    """A wait the provider stated in the error text, if any."""
    match = _RETRY_HINT_RE.search(str(err))
    if not match:
        return None
    raw = match.group(1) or match.group(2)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return min(value, MAX_RETRY_DELAY) if value > 0 else None


def is_quota_error(err: Exception) -> bool:
    """True when the failure is an exhausted allowance, not a transient blip.

    A stated retry time outranks the wording, always. Gemini answers a
    *per-minute* limit with "You exceeded your current quota, please check your
    plan and billing details … Please retry in 6.15s." — prose that reads like a
    dead account attached to a six-second wait. Matching on "billing" alone
    turned every ordinary rate-limit into a fatal error and killed runs that
    would have succeeded on the next attempt.
    """
    text = str(err)
    # Precedence, most specific first. Per-day beats the retry hint because
    # Google sends both together; the hint beats the prose because the prose is
    # identical for a six-second cap and a dead account.
    if _PER_DAY_RE.search(text):
        return True
    if retry_hint_seconds(err) is not None:
        return False
    if _PER_MINUTE_RE.search(text):
        return False
    low = text.lower()
    return any(marker.lower() in low for marker in _QUOTA_MARKERS)


def retry_after_seconds(err: Exception) -> Optional[float]:
    """Honour a server-supplied Retry-After, when there is one.

    The provider knows when its window reopens and we do not; guessing with a
    fixed ladder either hammers too early or waits far longer than needed.
    """
    for attr in ("response", "http_response"):
        response = getattr(err, attr, None)
        headers = getattr(response, "headers", None)
        if not headers:
            continue
        for key in ("retry-after", "Retry-After",
                    "x-ratelimit-reset-after", "anthropic-ratelimit-requests-reset",
                    "x-ratelimit-reset", "X-RateLimit-Reset"):
            try:
                raw = headers.get(key)
            except Exception:
                raw = None
            if raw is None:
                continue
            try:
                value = float(str(raw).strip())
            except (TypeError, ValueError):
                continue
            value = _as_delay_seconds(value)
            if value > 0:
                return min(value, MAX_RETRY_DELAY)
    return None


def _as_delay_seconds(value: float) -> float:
    """Normalise a rate-limit header to "seconds from now".

    Providers disagree about what these carry: some send a delta in seconds,
    OpenRouter sends an absolute epoch in *milliseconds*
    (`X-RateLimit-Reset: 1785974400000`). Sleeping 1.7 billion seconds because
    a header looked like a delta is not a recoverable mistake, so anything
    implausibly large is read as a timestamp and converted.
    """
    now = time.time()
    if value > now * 100:          # epoch in milliseconds
        return value / 1000.0 - now
    if value > now / 2:            # epoch in seconds
        return value - now
    return value                   # already a delta


MAX_RETRY_DELAY = 60.0

# Hard ceiling for the one automatic max_tokens escalation. High enough that a
# reasoning model can think *and* write a real document; low enough that a
# runaway cannot bill an unbounded completion. Raised from 32,768 alongside
# `provider_manager.Capabilities.max_output_tokens`'s default moving to that
# same number: escalation needs somewhere to go *above* the new unprobed
# default, or a model that still truncates at 32,768 has no retry left.
# Measured live: deepseek-v4-flash-free accepts max_tokens up to at least
# this.
MAX_OUTPUT_CEILING = 65_536


def backoff_delay(attempt: int, err: Optional[Exception] = None) -> float:
    """5s, 10s, 20s — jittered, capped, and overridden by Retry-After.

    Jitter matters as soon as more than one client is retrying: without it they
    all wake at the same instant and re-collide on the same limiter.
    """
    if err is not None:
        supplied = retry_after_seconds(err)
        if supplied is None:
            supplied = retry_hint_seconds(err)
        if supplied is not None:
            # A hair over what they asked for: coming back at exactly the
            # stated instant races the window and 429s again.
            return round(supplied + 0.5, 2)
    import random
    base = min(5 * (2 ** attempt), MAX_RETRY_DELAY)
    return round(base * random.uniform(0.8, 1.2), 2)

_CLIENT_ERROR_MARKERS = (
    "400", "Bad Request", "invalid_request",
    "401", "Unauthorized", "authentication",
    "403", "Forbidden", "permission",
    "422", "Unprocessable",
)


def is_retryable_error(err: Exception) -> bool:
    """True if the error is transient and worth retrying (429, 5xx, timeout)."""
    if any(k in str(err) for k in _RETRYABLE_MARKERS):
        return True
    status = getattr(err, "status_code", None)
    return bool(status and status >= 500)


def is_client_error(err: Exception) -> bool:
    """True for 400/401/403/422 — a retry would fail identically."""
    if any(k in str(err) for k in _CLIENT_ERROR_MARKERS):
        return True
    status = getattr(err, "status_code", None)
    return bool(status and 400 <= status < 500 and status != 429)


def call_signature(name: str, args: dict) -> str:
    """A stable identity for a tool call, used to spot a stuck loop."""
    try:
        payload = json.dumps(args, sort_keys=True, default=str)
    except Exception:
        payload = str(args)
    return f"{name}:{payload}"


def detect_loop(signatures: list[str]) -> Optional[str]:
    """Spot genuine non-progress: the same call over and over, or a short
    cycle of calls repeating.

    Deliberately narrow. A long run of *distinct* calls is a task making
    progress, not a loop, and must never be stopped by this.
    """
    if (len(signatures) >= REPEATED_CALL_LIMIT
            and len(set(signatures[-REPEATED_CALL_LIMIT:])) == 1):
        return f"the same tool call repeated {REPEATED_CALL_LIMIT}x in a row"

    # A→B→A→B→A→B (period 2) or A→B→C→A→B→C (period 3), three cycles deep.
    for period in (2, 3):
        window = period * 3
        if window > CYCLE_WINDOW + period or len(signatures) < window:
            continue
        tail = signatures[-window:]
        if len(set(tail)) == period and all(
            tail[i] == tail[i % period] for i in range(window)
        ):
            return f"a {period}-step cycle of tool calls repeated 3x with no progress"
    return None


#: What share of a clipped tool result is kept from the end.
#: A traceback's exception line, a compiler's error summary, a test runner's
#: failure count and `run_command`'s own exit line all live at the tail.
#: Head-only clipping handed the model the banner of a failing command and
#: none of its error — the most expensive characters were the ones dropped.
RESULT_TAIL_SHARE = 0.3


def clip_result(text: str, limit: int) -> str:
    """Clip an oversized tool result to `limit` characters, keeping both ends.

    Pure, and deliberately budget-neutral: the same `limit` characters of
    content survive as before, they are just taken from both ends instead of
    only the front. The marker names how much went missing so the model can
    tell a clipped result from a complete one and re-run for the middle.
    """
    if len(text) <= limit:
        return text
    tail = int(limit * RESULT_TAIL_SHARE)
    if tail < 1:
        # No room to split meaningfully — behave exactly as before.
        return text[:limit] + f"\n[...truncated, full result was {len(text)} chars]"
    head = limit - tail
    cut = len(text) - limit
    return (f"{text[:head]}"
            f"\n[...{cut:,} chars cut from the middle; "
            f"full result was {len(text):,} chars]\n"
            f"{text[-tail:]}")


#: How many tool calls may be in flight at once. Small on purpose: the point
#: is to overlap a handful of file reads, not to open thirty sockets and make
#: the machine the bottleneck instead of the model.
MAX_PARALLEL_TOOLS = 4


def _parallel_batch(state: AgentState, blocks: list) -> list:
    """The blocks in this batch that may run at the same time as each other.

    Narrow and opt-in, for two reasons that are not negotiable:

    * **Nothing the user would be asked about.** A permission prompt is a
      sequential, blocking conversation, and a worker thread cannot hold one.
      A call that needs approval must not begin before the answer arrives.
    * **Only what the host declared safe.** `parallel_safe` defaults to False,
      so a host that says nothing gets exactly today's behaviour.

    Returns [] rather than a single block when only one qualifies: spinning up
    a pool to run one read costs more than it saves.
    """
    if len(blocks) < 2:
        return []
    batch = [b for b in blocks
             if not state.needs_permission(b.name, b.input)
             and state.parallel_safe(b.name, b.input)]
    return batch if len(batch) > 1 else []


def _run_parallel(state: AgentState, blocks: list) -> dict:
    """Run `blocks` concurrently. Returns {tool_use_id: (result, ok, ms)}.

    Nothing is yielded and no host callback fires from in here. A generator
    cannot yield across a thread boundary, and `on_tool_call` appends to lists
    the front end reads — so every event, every callback and every transcript
    append still happens on the main thread, in the original block order, once
    this has returned. Concurrency buys the wall clock and changes nothing else.

    A tool that raises is caught per block, exactly as the sequential path does:
    one failing read must not take the other three down with it.
    """
    from concurrent.futures import ThreadPoolExecutor

    def run_one(block):
        started_at = time.perf_counter()
        try:
            result = state.execute_tool(block.name, block.input)
            ok = not (isinstance(result, str)
                      and result.lstrip().startswith("Error:"))
        except Exception as e:
            result = f"Error: tool raised {type(e).__name__}: {e}"
            ok = False
        return result, ok, int((time.perf_counter() - started_at) * 1000)

    with ThreadPoolExecutor(
            max_workers=min(MAX_PARALLEL_TOOLS, len(blocks))) as pool:
        outcomes = list(pool.map(run_one, blocks))
    return {b.id: outcome for b, outcome in zip(blocks, outcomes)}


def _prepare_request(state: AgentState) -> None:
    """Let the host re-cut prompt-cache breakpoints against the current history.

    Runs before *every* model call, on both paths, because the history a
    tool-using turn accumulates is exactly the history that used to go
    uncached: the host was only asked once, before the turn made a single call.

    Swallows everything it raises. A caching hint that fails is a missed
    optimisation; a caching hint that ends the turn is a bug, and the hook runs
    on the hot path of every request in the session.
    """
    if state.before_model_call is None:
        return
    try:
        state.before_model_call(state.messages)
    except Exception:
        pass


def _cached_input(usage) -> int:
    """Cache hits, under either name the two client shapes use.

    The adapter normalises the OpenAI-wire spellings into `cached_input_tokens`;
    the Anthropic SDK reports `cache_read_input_tokens` and nothing else. The
    native name matters most, because Anthropic is the only provider where the
    agent places the breakpoints itself — so it is the one whose caching needs
    to be measurable to stay correct.

    Type-checked rather than defaulted: a token counter is formatted with `:,`
    and summed across a session, so anything that is not a number has to become
    zero here and not three screens later.
    """
    for name in ("cached_input_tokens", "cache_read_input_tokens"):
        value = getattr(usage, name, None)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return int(value)
    return 0


def _record_usage(state: AgentState, usage) -> None:
    if not usage:
        return
    inp = getattr(usage, "input_tokens", 0) or 0
    out = getattr(usage, "output_tokens", 0) or 0
    # Reported alongside the input rather than deducted from it: this is what
    # was sent, annotated with how much of it the provider already had. Folding
    # it into the total would make a well-cached turn look like a cheap one and
    # hide the thing worth watching — how much is being re-sent at full price.
    cached = _cached_input(usage)
    state.usage["input"] = inp
    state.usage["output"] = out
    state.usage["cached_input"] = cached
    state.usage["total_input"] = state.usage.get("total_input", 0) + inp
    state.usage["total_output"] = state.usage.get("total_output", 0) + out
    state.usage["total_cached_input"] = (
        state.usage.get("total_cached_input", 0) + cached)
    state.usage["calls"] = state.usage.get("calls", 0) + 1


def _stream_serve_blocker(final_msg) -> Optional[str]:
    """None when the streamed assembly can stand in for a non-streamed repeat;
    otherwise, which check failed.

    The adapter builds complete `tool_use` blocks from the stream; the only
    thing it cannot do is recover arguments whose JSON never parsed, which it
    counts as `malformed_tool_args`. When that count is zero the second call
    has nothing left to contribute — measured across three live sessions,
    58 discarded streams, `would_have_served == duplicate_calls` and
    `stream_malformed_tool_args == 0` on every one of them: 2.9M input tokens
    re-sent to be told the same thing. Once every recoverable case is served
    directly (this function returning `None`), the reason attached to the
    cases that still fall through matters more than the old boolean did — see
    `_record_discarded_stream`.

    The real Anthropic SDK sets no such attribute; `getattr(..., 0)` reads as
    "nothing was malformed", which is correct — its own stream assembly is
    authoritative, and that path never needed the second call either.
    """
    if final_msg is None:
        return "no_final_message"
    malformed = getattr(final_msg, "malformed_tool_args", 0)
    if not isinstance(malformed, int) or isinstance(malformed, bool):
        malformed = 0
    if malformed:
        return "malformed"
    # A reply cut off at the output limit may have stopped cleanly *between*
    # two tool calls, so nothing parsed badly and a call was still lost. Fall
    # through in that case: the non-streamed retry gets a fresh budget and is
    # the existing path for handling truncation.
    if getattr(final_msg, "output_truncated", False):
        return "truncated"
    # And it must actually carry the calls it claims. A provider that reports
    # `tool_calls` while assembling none leaves a response that is neither a
    # reply nor a tool step, and serving it would spend a turn going nowhere;
    # the non-streamed call is the existing answer to "ask again".
    content = getattr(final_msg, "content", None) or []
    if not any(getattr(b, "type", None) == "tool_use" for b in content):
        return "no_tool_use_content"
    return None


def _stream_can_serve(final_msg) -> bool:
    """Whether the streamed assembly can stand in for a non-streamed repeat."""
    return _stream_serve_blocker(final_msg) is None


def _record_discarded_stream(state: AgentState, usage, final_msg,
                             reason: Optional[str] = None) -> None:
    """Account for a streamed answer that is about to be thrown away.

    A streamed call that wants tools falls through to a second, non-streamed
    call carrying the identical payload, and only that second call was ever
    counted — `_record_usage` is skipped on this path precisely so the turn is
    not billed twice. But the request was still sent and still paid for, so the
    counter has been under-reporting by roughly the size of the whole
    conversation, once per tool step. A 29-step turn reporting 1.8M input
    tokens really spent closer to 3.5M.

    `would_have_served` used to be inferred from `malformed_tool_args == 0`
    alone, which conflated "would have served" with "was truncated" or "had no
    tool_use content" — both legitimate discard reasons on a stream that is
    not malformed. Now that `_stream_serve_blocker` already serves everything
    recoverable, this stays at zero going forward; `reason` (from that same
    function, or `"interrupted"` for a turn the user cut off) is recorded per
    bucket instead, so a future regression is diagnosable rather than a single
    flat count.
    """
    inp = getattr(usage, "input_tokens", 0) or 0
    out = getattr(usage, "output_tokens", 0) or 0
    state.usage["duplicate_input"] = state.usage.get("duplicate_input", 0) + inp
    state.usage["duplicate_output"] = state.usage.get("duplicate_output", 0) + out
    state.usage["duplicate_calls"] = state.usage.get("duplicate_calls", 0) + 1

    malformed = getattr(final_msg, "malformed_tool_args", 0)
    if not isinstance(malformed, int) or isinstance(malformed, bool):
        malformed = 0
    state.usage["stream_malformed_tool_args"] = (
        state.usage.get("stream_malformed_tool_args", 0) + malformed)
    # Always present once a stream has been discarded, even at zero. A counter
    # that exists only when it is non-zero forces every reader to guess whether
    # a missing key means "none" or "never measured" — and those are the two
    # answers this whole measurement exists to tell apart.
    state.usage["would_have_served"] = (
        state.usage.get("would_have_served", 0) + (1 if malformed == 0 else 0))
    reason_key = f"duplicate_reason_{reason or 'unknown'}"
    state.usage[reason_key] = state.usage.get(reason_key, 0) + 1


def turn_expired(state: AgentState) -> bool:
    """Whether this turn has used its wall-clock allowance.

    Monotonic, so a clock change mid-turn cannot end it early or extend it
    forever. `turn_deadline == 0` means unbounded, which stays the default for
    any host that has not asked for a ceiling.
    """
    return bool(state.turn_deadline) and time.monotonic() >= state.turn_deadline


def _report_deadline(state: AgentState, partial: str) -> Iterator[AgentEvent]:
    """Say the turn ran out of time, and what that leaves the user holding."""
    limit = int(state.max_turn_seconds)
    kept = (f" What it had written is below." if partial else
            " It had produced nothing to keep.")
    state.last_error = f"turn deadline {limit}s exceeded"
    yield ErrorOccurred(
        f"This turn hit its {limit}s time limit and was stopped between steps, "
        f"so the transcript is intact.{kept} Ask for a smaller piece of the "
        f"work, or raise AGENT_MAX_TURN_SECONDS.",
        detail=state.last_error, recoverable=True)


def _deadline_wind_down(state: AgentState) -> Iterator[AgentEvent]:
    """Out of time: spend one bounded call asking for whatever can be salvaged,
    instead of returning nothing.

    Checked between steps, so — unlike loop-detection's `_wind_down` — there
    is no dangling `tool_use` to answer here; every prior one already has its
    `tool_result` by the time this runs. Measured live: a session spent its
    full 900s on Step-1 research (re-measuring margins, auditing unrelated
    files' conventions) and was cut off having never reached the save call —
    `_report_deadline` alone reported "produced nothing to keep" when one more
    exchange could plausibly have gotten a file saved, or at least a summary
    of exactly what's done and what's left. One extra call risks a few more
    seconds past the deadline; the alternative is certain to have nothing.
    """
    state.messages.append({"role": "user", "content": [
        {"type": "text",
         # The scaffolding clause is not tidiness. Cleanup was tied to
         # finishing, so a turn that ran out of time left its working files
         # in the user's own output folder and said nothing about them —
         # measured: two of three sessions ended this way, one leaving two
         # scaffolding JSONs beside zero deliverables and four scratch
         # scripts elsewhere, with the final reply describing neither.
         # Naming them costs one line and is the difference between leftovers
         # and litter.
         "text": "Out of time for this turn. Stop researching — if a file "
                 "was ready to save, save it now with what you have; "
                 "otherwise say exactly what's done and what's left, in "
                 "one short reply, no more tool calls. In that reply also "
                 "list any scratch scripts or intermediate files you created "
                 "and where they are, so nothing of yours is left behind "
                 "unannounced."}
    ]})
    closing = yield from _model_call(state)
    if closing is None:
        return ""
    text = "".join(b.text for b in closing.content if hasattr(b, "text"))
    if text:
        state.messages.append(
            {"role": "assistant",
             "content": _text_reply_content(text, _reasoning_of(closing.content))})
        yield AssistantMessage(text)
    return text


def _sampling(state: AgentState) -> dict:
    """Sampling knobs for a model call, or `{}` to accept the provider default.

    One function, called from both `_stream_call` and `_model_call`, because
    sampling that applies on one path and not the other is a difference the
    user cannot see and cannot explain: the same question would come back
    steady when the reply had a tool call in it and unhinged when it did not.
    `tests/test_core_loop.py::PathParity` pins it.

    Returns a dict rather than a value so "say nothing" stays expressible.
    Sending `temperature: null`, or a number to a provider that rejects the
    parameter, would trade a sampling problem for a 400.
    """
    if state.temperature is None:
        return {}
    return {"temperature": state.temperature}


def _reasoning_of(blocks) -> str:
    """The `reasoning_content` a response's blocks carry, if any.

    Same field `zen_proxy.reasoning_of` reads, duplicated rather than
    imported so `core/` stays provider-agnostic — this only needs to know the
    key exists, not which adapter set it. See `AgentState.last_reasoning`.
    """
    for block in blocks or []:
        get = getattr(block, "get", None)
        if callable(get) and block.get("reasoning_content"):
            return str(block["reasoning_content"])
    return ""


def _text_reply_content(text: str, reasoning: str):
    """Content for a plain-text assistant turn, carrying reasoning if any.

    A model that reasoned needs `reasoning_content` back on this exact turn or
    the *next* request is refused (`zen_proxy.anthropic_to_openai` only reads
    it off a block list — a plain string skips that branch entirely, which is
    how this was lost for every text-only reply from a thinking model). A
    model that did not reason gets the plain string it always got: adding an
    empty `reasoning_content` key is a new way to be rejected by a provider
    that does not expect the key at all.
    """
    if reasoning:
        return [{"type": "text", "text": text, "reasoning_content": reasoning}]
    return text


def _stream_call(state: AgentState) -> Iterator[AgentEvent]:
    """Stream one model response, yielding TextDelta as tokens arrive.

    Returns `(full_text, has_tool_use, served)` to the caller via `yield
    from`. Raises on providers that cannot stream so the caller can fall back.

    `served` is the assembled response when it can stand in for the
    non-streamed repeat (see `_stream_can_serve`), and None when the caller
    must make that second call. It is *reported*, not acted on: `run_turn`
    still decides, and still runs permissions, loop detection and the budget
    over whichever response it ends up with — the single place that handling
    lives.
    """
    text_parts: list[str] = []
    has_tool_use = False
    stop_reason = None
    usage_info = None
    final_reasoning = ""
    # Bound before the loop: an interrupt between `content_block_start` and
    # `message_stop` leaves a stream that wanted tools with no final message,
    # and the usage branch below reads it either way.
    final_msg = None

    _prepare_request(state)
    with state.get_client().messages.stream(
        model=state.get_model(),
        max_tokens=state.max_tokens,
        system=state.system_prompt,
        tools=state.tools,
        messages=state.messages,
        **_sampling(state),
    ) as stream:
        for event in stream:
            # Checked per chunk rather than only between turns: without this
            # an Esc pressed mid-reply had to wait for the whole streamed
            # answer to finish before it did anything.
            if state.interrupted():
                break
            if event.type == "content_block_delta":
                if event.delta.type == "text_delta":
                    text_parts.append(event.delta.text)
                    yield TextDelta(event.delta.text)
            elif event.type == "content_block_start":
                if event.content_block.type == "tool_use":
                    has_tool_use = True
            elif event.type == "message_stop":
                final_msg = stream.get_final_message()
                stop_reason = final_msg.stop_reason
                usage_info = getattr(final_msg, "usage", None)
                final_reasoning = _reasoning_of(getattr(final_msg, "content", None))

    wants_tools = has_tool_use or stop_reason == "tool_use"
    # Recorded rather than reported here: truncation is handled identically for
    # the streamed and non-streamed paths, and `run_turn` owns that decision.
    # Reporting it inside this function meant the streaming path announced the
    # failure and returned, so the escalation retry — which lived only in the
    # non-streamed branch — never ran for any provider that streams. Which is
    # most of them.
    state.last_stop_reason = stop_reason
    # Same reason: this function returns joined text, not blocks, so a
    # reasoning model's chain-of-thought has nowhere else to reach the
    # assistant message `run_turn` saves for a text-only reply.
    state.last_reasoning = final_reasoning
    # When tools were requested and the stream could not be trusted to carry
    # them whole, the caller re-issues the request non-streamed; counting the
    # streamed call too would bill the same turn twice. When it *can* be
    # trusted there is no second call, so this is the only call there is and
    # it is counted like any other.
    if not wants_tools:
        blocker = None
    elif state.interrupted():
        blocker = "interrupted"
    else:
        blocker = _stream_serve_blocker(final_msg)
    served = final_msg if (wants_tools and blocker is None) else None
    # Reported, never acted on here. See `AgentState.last_tool_args_truncated`.
    state.last_tool_args_truncated = bool(
        wants_tools and served is None and final_msg is not None
        and (getattr(final_msg, "malformed_tool_args", 0)
             or getattr(final_msg, "output_truncated", False)))
    if not wants_tools or served is not None:
        _record_usage(state, usage_info)
    else:
        _record_discarded_stream(state, usage_info, final_msg, blocker)
    return "".join(text_parts), wants_tools, served


def _quota_error(err: Exception) -> ErrorOccurred:
    """The message a quota failure deserves: what happened and what to do."""
    return ErrorOccurred(
        "The provider's usage limit is exhausted — this is a quota, not a "
        "temporary blip, so retrying will not clear it. Switch provider or model "
        "with /provider or /model, or wait for the allowance to reset.",
        detail=str(err), recoverable=False)


def _upstream_excerpt(err: Exception, limit: int = 220) -> str:
    """The provider's own words, short enough to sit inside a message.

    The terminal adapter prints `ErrorOccurred.message` and nothing else, so a
    diagnosis that files the upstream text away under `detail` is *less*
    debuggable than the raw dump it replaces. Say what we think it was, then
    show what they actually said.

    Clipped from both ends for the same reason tool results are: the status
    line and endpoint are at the front, the provider's actual sentence is at
    the back, and a head-only excerpt of a JSON error body is entirely
    preamble — `{"error":{"param":null,"type":...` and nothing that names the
    problem.
    """
    text = " ".join(str(err).split())
    if len(text) <= limit:
        return text
    head = limit // 3
    return f"{text[:head]} … {text[-(limit - head):]}"


#: An upstream saying "this model is not there", in the several ways gateways
#: word it. Measured against OpenCode Zen on 2026-08-13: `ling-3.0-tiny-free`
#: is listed by `/v1/models` and answers every request with
#: `[404] No endpoints found for inclusionai/ling-3.0-tiny:free`, and
#: `laguna-s-2.1-free` with `503 Endpoint is unavailable`. Both are advertised
#: and neither is served.
_MODEL_GONE_MARKERS = (
    "no endpoints found",
    "endpoint is unavailable",
    "model not found",
    "no such model",
    "does not exist",
    "is not supported",
    "unknown model",
    "model_not_found",
)


def is_model_unavailable(err: Exception) -> bool:
    """True when the failure is "that model is not there", not "bad request"."""
    low = str(err).lower()
    return any(marker in low for marker in _MODEL_GONE_MARKERS)


def _model_unavailable_error(err: Exception) -> ErrorOccurred:
    """Name the one failure a user can fix in one keystroke.

    Worth separating from the generic 4xx because the generic advice is
    actively wrong here. A gateway that lists a model it cannot route to
    answers with a 400, so this arrived as "the provider rejected the request
    itself … usually that is a payload too large for the model (try /compact)"
    — sending the user to compact a two-message conversation to fix a model
    that does not exist. Nothing about the payload was ever the problem.
    """
    return ErrorOccurred(
        "This model is listed by the provider but is not currently being "
        "served, so every request to it fails the same way — it is not "
        "something retrying or compacting can fix. Pick another with /model. "
        f"Upstream said: {_upstream_excerpt(err)}",
        detail=str(err), recoverable=False)


#: A refused, unresolvable or dropped socket. None of these reach an HTTP
#: status, so nothing above classifies them: they arrived as the catch-all
#: "An unexpected error occurred: [WinError 10061] No connection could be
#: made because the target machine actively refused it" — which names neither
#: the endpoint that refused nor anything the user could do about it.
_UNREACHABLE_MARKERS = (
    "WinError 10061",           # Windows: connection actively refused
    "WinError 10060",           # Windows: connect timed out
    "WinError 11001",           # Windows: host not found
    "ConnectionRefusedError", "Connection refused",
    "ConnectionError", "APIConnectionError", "Connection aborted",
    "NewConnectionError", "MaxRetryError",
    "Name or service not known", "nodename nor servname",
    "getaddrinfo failed", "Failed to establish a new connection",
    "Errno 111", "Errno 61",    # Linux / macOS: connection refused
)


#: A request that timed out is not an endpoint that is absent — something
#: answered slowly, or is still thinking. These keep the retry ladder they
#: have; only a socket that never opened skips it.
_TIMEOUT_MARKERS = ("APITimeoutError", "ReadTimeout", "WriteTimeout",
                    "PoolTimeout", "timed out", "Timeout")


def _error_chain_text(err: Exception) -> str:
    """Type names and messages down the `raise ... from` chain.

    Needed because the useful text is usually not on the exception that was
    raised. Measured against a genuinely closed port, the Anthropic SDK
    raises `APIConnectionError` whose `str()` is the entire five words
    "Connection error." — the `[WinError 10061]` is on its `__cause__`. A
    classifier reading only `str(err)` sees nothing to match and the failure
    falls through to the catch-all, which is the bug this exists to fix.
    """
    parts: list[str] = []
    seen: set[int] = set()
    cur: Optional[BaseException] = err
    while cur is not None and id(cur) not in seen and len(parts) < 20:
        seen.add(id(cur))
        parts.append(type(cur).__name__)
        parts.append(str(cur))
        # `__cause__` only: an explicit `raise X from Y`. `__context__` would
        # also pick up whatever happened to be in flight inside an unrelated
        # `except` block, and this check runs ahead of every other one.
        cur = cur.__cause__
    return " | ".join(parts)


def is_endpoint_unreachable(err: Exception) -> bool:
    """True when nothing answered at the endpoint at all.

    Distinct from `is_retryable_error`: a 503 means the service is up and
    busy, this means the socket never opened. Retrying a closed port for the
    full 5/10/20s ladder cannot help — a local server is either running or it
    is not.
    """
    text = _error_chain_text(err)
    if any(marker in text for marker in _TIMEOUT_MARKERS):
        return False
    return any(marker in text for marker in _UNREACHABLE_MARKERS)


def _deepest_reason(err: Exception) -> str:
    """The most specific message in the chain, not the vaguest.

    `str(APIConnectionError)` is "Connection error." for a refused socket, a
    resolver failure and a dropped TLS handshake alike. The cause underneath
    is the one that says which.
    """
    reason, cur = str(err), err.__cause__
    while cur is not None:
        if str(cur).strip():
            reason = str(cur)
        cur = cur.__cause__
    return reason


def _endpoint_unreachable_error(err: Exception,
                                state: Optional[Any] = None) -> ErrorOccurred:
    """Say which endpoint refused, and what the two ways out are.

    Real session: the active provider was Ollama, Ollama was not running, and
    the turn reported `[WinError 10061] ... actively refused it` and nothing
    else. The endpoint is the single fact that makes it actionable, and the
    core cannot know it — `state.describe_endpoint` is the host's answer.
    """
    where = ""
    if state is not None and getattr(state, "describe_endpoint", None):
        try:
            label = state.describe_endpoint()
            if label:
                where = f" ({label})"
        except Exception:
            pass
    return ErrorOccurred(
        f"Nothing is listening at the active provider's endpoint{where}, so "
        "the request never reached a server. If it is a local one, start it; "
        "otherwise check the address and your connection, or switch provider "
        "with /provider. Retrying will fail the same way. "
        f"The socket said: {_deepest_reason(err)}",
        detail=str(err), recoverable=False)


def _client_error(err: Exception) -> ErrorOccurred:
    """Name what a 4xx actually was, instead of "an unexpected error".

    A client error was diagnosed only inside the `anthropic.InternalServerError`
    branch — which the in-process adapter never raises. It reports a 400 as a
    plain `RuntimeError` carrying the upstream's JSON body, so every rejected
    request on the adapter path (that is, every OpenAI-wire provider: zen,
    openrouter, ollama, custom) fell through to the catch-all and dumped raw
    JSON at the user.

    This changes no control flow. A 4xx is fatal to the turn before and after,
    because a retry sends the identical payload and is refused identically.
    What changes is whether the user can tell a rejected credential from an
    oversized payload from a model that cannot answer this conversation at all.
    """
    text = str(err)
    excerpt = _upstream_excerpt(err)
    if "reasoning_content" in text:
        return ErrorOccurred(
            "This endpoint answered in thinking mode and rejected a request that "
            "replayed one of its earlier turns without the reasoning it produced "
            "then. That turn's chain-of-thought was not carried forward, so this "
            "model cannot continue the conversation from here — switch with "
            "/model, or start a new session. "
            f"Upstream said: {excerpt}",
            detail=text, recoverable=False)
    if any(k in text for k in ("401", "403", "Unauthorized", "Forbidden",
                               "authentication")):
        return ErrorOccurred(
            "The provider refused the credentials for this request. Check the "
            "key for the active provider with /provider. "
            f"Upstream said: {excerpt}",
            detail=text, recoverable=False)
    return ErrorOccurred(
        "The provider rejected the request itself, so sending it again unchanged "
        "would fail the same way. Usually that is a payload too large for the "
        "model (try /compact) or a parameter it does not accept. "
        f"Upstream said: {excerpt}",
        detail=text, recoverable=False)


def _overrun(state: AgentState, started: float) -> float:
    """Seconds the turn spent past its ceiling, or 0.0 if it stayed inside.

    `turn_expired` is checked *between* steps, so a step starting one second
    inside the allowance can finish well outside it — measured, 1279.8s
    against a 1200s ceiling, saved as a clean success. Declining that step
    would be worse (it is usually the one writing the file), so the overrun
    is reported instead of prevented.
    """
    if not state.max_turn_seconds:
        return 0.0
    return max(0.0, (time.perf_counter() - started) - state.max_turn_seconds)


def _is_empty_response(response) -> bool:
    """Nothing to show and nothing to run.

    Not the same as a truncation (`stop_reason == "max_tokens"` carries
    partial text) and not the same as an error — the provider returned a
    well-formed message with no text, no tool call and nothing to act on.
    """
    if response is None:
        return True
    if getattr(response, "stop_reason", None) == "max_tokens":
        return False
    for block in getattr(response, "content", None) or []:
        if getattr(block, "type", None) == "tool_use":
            return False
        if (getattr(block, "text", "") or "").strip():
            return False
    return True


def _model_call(state: AgentState) -> Iterator[AgentEvent]:
    """One model call with retry. Returns the response, or None if it failed
    (an ErrorOccurred / TurnFinished will already have been yielded)."""
    import anthropic

    def fail(event: ErrorOccurred) -> ErrorOccurred:
        # Record the reason on the state so the host can save it with the
        # session rather than reporting a bare "empty reply".
        state.last_error = event.detail or event.message
        return event

    for attempt in range(MAX_RETRIES + 1):
        # A retry is a *new* request, so the deadline applies to starting it.
        # Without this the ladder outruns the ceiling on its own: a stalled
        # call costs the full 300 s socket timeout, timeouts are classified
        # retryable, and four attempts plus backoff is over twenty minutes for
        # one step of one turn.
        #
        # Only retries, never the first attempt: `_deadline_wind_down` calls
        # this deliberately *after* the deadline to salvage the turn's work,
        # and blocking that would trade a reported overrun for a lost
        # document.
        if attempt and turn_expired(state):
            yield fail(ErrorOccurred(
                "Out of time for this turn before the retry could run.",
                detail=f"turn deadline {int(state.max_turn_seconds)}s exceeded",
                recoverable=True))
            return None
        try:
            _prepare_request(state)
            response = state.get_client().messages.create(
                model=state.get_model(),
                max_tokens=state.max_tokens,
                system=state.system_prompt,
                tools=state.tools,
                messages=state.messages,
                **_sampling(state),
            )
            _record_usage(state, getattr(response, "usage", None))
            # An empty response is a provider hiccup, not a refusal: the
            # request was accepted and well-formed, so the identical payload
            # has a real chance the second time — unlike a 4xx, which is why
            # that one is never retried. Measured on deepseek-v4-flash-free
            # via Zen: a session that had already written both deliverables
            # lost only its closing report to this, and was saved
            # `complete: false` for it.
            if _is_empty_response(response) and attempt < MAX_RETRIES:
                delay = backoff_delay(attempt)
                yield RetryScheduled(attempt + 1, MAX_RETRIES, delay,
                                     "empty reply from the provider")
                time.sleep(delay)
                continue
            return response
        except anthropic.InternalServerError as e:
            if is_endpoint_unreachable(e):
                yield fail(_endpoint_unreachable_error(e, state))
                return None
            # Checked before the retryable test on purpose: a missing model is
            # reported as a 503 by some gateways, which `is_retryable_error`
            # reads as a blip and burns the whole 5/10/20s ladder on.
            if is_model_unavailable(e):
                state.model_unavailable = True
                yield fail(_model_unavailable_error(e))
                return None
            if is_client_error(e) and not is_retryable_error(e):
                yield fail(_client_error(e))
                return None
            if is_quota_error(e):
                yield fail(_quota_error(e))
                return None
            if not is_retryable_error(e):
                yield fail(ErrorOccurred("Error communicating with the AI service.",
                                         detail=str(e), recoverable=False))
                return None
            if attempt >= MAX_RETRIES:
                yield fail(ErrorOccurred(f"The AI service is unavailable right now: {e}",
                                         detail=str(e), recoverable=False))
                return None
            delay = backoff_delay(attempt, e)
            yield RetryScheduled(attempt + 1, MAX_RETRIES, delay, str(e))
            time.sleep(delay)
        except Exception as e:
            # Same diagnosis, same place in the order, as the SDK branch
            # above: an unreachable endpoint is decided before anything that
            # could send it round the retry ladder.
            if is_endpoint_unreachable(e):
                yield fail(_endpoint_unreachable_error(e, state))
                return None
            if is_model_unavailable(e):
                state.model_unavailable = True
                yield fail(_model_unavailable_error(e))
                return None
            if is_quota_error(e):
                yield fail(_quota_error(e))
                return None
            # Same test, same order, same outcome as the SDK branch above: a
            # 4xx that happens to carry a retryable-looking word in its body
            # keeps the retry it has today, and everything else is reported
            # instead of being dumped raw.
            if is_client_error(e) and not is_retryable_error(e):
                yield fail(_client_error(e))
                return None
            if attempt < MAX_RETRIES and is_retryable_error(e):
                delay = backoff_delay(attempt, e)
                yield RetryScheduled(attempt + 1, MAX_RETRIES, delay, str(e))
                time.sleep(delay)
            else:
                yield fail(ErrorOccurred(f"An unexpected error occurred: {e}",
                                         detail=str(e), recoverable=False))
                return None
    return None


def _wind_down(state: AgentState, response, reason: str) -> Iterator[AgentEvent]:
    """Stop executing tools but leave a well-formed transcript and get a
    closing summary out of the model.

    Every tool_use must be answered with a tool_result or the next request is
    malformed (dangling tool_calls) and the upstream rejects it outright.
    """
    state.messages.append({"role": "assistant", "content": response.content})
    state.messages.append({"role": "user", "content": [
        {
            "type": "tool_result",
            "tool_use_id": b.id,
            "content": f"Not executed: {reason}.",
        }
        for b in response.content if b.type == "tool_use"
    ] + [
        {"type": "text",
         "text": f"{reason}. Summarize what you've found so far, state clearly "
                 f"what remains unfinished, and respond to the user."}
    ]})

    closing = yield from _model_call(state)
    if closing is None:
        yield TurnFinished(reply="", usage=dict(state.usage))
        return
    text = "".join(b.text for b in closing.content if hasattr(b, "text"))
    if text:
        state.messages.append(
            {"role": "assistant",
             "content": _text_reply_content(text, _reasoning_of(closing.content))})
        yield AssistantMessage(text)
    yield TurnFinished(reply=text, usage=dict(state.usage))


def _report_truncation(state: AgentState, text: str) -> Iterator[AgentEvent]:
    """Say that the reply was cut off at the output limit.

    This was swallowed completely. `stop_reason` was read only to answer "did
    it ask for tools?", so `max_tokens` fell through the same branch as a
    normal finish. On a reasoning model the whole budget goes to internal
    reasoning, the visible content is empty, and the turn ended with no text,
    no tool call and no error — the user saw a token counter and nothing else,
    asked "where is the file?", and got another empty turn.

    An empty truncated reply is a failure and is recorded as one; a partial one
    is still useful, so it is shown with a warning rather than discarded.
    """
    limit = state.max_tokens
    if text.strip():
        message = (f"The reply was cut off at the {limit}-token output limit — "
                   f"what you see above is incomplete.")
    else:
        # Reached only after the automatic escalation has already been spent,
        # so "raise the limit" is no longer the useful advice — the shape of
        # the request is.
        message = (
            f"Still no reply within {limit} output tokens. The model is "
            f"spending the whole budget reasoning. Ask for one piece at a time "
            f"(a single section rather than a whole document), set "
            f"AGENT_MAX_TOKENS higher, or switch to a non-reasoning model.")
    state.last_error = f"truncated at max_tokens={limit}"
    yield ErrorOccurred(message, detail=state.last_error, recoverable=True)


def _report_empty_reply(state: AgentState) -> Iterator[AgentEvent]:
    """Say that the model returned nothing at all.

    Not the same as a truncation, which `_report_truncation` already covers by
    reading `stop_reason == "max_tokens"`. This is the case where the provider
    hands back a well-formed response containing no text, no tool call and no
    stop reason — measured on `nvidia/nemotron-nano-12b-v2-vl:free` via
    OpenRouter, which answers `finish_reason: null`, `content: null`,
    `tool_calls: null` and no usage block.

    Without this the turn ends `TurnStarted → ThinkingStarted → TurnFinished`
    and the user sees a spinner, then their prompt again. That is the exact
    shape of failure `_report_truncation` was written for — "the user saw a
    token counter and nothing else, asked 'where is the file?', and got
    another empty turn" — arriving through a different door.

    Recoverable, because the next message often works: this is usually the
    model, not the configuration.
    """
    state.last_error = "empty reply from the provider"
    yield ErrorOccurred(
        "The model returned an empty response — no text, no tool call, and no "
        "reason given. Nothing was wrong with the request, so trying again "
        "often works; if it keeps happening, this model is not usable here "
        "and /model will switch it.",
        detail=state.last_error, recoverable=True)


#: Phrases that promise the *next* action rather than describe a finished one.
#: Deliberately narrow: these announce work about to start, in the two
#: languages this agent is used in. A reply that merely mentions "let me know"
#: or ends "готово" must not match.
_ANNOUNCEMENT_RE = re.compile(
    r"(?:^|[\s\"'(«])(?:"
    r"let me\s+(?!know|have)\w+|now\s+(?:i'?ll|let me|i\s+will)|"
    r"i'?ll\s+(?:now\s+)?(?!know)\w+|"
    r"i\s+will\s+now|next[,\s]+i'?ll|let'?s\s+(?:now\s+)?\w+|"
    r"(?:зараз|тепер|далі|потім)\s+(?:я\s+)?(?=\w)|"
    r"перейду\s+до|почну\s+з|давайте\s+\w+|"
    r"створю|побудую|запущу|сформую|перевірю|прочитаю|згенерую|напишу|"
    r"додам|виправлю|оновлю|порівняю|проаналізую|відкрию|збережу|видалю|"
    r"розгляну|зроблю|складу|підготую|виміряю|відрендерю|сконвертую"
    r")\b",
    re.IGNORECASE)
#: The Ukrainian half above needed widening after a measured loss. `тепер я`
#: required the pronoun and Ukrainian drops it — the reply that ended one
#: session was "Тепер перевірю обсяги тексту…", announcement, no pronoun, and
#: `перевірю` was not in the verb list either. The first-person perfective
#: futures are listed explicitly rather than matched by an `-ю` suffix rule,
#: because that suffix also ends ordinary nouns and adjectives ("з
#: навігацією", "цією моделлю") and a suffix rule fired on almost every
#: sentence.


#: Shortest final line that can still be read as a sentence cut off in
#: flight. Below it, an unpunctuated last line is a sign-off, a heading, a
#: filename or a list item. Shared with `session_manager.audit_transcript`,
#: which must reach the same verdict about the same transcript.
MIN_UNFINISHED_LINE = 40


def _ends_mid_sentence(text: str) -> bool:
    """Does this text stop in the middle of a sentence?"""
    stripped = (text or "").rstrip()
    if not stripped or not stripped[-1].isalpha():
        return False
    return len(stripped.rsplit("\n", 1)[-1].strip()) >= MIN_UNFINISHED_LINE


def ended_mid_work(text: str, tool_calls_made: int,
                   stop_reason: Optional[str]) -> bool:
    """The turn stopped in the middle of doing something, rather than finishing.

    Sibling of `announces_without_acting`, for the case where the reply is not
    a well-formed announcement because it is not well-formed at all. Measured
    on `deepseek-v4-flash-free`, the turn ended on

        "Структура повністю зрозуміла. Тепер перевірю обсяги тексту
         (target_chars) для кожного блоку, щоб написати відповідний"

    — cut off mid-phrase, no tool call, alongside 4,000 characters of
    `reasoning_content` carrying a complete eight-step plan. The model had
    decided what to do and spent its output budget deciding it. Nineteen of
    forty tool calls and 394 of 1200 seconds were still unspent, and the
    session was saved `complete: true` with two scaffolding files and no
    deliverable.

    Two signals, either of which is enough once the turn has already run
    tools:

    * `max_tokens` — the provider says it was cut off. Checked even when the
      visible text is empty, which is the shape a reasoning model truncates
      in, and why this cannot live inside an `if text:` branch.
    * the last line is long and ends on a letter — no full stop, no closing
      bracket, no code fence. A sentence that finished does not.

    The length floor is not decoration. "Ends on a letter" alone flagged a
    complete, correct session whose report closed with the user's own
    required sign-off, `My Lord` — seven characters, no period. Sign-offs,
    headings, bare filenames and list items are all short; a clause cut off
    in flight is not. `MIN_UNFINISHED_LINE` sits above all of them.

    `tool_calls_made` is the same guard `announces_without_acting` uses and
    for the same reason: a first reply that answers in prose and stops is an
    answer, not an interruption.
    """
    if not tool_calls_made:
        return False
    if stop_reason == "max_tokens":
        return True
    return _ends_mid_sentence(text)


def announces_without_acting(text: str, tool_calls_made: int) -> bool:
    """A reply that says what it is about to do, and then does nothing.

    Measured on `hy3-free`, twice inside one session: the turn ended with
    "Now I understand the structure: …" and, after a `continue`, with "I now
    fully understand the workflow. Let me create the target directory and
    build the content plan JSON…" — no tool call either time. The session was
    saved `complete: true` with `failed_turns: []` and produced no file at
    all, because the integrity check looks for orphaned *user* turns and this
    turn has a perfectly good assistant reply on the end of it.

    `tool_calls_made` guards the common false positive: a first reply that
    proposes a plan and waits for approval is a legitimate answer, and the
    user asked for a plan. A turn that has already run tools and *then*
    announces the next one has stopped mid-work instead.
    """
    if not tool_calls_made or not text:
        return False
    tail = text.strip()[-400:]
    return bool(_ANNOUNCEMENT_RE.search(tail))


#: What to say back, per reason a turn is being continued instead of ended.
_NUDGE_TEXT = {
    "announced":
        "You described the next step but did not take it. Do it now "
        "with the tools — no more planning, no restating the plan. "
        "If you genuinely cannot proceed, say exactly what is "
        "blocking you.",
    "truncated":
        "Your last reply was cut off before you finished it, and no tool "
        "was called. Do not repeat what you already wrote and do not "
        "restate the plan — take the next action now, with the tools. If "
        "you were about to write a long file, write it in pieces. If you "
        "genuinely cannot proceed, say exactly what is blocking you.",
}


def _nudge_to_act(state: AgentState, reason: str = "announced") -> None:
    """Ask for the interrupted step, once, in the transcript itself.

    The same mechanism `_deadline_wind_down` uses: a user-role message the
    model answers on the next pass. Cheaper and far more reliable than
    prompting the model not to do it in the first place, which the system
    prompt already asks for.

    The truncation wording differs on one point that matters: a cut-off reply
    is *already* in the transcript, so "carry on" reads as "write it again"
    and the model re-emits the same paragraph into the same limit. It has to
    be told to move, not to resume.
    """
    state.messages.append({"role": "user", "content": [
        {"type": "text", "text": _NUDGE_TEXT.get(reason, _NUDGE_TEXT["announced"])}
    ]})


def _maybe_continue(state: AgentState, text: str, calls_used: int,
                    stop_reason: Optional[str],
                    nudged: bool) -> Iterator[AgentEvent]:
    """Continue a turn that stopped mid-work. Returns True when it did.

    One place, called from both model paths, because "may the turn keep
    going?" is a question the core owns — the same reasoning that put the
    budget checkpoint's `needs_continuation_approval()` here rather than in a
    responder. Both callers pass their own `stop_reason` (the streamed path
    reports it on `state`, the non-streamed one on the response) and act on
    the same answer, which is what `PathParity` asserts.

    Fires at most once per turn: `nudged` is the caller's flag, and an
    unbounded "keep asking" is a way to loop forever on a model that keeps
    narrating.
    """
    if nudged:
        return False
    if announces_without_acting(text, calls_used):
        reason = "announced"
    elif ended_mid_work(text, calls_used, stop_reason):
        reason = "truncated"
    else:
        return False
    yield AnnouncedWithoutActing(text, reason=reason)
    _nudge_to_act(state, reason)
    return True


def _can_escalate(state: AgentState, text: str, escalated: bool) -> bool:
    """Is this a truncation worth one more attempt with a bigger budget?

    Both an empty reply and a partial one qualify. This used to require the
    reply to be empty, reasoning that "a partial answer means it was writing,
    and re-running would re-bill the same work to get the same cut-off" — but
    the retry runs with *four times* the budget, so it does not get the same
    cut-off, which is the whole point. What that rule actually did was leave
    the one case users hit most with no recovery at all: a long document cut
    off mid-sentence is not a partial success, it is an unusable artifact, and
    the turn ended by advising an env var that was itself clamped and inert.

    The re-billing concern is real but bounded: `escalated` allows this once
    per turn, and MAX_OUTPUT_CEILING bounds where it can go.
    """
    return not escalated and state.max_tokens < MAX_OUTPUT_CEILING


def _escalate(state: AgentState, discarded: str = "") -> Iterator[AgentEvent]:
    """Quadruple the output budget for one retry, and say so.

    When a partial reply is being thrown away, that is announced separately
    and first: on the streamed path those characters have already been printed,
    and restarting the answer without a word looks like the agent repeating
    itself.
    """
    previous = state.max_tokens
    state.max_tokens = min(state.max_tokens * 4, MAX_OUTPUT_CEILING)
    if discarded.strip():
        yield TruncatedOutputDiscarded(len(discarded), previous, state.max_tokens)
        # "Retrying once with N" is the canonical phrase for an escalation and
        # is asserted on by both paths' tests — keep it identical in both
        # branches, or a message change silently stops meaning "we escalated".
        yield ErrorOccurred(
            f"The reply was cut off at the {previous}-token output limit after "
            f"{len(discarded)} characters, and that partial answer is being "
            f"discarded. Retrying once with {state.max_tokens}.",
            detail=f"escalating max_tokens {previous} -> {state.max_tokens} "
                   f"(discarded {len(discarded)} chars)",
            recoverable=True)
        return
    yield ErrorOccurred(
        f"No reply within {previous} output tokens — the model spent them "
        f"reasoning. Retrying once with {state.max_tokens}.",
        detail=f"escalating max_tokens {previous} -> {state.max_tokens}",
        recoverable=True)


def _recover_written_tool_calls(state: AgentState, text: str,
                                streamed: bool, reasoning: str = "") -> Iterator[AgentEvent]:
    """Lift a tool call the model typed out back into the tool channel.

    Returns a response shaped like one that asked for tools, or None when the
    reply really was an answer. Called at the one moment on each path where a
    turn is about to end without having called anything — which is precisely
    when a written-out call is the difference between a working model and a
    model that prints JSON at the user and stops.

    Both paths call it, and both hand the result into the *same* tool-execution
    code below: a recovered call is subject to permissions, loop detection and
    the budget checkpoint exactly like a real one. That is why this builds a
    response instead of running anything — recovery changes where a call came
    from, never what may be done with it.

    Deliberately last, after the truncation branch. A reply cut off at
    max_tokens can end mid-JSON, and half a tool call is not one; escalating
    first means the retry gets the chance to produce a complete call.

    `recover_text_tool_calls` builds its blocks purely from `text` — it has
    no way to know the call reasoned, so `reasoning_content` never reached
    the reassembled response without this: a thinking model that wrote its
    tool call as text (the exact quirk this function exists to recover from)
    lost its reasoning on the very same turn recovery saved the call on, and
    the next request replaying that turn was rejected the same way a
    plain-text reply's dropped reasoning was.
    """
    recovered = recover_text_tool_calls(text, state.tools)
    if recovered is None:
        return None
    if reasoning and recovered.content:
        recovered.content[0]["reasoning_content"] = reasoning
    yield ToolCallsRecovered([b.name for b in recovered.recovered],
                             streamed=streamed)
    return recovered


def run_turn(state: AgentState, user_message: Optional[str] = None) -> Iterator[AgentEvent]:
    """Drive one user turn to completion, yielding events as it goes."""
    started = time.perf_counter()
    if user_message is not None:
        state.messages.append({"role": "user", "content": user_message})
        yield TurnStarted(user_message)

    calls_used = 0
    denials = 0          # per-turn, so the message can escalate
    extensions = 0       # budget extensions granted, asked-for or automatic
    budget = state.tool_budget
    signatures: list[str] = []
    escalated = False    # max_tokens has been raised once for this turn
    # Once per turn, like escalation and for the same reason: a nudge that
    # can fire repeatedly is a way to loop forever on a model that keeps
    # narrating.
    nudged = False
    # A partial reply thrown away to retry with more room. Kept so that a
    # retry which then fails outright (quota, 5xx) does not leave the user with
    # less than they had before it — half a document beats nothing.
    discarded = ""

    state.turn_deadline = (time.monotonic() + state.max_turn_seconds
                           if state.max_turn_seconds > 0 else 0.0)

    while True:
        # Checked once per iteration — after a tool batch or a continuation
        # extension, before spending another model call — as well as inside
        # the streaming and tool-execution loops below, which is where an
        # Esc pressed mid-turn is actually seen.
        if state.interrupted():
            yield TurnFinished(reply="", usage=dict(state.usage),
                               seconds=time.perf_counter() - started,
                               overran_by=_overrun(state, started),
                               interrupted=True)
            return

        # Same position, same reason: decline to start the next step rather
        # than abandon one in flight. `text` is whatever the turn has produced
        # so far and is handed back — a turn stopped on time must not also
        # lose the work it had done.
        if turn_expired(state):
            salvaged = yield from _deadline_wind_down(state)
            reply = salvaged or discarded
            yield from _report_deadline(state, reply)
            yield TurnFinished(reply=reply, usage=dict(state.usage),
                               seconds=time.perf_counter() - started,
                               overran_by=_overrun(state, started))
            return

        yield ThinkingStarted(state.get_model())

        # A response rebuilt from a tool call the model wrote as text, when
        # there is one. Reset per iteration: the next step gets its own answer.
        recovered = None
        # The streamed assembly, when it was complete enough to use as-is.
        # Same lifetime and the same reason: per-iteration, never carried over.
        served = None

        # ── Streaming attempt ──
        if state.streaming_enabled:
            try:
                text, wants_tools, served = yield from _stream_call(state)
                if state.interrupted():
                    # Whatever text arrived stays; a tool_use block that had
                    # started is dropped rather than completed non-streamed —
                    # acting on it after being told to stop would be worse
                    # than showing an incomplete answer.
                    if text:
                        state.messages.append(
                            {"role": "assistant",
                             "content": _text_reply_content(text, state.last_reasoning)})
                    yield TurnFinished(reply=text, usage=dict(state.usage),
                                       seconds=time.perf_counter() - started,
                               overran_by=_overrun(state, started),
                                       interrupted=True)
                    return
                if not wants_tools:
                    if state.last_stop_reason == "max_tokens":
                        if _can_escalate(state, text, escalated):
                            escalated = True
                            discarded = text
                            yield from _escalate(state, text)
                            continue
                        text = text or discarded
                        yield from _report_truncation(state, text)
                    recovered = yield from _recover_written_tool_calls(
                        state, text, streamed=True, reasoning=state.last_reasoning)
                    if recovered is None:
                        # Record the reply — without this the streaming path
                        # silently reintroduces the no-memory bug.
                        if text:
                            state.messages.append(
                                {"role": "assistant",
                                 "content": _text_reply_content(text, state.last_reasoning)})
                        elif state.last_stop_reason != "max_tokens":
                            yield from _report_empty_reply(state)
                        # Outside the `if text` above: a reasoning model that
                        # spends its whole budget thinking truncates with no
                        # visible text at all, and that is the case with the
                        # most left to salvage.
                        if (yield from _maybe_continue(
                                state, text, calls_used,
                                state.last_stop_reason, nudged)):
                            nudged = True
                            continue
                        yield TurnFinished(reply=text, usage=dict(state.usage),
                                           seconds=time.perf_counter() - started,
                               overran_by=_overrun(state, started))
                        return
                    # A recovered call needs no second request: the stream ran
                    # to completion, so its text is whole and the blocks built
                    # from it are too. This is the one kind of tool call that
                    # does not fall through, and the reason is that there is
                    # nothing a non-streamed repeat could add — it would re-ask
                    # the same question and be answered in text again.
                elif state.last_tool_args_truncated and _can_escalate(
                        state, text, escalated):
                    # The arguments were cut off, and the non-streamed repeat
                    # below is made at the *same* output limit — which is how
                    # three `write_file` calls carrying ~12 KB of content
                    # arrived empty twice each, once streamed and once not.
                    # Raise the limit and let the fall-through happen anyway:
                    # the second call is still the right answer for a mangled
                    # assembly, it just needed room. Re-streaming instead would
                    # discard the existing recovery for the ordinary case.
                    escalated = True
                    yield from _escalate(state)
                # Tools requested: use the streamed assembly when it is whole
                # (`served`), otherwise fall through to the non-streamed call,
                # which returns complete tool_use blocks. Either way the same
                # code below runs permissions, loop detection and the budget —
                # this chooses the response, it does not handle it.
            except Exception as e:
                # Two streaming failures that falling through cannot fix — the
                # non-streamed call spends another request to be told the same
                # thing. A model the provider does not serve is the second:
                # without this it also gets recorded as "this provider cannot
                # stream", which is a lie that outlives the model switch.
                if is_quota_error(e) or is_model_unavailable(e):
                    if is_model_unavailable(e):
                        state.model_unavailable = True
                    event = (_model_unavailable_error(e)
                             if is_model_unavailable(e) else _quota_error(e))
                    state.last_error = event.detail or event.message
                    yield event
                    # Same rule as the failed-retry path below: a discarded
                    # partial answer is returned rather than lost when the
                    # attempt that was to supersede it cannot run.
                    if discarded:
                        state.messages.append(
                            {"role": "assistant",
                             "content": _text_reply_content(discarded, state.last_reasoning)})
                        yield AssistantMessage(discarded)
                    yield TurnFinished(reply=discarded, usage=dict(state.usage),
                                       seconds=time.perf_counter() - started,
                               overran_by=_overrun(state, started))
                    return
                # Any other streaming failure is recoverable the same way: a
                # provider whose streaming endpoint 5xxs (or that has no
                # streaming API at all) still works fine non-streamed. Disable
                # it and fall through, rather than retrying the streaming call
                # three times and then giving up on the turn entirely.
                state.streaming_enabled = False
                state.streaming_error = str(e)
                state.streaming_error_retryable = is_retryable_error(e)
                yield StreamingDisabled(str(e))

        response = recovered or served
        if response is None:
            response = yield from _model_call(state)
        if response is None:
            # The retry that was meant to replace a discarded partial answer
            # never landed. Hand back what was thrown away rather than nothing:
            # escalation must not be able to leave the user worse off than not
            # escalating would have.
            if discarded:
                state.messages.append(
                    {"role": "assistant",
                     "content": _text_reply_content(discarded, state.last_reasoning)})
                yield AssistantMessage(discarded)
            yield TurnFinished(reply=discarded, usage=dict(state.usage),
                               seconds=time.perf_counter() - started,
                               overran_by=_overrun(state, started))
            return

        if response.stop_reason != "tool_use":
            text = "".join(b.text for b in response.content if hasattr(b, "text"))
            if response.stop_reason == "max_tokens":
                if _can_escalate(state, text, escalated):
                    escalated = True
                    discarded = text
                    yield from _escalate(state, text)
                    continue
                text = text or discarded
                yield from _report_truncation(state, text)
            recovered = yield from _recover_written_tool_calls(
                state, text, streamed=False, reasoning=_reasoning_of(response.content))
            if recovered is None:
                if text:
                    # The non-streamed response's own blocks already carry
                    # `reasoning_content` if the model reasoned (attached by
                    # `openai_to_anthropic`); reading it off `response.content`
                    # here is what used to get skipped by rebuilding a plain
                    # string unconditionally.
                    state.messages.append(
                        {"role": "assistant",
                         "content": _text_reply_content(text, _reasoning_of(response.content))})
                    yield AssistantMessage(text)
                elif response.stop_reason != "max_tokens":
                    yield from _report_empty_reply(state)
                # Same position and same reasoning as the streamed path: the
                # empty-text truncation is the one worth continuing, so this
                # cannot sit inside the `if text` branch. `PathParity` asserts
                # the two behave identically.
                if (yield from _maybe_continue(
                        state, text, calls_used,
                        response.stop_reason, nudged)):
                    nudged = True
                    continue
                yield TurnFinished(reply=text, usage=dict(state.usage),
                                   seconds=time.perf_counter() - started,
                               overran_by=_overrun(state, started))
                return
            response = recovered

        tool_blocks = [b for b in response.content if b.type == "tool_use"]
        if not tool_blocks:
            # `stop_reason == "tool_use"` with nothing to run. The loop below
            # would record an assistant turn and an empty result turn, change
            # nothing, and come back here — forever, since a turn that runs no
            # tool never reaches the budget checkpoint that would stop it.
            # Treat it as the reply it actually is and end the turn.
            text = "".join(b.text for b in response.content if hasattr(b, "text"))
            if text:
                state.messages.append(
                    {"role": "assistant",
                     "content": _text_reply_content(text, _reasoning_of(response.content))})
                yield AssistantMessage(text)
            else:
                yield from _report_empty_reply(state)
            yield TurnFinished(reply=text, usage=dict(state.usage),
                               seconds=time.perf_counter() - started,
                               overran_by=_overrun(state, started))
            return

        for b in tool_blocks:
            signatures.append(call_signature(b.name, b.input))
        calls_used += len(tool_blocks)

        # ── A genuine stuck loop is not negotiable ──
        loop_reason = detect_loop(signatures)
        if loop_reason:
            yield LoopDetected(loop_reason, signature=signatures[-1] if signatures else "")
            yield from _wind_down(state, response, f"Stopped: {loop_reason}")
            return

        # ── The budget is a checkpoint, not a ceiling ──
        if calls_used > budget:
            yield ContinuationNeeded(calls_used, budget)
            if state.needs_continuation_approval(extensions):
                granted = state.responder.ask_continue(
                    ContinuationNeeded(calls_used, budget))
            else:
                # Bypass mode: extend without asking, but still announce it —
                # a turn quietly running to 400 tool calls with no trace of why
                # is indistinguishable from a runaway.
                granted = True
            if granted:
                extensions += 1
                budget += state.tool_budget
                yield ContinuationGranted(calls_used, budget)
            else:
                yield from _wind_down(
                    state, response,
                    f"Stopped at the user's request after {calls_used} tool calls")
                return

        # ── Execute ──
        tool_results = []
        # Read-only calls nobody is being asked about can overlap. Their
        # ToolStarted events are emitted here, up front, because the work
        # begins for all of them at once; everything after this — results,
        # truncation, callbacks, the transcript — still runs in block order in
        # the single loop below, which remains the only place that handles a
        # tool result.
        parallel = _parallel_batch(state, tool_blocks)
        precomputed: dict = {}
        if parallel and not state.interrupted():
            for block in parallel:
                yield ToolStarted(block.id, block.name, block.input,
                                  state.risk_of(block.name, block.input),
                                  origin=state.origin_of(block.name),
                                  interactive=is_interactive_tool(block.name))
            precomputed = _run_parallel(state, parallel)

        for block in tool_blocks:
            done = precomputed.get(block.id)
            # A queued call that hasn't started yet is simply skipped — the
            # one already running (if any) is stopped from inside its own
            # handler (run_command polls this same signal to kill its
            # subprocess), not by anything here. One already *finished* in the
            # parallel batch is kept: the work is done and paid for, and the
            # transcript needs a result for every tool_use regardless.
            if done is None and state.interrupted():
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": "Not executed: interrupted by user (Esc).",
                })
                continue

            risk = state.risk_of(block.name, block.input)
            if done is None:
                yield ToolStarted(block.id, block.name, block.input, risk,
                                  origin=state.origin_of(block.name),
                                  interactive=is_interactive_tool(block.name))

            approved = True
            if done is None and state.needs_permission(block.name, block.input):
                decision = state.responder.ask(
                    PermissionNeeded(block.id, block.name, block.input, risk))
                if decision == "deny":
                    approved = False
                elif decision == "always_allow_this_call":
                    state.approvals.approve(block.name, block.input)

            t0 = time.perf_counter()
            if done is not None:
                result, ok, _elapsed_ms = done
            elif not approved:
                # "user denied this tool call" reads like a transient failure,
                # so the model rewrote the same command cosmetically and tried
                # again — six times in one observed turn, until the loop
                # detector stopped it. Say that a retry cannot succeed.
                denials += 1
                result = (
                    "Error: the user denied this tool call. Retrying the same "
                    "call — or a cosmetic variation of it — will be denied "
                    "again. Do not re-issue it. Either continue without this "
                    "tool, or explain what you wanted to do and why."
                )
                if denials >= 2:
                    result += (" Further tool calls this turn are unlikely to "
                               "be approved; summarise what you have instead.")
                ok = False
            else:
                try:
                    result = state.execute_tool(block.name, block.input)
                    # Handlers signal soft failure by *returning* "Error: ..."
                    # rather than raising, so "did not raise" is not success:
                    # four command timeouts and an unreachable MCP server all
                    # logged ok=True, and 116/116 OK became a tautology.
                    # agent.py's _tool_log already reads this prefix the same
                    # way; this makes `ok` agree with it.
                    ok = not (isinstance(result, str)
                              and result.lstrip().startswith("Error:"))
                except Exception as e:  # a tool must never kill the turn
                    result = f"Error: tool raised {type(e).__name__}: {e}"
                    ok = False
            # A parallel call reports the time it actually spent, not the
            # fraction of a second it takes to look its result up here.
            ms = (done[2] if done is not None
                  else int((time.perf_counter() - t0) * 1000))

            if state.on_tool_call:
                try:
                    state.on_tool_call(block.name, block.input,
                                       str(result)[:200], ms, ok)
                except Exception:
                    pass

            if isinstance(result, str) and len(result) > state.max_result_chars:
                yield ToolResultTruncated(block.name, len(result), state.max_result_chars)
                result = clip_result(result, state.max_result_chars)

            yield ToolFinished(block.id, block.name, result, ms, ok=ok,
                               error=None if ok else result)
            tool_result: dict = {
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result,
            }
            if not ok:
                # Without this the model has to notice failure by reading
                # prose. OpenAI-format upstreams ignore the key (the adapter
                # forwards only tool_use_id and content), so it is inert there.
                tool_result["is_error"] = True
            tool_results.append(tool_result)

        # The assistant's tool_use blocks MUST be in the transcript before the
        # tool results. OpenAI-format upstreams (everything behind zen_proxy)
        # translate tool_result into a `role: "tool"` message, which is only
        # valid as a response to a preceding message carrying `tool_calls`.
        state.messages.append({"role": "assistant", "content": response.content})
        state.messages.append({"role": "user", "content": tool_results})
