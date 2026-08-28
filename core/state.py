"""
Everything a turn needs, passed in explicitly.

The core deliberately does not import agent.py: the caller supplies the client,
the tool table and the execution callbacks. That is what makes the loop
runnable from a test, a GUI, or a headless script.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .permissions import ApprovalStore, PermissionResponder

# How many tool calls a turn may make before it stops to ask whether to keep
# going. This is a checkpoint, not a ceiling — see core.loop.run_turn.
DEFAULT_TOOL_BUDGET = 40

# Identical calls in a row that count as a stuck loop.
REPEATED_CALL_LIMIT = 3

# How many recent signatures to scan for an A→B→A→B style cycle.
CYCLE_WINDOW = 6


@dataclass
class AgentState:
    """A single turn's execution context."""

    # Either plain text, or Anthropic content blocks when the host marks the
    # prefix with cache_control. The core passes it straight through to the
    # client and never inspects it, so both forms are equally fine here.
    system_prompt: "str | list"
    messages: list

    # Model access — callables so the caller can re-resolve per call
    # (provider switches, .env reloads) without rebuilding the state.
    get_client: Callable[[], Any]
    get_model: Callable[[], str]

    # Who answers permission and continuation questions. Deliberately has no
    # default: defaulting to AutoApprove would mean that *forgetting* to wire
    # a front end silently auto-approves every tool call, including
    # run_command. A permission system must not fail open — make the caller
    # name its policy (AutoApprove/DenyAll are there for when it really is
    # "approve everything" / "unattended").
    responder: PermissionResponder

    tools: list = field(default_factory=list)
    max_tokens: int = 8192

    # Sampling temperature, or None to send nothing and take the provider's
    # default.
    #
    # **Nothing set this, and the default is not neutral.** The adapter has had
    # a `temperature` passthrough all along and no caller ever supplied a
    # value, so every request this agent has ever made ran at whatever the
    # endpoint defaults to — 1.0 on the OpenAI wire. That is a reasonable
    # default for open-ended chat and a bad one for an agent producing a
    # structured document, and on a small quantised model it is the difference
    # between a document and token soup. Measured on
    # `nemotron-3.5-lightning-free` writing a Ukrainian lab guide, one reply
    # contained:
    #
    #     一 원시,奈す utilities+ ไrire. ی 1 conto create, export আফ, ե
    #
    # Korean, Chinese, kana, Thai, Perso-Arabic, Bengali and Armenian in one
    # line, and `現在考慮` reached the finished PDF. Strong models absorb a high
    # temperature; weak ones come apart, and the free tier is where the weak
    # ones are.
    #
    # `None` rather than a number as the dataclass default so that a host which
    # has not thought about it changes nothing — the value is chosen in
    # `agent.build_state`, where the env var is read.
    temperature: Optional[float] = None

    # Tool execution, supplied by the host so core/ stays dependency-free.
    execute_tool: Callable[[str, dict], str] = lambda name, args: ""
    # Takes the args too: the risk of `run_command` is a property of the
    # command, not of the tool name.
    risk_of: Callable[[str, dict], str] = lambda name, args=None: "high"
    origin_of: Callable[[str], str] = lambda name: "built-in"

    # May two calls to this tool run at the same time as each other?
    #
    # Defaults to "no". Concurrency has to be opt-in per host because the core
    # cannot know which of its injected tools share something: an MCP server is
    # one stdio pipe carrying JSON-RPC and two calls would interleave frames on
    # it, a browser tool shares a driver, a writer shares a file. Only the host
    # that supplied `execute_tool` knows, so only the host may say.
    parallel_safe: Callable[[str, dict], bool] = lambda name, args=None: False

    # Called with `messages` immediately before every model call, so the host
    # can re-cut prompt-cache breakpoints against history that has grown since
    # the turn began.
    #
    # It exists because the host only got one chance per *user turn*, while a
    # tool-using turn appends two messages per step — a 29-step turn added 58
    # messages behind marks that were placed before any of them existed, so the
    # part of the history that grew fastest was the part never cached. The hook
    # is injected rather than imported for the same reason `execute_tool` is:
    # the core must not know what a cache breakpoint is.
    #
    # A hint, never a decision: it may not change what is sent, only how the
    # provider is told to cut it, and the core ignores anything it raises.
    before_model_call: Optional[Callable[[list], None]] = None

    # Where requests are going, in words, for error messages only — e.g.
    # "Ollama (ollama) at http://localhost:11434/v1". A callable for the same
    # reason `get_model` is one: the endpoint changes under a running state
    # when the user switches provider. Injected rather than imported because
    # the core must not know that `provider_manager` exists; when no host
    # supplies it, the messages simply omit the endpoint.
    describe_endpoint: Optional[Callable[[], str]] = None

    # Permission policy
    approvals: ApprovalStore = field(default_factory=ApprovalStore)
    auto_approve_low: bool = True
    yolo: bool = False

    # Continuation policy. `yolo` answers "may this tool run?"; this answers
    # "may the turn keep going?" — a separate question the budget checkpoint
    # asks, and one yolo never silenced. A 56-tool-call session on
    # deepseek-v4-flash-free stopped at the 40-call checkpoint and was saved
    # with complete=False, because approving every tool does nothing about
    # being asked whether to carry on.
    auto_continue: bool = False
    # How many times the budget may be extended without asking. Unbounded
    # "never stop" is not a mode, it is a way to bill an unattended runaway:
    # the same session spent 3.3M input tokens in two turns. At the default
    # tool_budget of 40 this still allows 400 tool calls in one turn, which is
    # past what any real task needs, and the loop detector remains in force.
    max_auto_continuations: int = 9

    # Budgets and limits
    tool_budget: int = DEFAULT_TOOL_BUDGET
    # One tool result's share of the window. At the old 100,000 a single
    # `run_command` or MCP call could add ~25,000 tokens to the transcript —
    # and unlike a long reply it stays there, re-sent on every later turn until
    # compaction. `read_file` already caps itself near 20,000 chars with a
    # resumable footer; this is the backstop for everything that does not.
    max_result_chars: int = 30_000
    streaming_enabled: bool = True
    # Why streaming was turned off, when it was. A transient failure (429,
    # 5xx, timeout) is not evidence that the provider cannot stream, so the
    # host must be able to tell the two apart before persisting anything.
    streaming_error: Optional[str] = None
    streaming_error_retryable: bool = False
    # Wall-clock ceiling for one turn, in seconds. 0 means unbounded, which is
    # what shipped and what let a single turn run for 75 minutes with nothing
    # on screen.
    #
    # A socket timeout does not bound this. It is 300 s, a timeout is
    # classified retryable, and `MAX_RETRIES` is 3 — so one stalled model call
    # costs up to 20 minutes, and the tool loop then makes another. Measured:
    # `mimo-v2.5-free` produced no output at all for 75 minutes on turn 1, and
    # `nemotron-3.5-lightning-free` spent 33 minutes and 89 tool calls on a
    # single instruction. Neither is distinguishable from a hang while it is
    # happening.
    #
    # Checked between steps rather than enforced on the socket: cancelling a
    # request mid-flight would throw away a reply that is still arriving, while
    # declining to start the *next* step costs nothing and leaves a well-formed
    # transcript.
    max_turn_seconds: float = 0.0

    # Absolute monotonic time this turn must stop by. Set by `run_turn` from
    # `max_turn_seconds`; 0.0 when unbounded.
    turn_deadline: float = 0.0

    # Set when a call failed because the provider does not serve this model at
    # all. Reported rather than acted on: `core/` cannot write to a catalogue
    # without importing one, and the host already reads this state back after
    # every turn. Without it the detection was per-call and forgotten — the
    # model picker went on offering `ling-3.0-tiny-free`, which is listed by
    # `opencode.ai/zen/v1/models` and answers `401 ModelError: Model
    # ling-3.0-tiny-free is not supported` to every request.
    model_unavailable: bool = False

    # Why the last model call ended ("end_turn", "tool_use", "max_tokens").
    # Set by the streaming path so the caller can treat truncation the same
    # way it does on the non-streamed one.
    last_stop_reason: Optional[str] = None
    # A thinking model's chain-of-thought from the last streamed call, if any.
    # Some upstreams (DeepSeek via Zen) reject the *next* request unless an
    # assistant turn that reasoned carries this back — see `_reasoning_of` and
    # `_stream_call` in `core/loop.py`. The streamed path only ever returns
    # joined text, so without somewhere to report this the reasoning had no
    # way to reach the assistant message `run_turn` saves for a plain-text
    # reply, and every following request in the conversation was refused.
    last_reasoning: str = ""
    # The last streamed call asked for a tool and its arguments did not
    # survive: the JSON never parsed, or the reply was cut off at the output
    # limit part-way through building the calls. Reported here rather than
    # acted on in `_stream_call`, for the same reason `last_stop_reason` is —
    # `run_turn` decides. It needs its own field because a truncated *tool*
    # call still reports `stop_reason: "tool_use"`, so the max_tokens branch
    # never sees it: measured across two sessions, three `write_file` calls
    # carrying ~12 KB of content arrived as `{}` or without their `content`
    # argument, were re-requested non-streamed at the same output limit, and
    # truncated again.
    last_tool_args_truncated: bool = False
    # What ended the turn, when something did. Without this a turn that died
    # on a 429 was saved as "empty_reply" with no reason attached, which is
    # exactly the unreadable record P6-11 exists to prevent.
    last_error: Optional[str] = None

    # Accumulated during the turn
    # "input"/"output" are the most recent call (what's actually in the context
    # window); "total_*" accumulate across every call the turn made.
    usage: dict = field(default_factory=lambda: {
        "input": 0, "output": 0, "total_input": 0, "total_output": 0, "calls": 0,
    })
    # (name, args, result_preview, duration_ms, ok). The host records this;
    # without duration and outcome a saved session cannot say which call was
    # the slow one or which one failed.
    on_tool_call: Optional[Callable[[str, dict, str, int, bool], None]] = None

    # "Stop as soon as it's safe to." The core has no keyboard access (see
    # loop.py's module docstring) and cannot cancel a socket read mid-flight,
    # so this is polled at checkpoints — before a new model call, between
    # queued tool calls, per streamed chunk — rather than force-killing
    # anything. An adapter's Esc-watcher thread is what makes it True.
    interrupted: Callable[[], bool] = lambda: False

    # This turn's `max_tokens` was lowered on purpose (core.features'
    # every-third-reply cap), so a `max_tokens` stop is the expected outcome
    # rather than a failure.
    #
    # It needs its own flag because `max_tokens` alone cannot say why it is
    # small: `_can_escalate` fires on any truncation and retries at 4x the
    # budget, which would undo the cap on the very turn it was applied and
    # report a scary "the reply was cut off" for something the user asked for.
    # Set by the host in `build_state`; read by `_can_escalate` and
    # `_report_truncation`, which is the same report-vs-decide split the rest
    # of the turn path uses.
    reply_capped: bool = False

    def needs_permission(self, name: str, args: dict) -> bool:
        """True if this call must be put to the responder."""
        if self.yolo:
            return False
        if self.approvals.is_approved(name, args):
            return False
        risk = self.risk_of(name, args)
        # "none" is a tier below "low": a call with no side effects at all
        # (e.g. asking the user a question) never needs approval, in any
        # mode — unlike "low" it does not depend on auto_approve_low, because
        # gating it on that would put a permission prompt in front of the
        # interaction that *is* the human-in-the-loop control.
        if risk == "none":
            return False
        if risk == "low" and self.auto_approve_low:
            return False
        return True

    def needs_continuation_approval(self, extensions_used: int) -> bool:
        """True if the user must be asked before the turn keeps going.

        Kept here rather than in the responder for the same reason
        `needs_permission` is: it is policy, and policy the core owns can be
        tested without a front end. A responder that silently always said yes
        would make every adapter — including DenyAll — a place where this
        decision could be got wrong.
        """
        if not self.auto_continue:
            return True
        return extensions_used >= self.max_auto_continuations
