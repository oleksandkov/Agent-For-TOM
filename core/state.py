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

    # Tool execution, supplied by the host so core/ stays dependency-free.
    execute_tool: Callable[[str, dict], str] = lambda name, args: ""
    # Takes the args too: the risk of `run_command` is a property of the
    # command, not of the tool name.
    risk_of: Callable[[str, dict], str] = lambda name, args=None: "high"
    origin_of: Callable[[str], str] = lambda name: "built-in"

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
    # Why the last model call ended ("end_turn", "tool_use", "max_tokens").
    # Set by the streaming path so the caller can treat truncation the same
    # way it does on the non-streamed one.
    last_stop_reason: Optional[str] = None
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
