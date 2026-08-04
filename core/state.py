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

    system_prompt: str
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

    # Budgets and limits
    tool_budget: int = DEFAULT_TOOL_BUDGET
    max_result_chars: int = 100_000
    streaming_enabled: bool = True
    # Why streaming was turned off, when it was. A transient failure (429,
    # 5xx, timeout) is not evidence that the provider cannot stream, so the
    # host must be able to tell the two apart before persisting anything.
    streaming_error: Optional[str] = None
    streaming_error_retryable: bool = False
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

    def needs_permission(self, name: str, args: dict) -> bool:
        """True if this call must be put to the responder."""
        if self.yolo:
            return False
        if self.approvals.is_approved(name, args):
            return False
        if self.risk_of(name, args) == "low" and self.auto_approve_low:
            return False
        return True
