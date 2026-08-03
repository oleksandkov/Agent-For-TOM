"""
The agent loop, as a generator of events.

`run_turn` keeps calling the model until it stops requesting tools, yielding a
typed event for everything that happens along the way. It never writes to the
console, never reads stdin, and never touches a keyboard API — rendering and
questions both belong to the adapter driving it.
"""

from __future__ import annotations

import json
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
    # When tools were requested the caller immediately re-issues the request
    # non-streamed to get complete tool_use blocks. Counting the streamed call
    # too would bill the same turn twice.
    if not wants_tools:
        _record_usage(state, usage_info)
    return "".join(text_parts), wants_tools


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
            if not is_retryable_error(e):
                yield fail(ErrorOccurred("Error communicating with the AI service.",
                                         detail=str(e), recoverable=False))
                return None
            if attempt >= MAX_RETRIES:
                yield fail(ErrorOccurred(f"The AI service is unavailable right now: {e}",
                                         detail=str(e), recoverable=False))
                return None
            delay = 5 * (2 ** attempt)
            yield RetryScheduled(attempt + 1, MAX_RETRIES, delay, str(e))
            time.sleep(delay)
        except Exception as e:
            if attempt < MAX_RETRIES and is_retryable_error(e):
                delay = 5 * (2 ** attempt)
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


def run_turn(state: AgentState, user_message: Optional[str] = None) -> Iterator[AgentEvent]:
    """Drive one user turn to completion, yielding events as it goes."""
    started = time.perf_counter()
    if user_message is not None:
        state.messages.append({"role": "user", "content": user_message})
        yield TurnStarted(user_message)

    calls_used = 0
    budget = state.tool_budget
    signatures: list[str] = []

    while True:
        yield ThinkingStarted(state.get_model())

        # ── Streaming attempt ──
        if state.streaming_enabled:
            try:
                text, wants_tools = yield from _stream_call(state)
                if not wants_tools:
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
                # Any streaming failure is recoverable the same way: a provider
                # whose streaming endpoint 5xxs (or that has no streaming API at
                # all) still works fine non-streamed. Disable it and fall
                # through, rather than retrying the streaming call three times
                # and then giving up on the turn entirely.
                state.streaming_enabled = False
                state.streaming_error = str(e)
                state.streaming_error_retryable = is_retryable_error(e)
                yield StreamingDisabled(str(e))

        response = yield from _model_call(state)
        if response is None:
            yield TurnFinished(reply="", usage=dict(state.usage),
                               seconds=time.perf_counter() - started)
            return

        if response.stop_reason != "tool_use":
            text = "".join(b.text for b in response.content if hasattr(b, "text"))
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
            if state.responder.ask_continue(ContinuationNeeded(calls_used, budget)):
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
                result = "Error: user denied this tool call."
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
