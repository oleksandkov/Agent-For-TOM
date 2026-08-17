"""
Typed events emitted by the agent core.

Every event is a plain data object — no formatting, no colour, no assumption
about where it will be displayed. The terminal adapter renders them with ANSI,
a desktop adapter would render them as widgets, and the test adapter just
collects them into a list.

Nothing in this module may print, read stdin, or import a UI library.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AgentEvent:
    """Base class for everything the core emits."""


@dataclass
class TurnStarted(AgentEvent):
    user_message: str


@dataclass
class ThinkingStarted(AgentEvent):
    """A model call is in flight — adapters can show a spinner."""
    model: str


@dataclass
class TextDelta(AgentEvent):
    """A chunk of assistant text. Adapters concatenate or stream it."""
    text: str


@dataclass
class AssistantMessage(AgentEvent):
    """A complete assistant reply that did not arrive as deltas."""
    text: str


@dataclass
class ToolStarted(AgentEvent):
    """A tool is about to run.

    `interactive` means the tool reads the console itself (see
    `core.console.INTERACTIVE_TOOLS`). An adapter MUST NOT draw a spinner or
    poll the keyboard while one of these runs: measured on a live session, the
    `Thinking` spinner ran throughout `ask_user_question`, stealing a keystroke
    and erasing the input line twelve times a second. The rule already existed
    for permission prompts — `TerminalAdapter.ask` stops the spinner first —
    and this is what extends it to a tool that prompts from the inside, where
    the adapter cannot otherwise tell.
    """
    tool_use_id: str
    name: str
    args: dict
    risk: str
    origin: str = "built-in"  # or "MCP: <server>"
    interactive: bool = False


@dataclass
class ToolFinished(AgentEvent):
    tool_use_id: str
    name: str
    result: str
    ms: int = 0
    ok: bool = True
    error: Optional[str] = None


@dataclass
class ToolResultTruncated(AgentEvent):
    name: str
    original_chars: int
    kept_chars: int


@dataclass
class PermissionNeeded(AgentEvent):
    """The core blocks on the adapter's answer via the PermissionResponder."""
    tool_use_id: str
    name: str
    args: dict
    risk: str


@dataclass
class ContinuationNeeded(AgentEvent):
    """The turn used its whole tool-call budget without finishing.

    The core asks rather than gives up: a task that legitimately needs many
    steps should be able to finish. Adapters answer via
    PermissionResponder.ask_continue().
    """
    calls_used: int
    budget: int


@dataclass
class ContinuationGranted(AgentEvent):
    """The budget was extended — emitted so the UI can say so."""
    calls_used: int
    new_budget: int


@dataclass
class LoopDetected(AgentEvent):
    """A genuine stuck loop — the same call, or a short cycle of calls,
    repeating with no progress. Unlike the budget, this is not negotiable."""
    reason: str
    signature: str = ""


@dataclass
class RetryScheduled(AgentEvent):
    attempt: int
    max_attempts: int
    delay_s: float
    reason: str


@dataclass
class StreamingDisabled(AgentEvent):
    """This provider cannot stream; the turn continues non-streamed."""
    reason: str = ""


@dataclass
class TruncatedOutputDiscarded(AgentEvent):
    """A partial reply is being thrown away and re-requested with more room.

    Adapters MUST render this. On the streamed path the discarded text has
    already gone to the screen token by token, so without a notice the user
    watches the answer restart and sees the first half of a document twice
    with no explanation of which copy to trust.
    """
    discarded_chars: int
    previous_limit: int
    new_limit: int


@dataclass
class AnnouncedWithoutActing(AgentEvent):
    """The reply described the next step instead of taking it, and the turn
    is asking for it once rather than ending there.

    Adapters SHOULD render this: the announcement itself has already reached
    the screen, so without a line saying the turn is continuing, the user
    sees the model repeat its plan and cannot tell whether anything is
    happening. Measured on `hy3-free`: two turns in one session ended this
    way, the session was saved `complete: true`, and no file was produced.

    `reason` says which shape it was: `"announced"` for a well-formed reply
    that describes the next step, `"truncated"` for one cut off before it
    could take it — measured on `deepseek-v4-flash-free`, which ended a turn
    mid-phrase with nineteen of forty tool calls unspent. They read very
    differently on screen and an adapter should say which happened.
    """
    announcement: str
    reason: str = "announced"


@dataclass
class ToolCallsRecovered(AgentEvent):
    """The model wrote its tool call as text and the core lifted it back out.

    Adapters SHOULD render this, quietly. It is the difference between a model
    that works and a model that works *by exception*: a session full of these
    is a session where the model never once used the tool-call channel, which
    is worth seeing before it is blamed on the agent. It also tells the user
    why a tool is about to run when nothing on screen appeared to ask for one.
    """
    names: list[str]
    streamed: bool = False


@dataclass
class ContextCompacted(AgentEvent):
    before_tokens: int
    after_tokens: int


@dataclass
class LearnedSomething(AgentEvent):
    """Phase 3 emits this when a lesson is promoted. The terminal may ignore
    it; a desktop app can show a quiet, dismissible indicator."""
    kind: str
    summary: str


@dataclass
class TurnFinished(AgentEvent):
    reply: str
    usage: dict = field(default_factory=dict)
    seconds: float = 0.0
    # Set when the turn ended early because the host's interrupt signal
    # (e.g. an adapter's Esc watcher) was seen, rather than reaching a
    # natural end_turn/error. Adapters use it to render "stopped" rather
    # than "finished".
    interrupted: bool = False
    #: Seconds spent past `max_turn_seconds`, when the ceiling was passed.
    #: The ceiling is checked between steps, so a step that starts just
    #: inside it can finish well outside — measured, 1279.8s against a
    #: 1200s limit, saved as a clean success with nothing saying so. Zero
    #: when the turn stayed inside its allowance or had none.
    overran_by: float = 0.0


@dataclass
class ErrorOccurred(AgentEvent):
    message: str
    detail: str = ""
    recoverable: bool = True
