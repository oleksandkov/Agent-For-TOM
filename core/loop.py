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
from typing import Iterator, Optional

from .events import (
    AgentEvent,
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
    ToolFinished,
    ToolResultTruncated,
    ToolStarted,
    TruncatedOutputDiscarded,
    TurnFinished,
    TurnStarted,
)
from .state import CYCLE_WINDOW, REPEATED_CALL_LIMIT, AgentState

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
# runaway cannot bill an unbounded completion.
MAX_OUTPUT_CEILING = 32_768


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


def _record_usage(state: AgentState, usage) -> None:
    if not usage:
        return
    inp = getattr(usage, "input_tokens", 0) or 0
    out = getattr(usage, "output_tokens", 0) or 0
    state.usage["input"] = inp
    state.usage["output"] = out
    state.usage["total_input"] = state.usage.get("total_input", 0) + inp
    state.usage["total_output"] = state.usage.get("total_output", 0) + out
    state.usage["calls"] = state.usage.get("calls", 0) + 1


def _stream_call(state: AgentState) -> Iterator[AgentEvent]:
    """Stream one model response, yielding TextDelta as tokens arrive.

    Returns (full_text, has_tool_use) to the caller via `yield from`. Raises
    on providers that cannot stream so the caller can fall back.
    """
    text_parts: list[str] = []
    has_tool_use = False
    stop_reason = None
    usage_info = None

    with state.get_client().messages.stream(
        model=state.get_model(),
        max_tokens=state.max_tokens,
        system=state.system_prompt,
        tools=state.tools,
        messages=state.messages,
    ) as stream:
        for event in stream:
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

    wants_tools = has_tool_use or stop_reason == "tool_use"
    # Recorded rather than reported here: truncation is handled identically for
    # the streamed and non-streamed paths, and `run_turn` owns that decision.
    # Reporting it inside this function meant the streaming path announced the
    # failure and returned, so the escalation retry — which lived only in the
    # non-streamed branch — never ran for any provider that streams. Which is
    # most of them.
    state.last_stop_reason = stop_reason
    # When tools were requested the caller immediately re-issues the request
    # non-streamed to get complete tool_use blocks. Counting the streamed call
    # too would bill the same turn twice.
    if not wants_tools:
        _record_usage(state, usage_info)
    return "".join(text_parts), wants_tools


def _quota_error(err: Exception) -> ErrorOccurred:
    """The message a quota failure deserves: what happened and what to do."""
    return ErrorOccurred(
        "The provider's usage limit is exhausted — this is a quota, not a "
        "temporary blip, so retrying will not clear it. Switch provider or model "
        "with /provider or /model, or wait for the allowance to reset.",
        detail=str(err), recoverable=False)


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
        try:
            response = state.get_client().messages.create(
                model=state.get_model(),
                max_tokens=state.max_tokens,
                system=state.system_prompt,
                tools=state.tools,
                messages=state.messages,
            )
            _record_usage(state, getattr(response, "usage", None))
            return response
        except anthropic.InternalServerError as e:
            if is_client_error(e) and not is_retryable_error(e):
                yield fail(ErrorOccurred(
                    "The AI service rejected the request. The system prompt or "
                    "message may be too large. Try /compact to reduce context size.",
                    detail=str(e), recoverable=False))
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
            if is_quota_error(e):
                yield fail(_quota_error(e))
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
        state.messages.append({"role": "assistant", "content": text})
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
    # A partial reply thrown away to retry with more room. Kept so that a
    # retry which then fails outright (quota, 5xx) does not leave the user with
    # less than they had before it — half a document beats nothing.
    discarded = ""

    while True:
        yield ThinkingStarted(state.get_model())

        # ── Streaming attempt ──
        if state.streaming_enabled:
            try:
                text, wants_tools = yield from _stream_call(state)
                if not wants_tools:
                    if state.last_stop_reason == "max_tokens":
                        if _can_escalate(state, text, escalated):
                            escalated = True
                            discarded = text
                            yield from _escalate(state, text)
                            continue
                        text = text or discarded
                        yield from _report_truncation(state, text)
                    # Record the reply — without this the streaming path
                    # silently reintroduces the no-memory bug.
                    if text:
                        state.messages.append({"role": "assistant", "content": text})
                    yield TurnFinished(reply=text, usage=dict(state.usage),
                                       seconds=time.perf_counter() - started)
                    return
                # Tools requested: fall through to the non-streamed call, which
                # returns complete tool_use blocks.
            except Exception as e:
                # A quota is the one streaming failure that falling through
                # cannot fix — the non-streamed call spends another request to
                # be told the same thing. Stop here.
                if is_quota_error(e):
                    event = _quota_error(e)
                    state.last_error = event.detail or event.message
                    yield event
                    # Same rule as the failed-retry path below: a discarded
                    # partial answer is returned rather than lost when the
                    # attempt that was to supersede it cannot run.
                    if discarded:
                        state.messages.append(
                            {"role": "assistant", "content": discarded})
                        yield AssistantMessage(discarded)
                    yield TurnFinished(reply=discarded, usage=dict(state.usage),
                                       seconds=time.perf_counter() - started)
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

        response = yield from _model_call(state)
        if response is None:
            # The retry that was meant to replace a discarded partial answer
            # never landed. Hand back what was thrown away rather than nothing:
            # escalation must not be able to leave the user worse off than not
            # escalating would have.
            if discarded:
                state.messages.append({"role": "assistant", "content": discarded})
                yield AssistantMessage(discarded)
            yield TurnFinished(reply=discarded, usage=dict(state.usage),
                               seconds=time.perf_counter() - started)
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
            if text:
                state.messages.append({"role": "assistant", "content": text})
                yield AssistantMessage(text)
            yield TurnFinished(reply=text, usage=dict(state.usage),
                               seconds=time.perf_counter() - started)
            return

        tool_blocks = [b for b in response.content if b.type == "tool_use"]
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
        for block in tool_blocks:
            risk = state.risk_of(block.name, block.input)
            yield ToolStarted(block.id, block.name, block.input, risk,
                              origin=state.origin_of(block.name))

            approved = True
            if state.needs_permission(block.name, block.input):
                decision = state.responder.ask(
                    PermissionNeeded(block.id, block.name, block.input, risk))
                if decision == "deny":
                    approved = False
                elif decision == "always_allow_this_call":
                    state.approvals.approve(block.name, block.input)

            t0 = time.perf_counter()
            if not approved:
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
                    ok = True
                except Exception as e:  # a tool must never kill the turn
                    result = f"Error: tool raised {type(e).__name__}: {e}"
                    ok = False
            ms = int((time.perf_counter() - t0) * 1000)

            if state.on_tool_call:
                try:
                    state.on_tool_call(block.name, block.input,
                                       str(result)[:200], ms, ok)
                except Exception:
                    pass

            if isinstance(result, str) and len(result) > state.max_result_chars:
                yield ToolResultTruncated(block.name, len(result), state.max_result_chars)
                result = (result[:state.max_result_chars]
                          + f"\n[...truncated, full result was {len(result)} chars]")

            yield ToolFinished(block.id, block.name, result, ms, ok=ok,
                               error=None if ok else result)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result,
            })

        # The assistant's tool_use blocks MUST be in the transcript before the
        # tool results. OpenAI-format upstreams (everything behind zen_proxy)
        # translate tool_result into a `role: "tool"` message, which is only
        # valid as a response to a preceding message carrying `tool_calls`.
        state.messages.append({"role": "assistant", "content": response.content})
        state.messages.append({"role": "user", "content": tool_results})
