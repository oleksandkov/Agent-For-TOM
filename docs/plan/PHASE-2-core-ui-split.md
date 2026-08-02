# Phase 2 — Core / UI Split

**Goal:** the engine runs without a terminal, emits events instead of printing, and is testable without a live model.
**Effort:** 1-2 weeks.
**Depends on:** Phase 0. Should be done **before** Phase 3 and is a hard prerequisite for Phase 5.

This is the highest-leverage structural change in the plan, and it gets more expensive every day it is deferred.

---

## The problem

**The core cannot run without a terminal.** Measured:

| Coupling | Count |
|---|---|
| `print()` in `agent.py` | 71 |
| `print()` **inside `agent_loop` itself** (lines 1062-1220) | 12 |
| `print()` in `agent_cli.py` | 114 |
| `msvcrt` references | 6 in `agent.py`, 8 in `agent_cli.py` |
| blocking `input()` in `agent.py` | 3 |

`agent_loop` does not *return* a conversation — it **prints** one. Tool calls, permission prompts, retry notices, token counters and the assistant's reply all go straight to stdout. A GUI has literally no way to render a tool call, because the tool call *is* a `print` statement (`agent.py:1192`).

Three concrete consequences:

1. **A desktop app is impossible without rewriting the loop.** Phase 5 becomes a rewrite instead of an adapter.
2. **The core is barely testable.** All three Phase 0 blocking bugs survived into production because nothing could observe the loop's behaviour without a terminal and a live model.
3. **Permission prompts block on `input()`** (`agent.py:815`), inside the tool execution path, inside the agent loop. There is no way for a GUI, a test, or a headless run to answer them.

---

## The design

The core emits **events** and asks questions through an **interface**. Adapters render events and answer questions.

```
                   ┌────────────────────────────────┐
  user input  ───► │  core.run_turn() -> Iterator[   │
                   │      AgentEvent]                │
                   │   • no print()                  │
                   │   • no input()                  │
                   │   • no msvcrt                   │
                   └───────────────┬─────────────────┘
                                   │  events
              ┌────────────────────┼────────────────────┐
              ▼                    ▼                    ▼
      TerminalAdapter        DesktopAdapter        TestAdapter
      ANSI + msvcrt          IPC → Electron        collects to a list
      (today's UI)           (Phase 5)             (makes core testable)
```

### The event types

Create `core/events.py`:

```python
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentEvent:
    """Base class. Every event is a plain data object — no formatting,
    no colour, no assumption about where it will be displayed."""


@dataclass
class TurnStarted(AgentEvent):
    user_message: str

@dataclass
class TextDelta(AgentEvent):
    """A chunk of assistant text. Adapters concatenate or stream it."""
    text: str

@dataclass
class ThinkingStarted(AgentEvent):
    """Model call in flight — adapters can show a spinner."""
    model: str

@dataclass
class ToolStarted(AgentEvent):
    tool_use_id: str
    name: str
    args: dict
    risk: str
    origin: str = "built-in"        # or "MCP: <server>"

@dataclass
class ToolFinished(AgentEvent):
    tool_use_id: str
    name: str
    result: str
    ms: int
    ok: bool = True
    error: str | None = None

@dataclass
class PermissionNeeded(AgentEvent):
    """The core blocks on the adapter's answer via the PermissionResponder."""
    tool_use_id: str
    name: str
    args: dict
    risk: str

@dataclass
class RetryScheduled(AgentEvent):
    attempt: int
    max_attempts: int
    delay_s: float
    reason: str

@dataclass
class ContextCompacted(AgentEvent):
    before_tokens: int
    after_tokens: int

@dataclass
class LearnedSomething(AgentEvent):
    """Phase 3 emits this when a lesson is promoted. The terminal may ignore
    it; the desktop app can show a quiet, dismissible indicator."""
    kind: str
    summary: str

@dataclass
class TurnFinished(AgentEvent):
    reply: str
    usage: dict = field(default_factory=dict)
    seconds: float = 0.0

@dataclass
class ErrorOccurred(AgentEvent):
    message: str
    detail: str = ""
    recoverable: bool = True
```

### The permission interface

Permission is a **question**, not a print-plus-`input`. Define the responder as a protocol so every front end can implement it:

```python
# core/permissions.py
from typing import Protocol, Literal

Decision = Literal["allow", "deny", "always_allow_this_call"]


class PermissionResponder(Protocol):
    def ask(self, event: "PermissionNeeded") -> Decision: ...


class AutoApprove:
    """Headless / test / YOLO."""
    def ask(self, event) -> Decision:
        return "allow"


class DenyAll:
    """Safe default for unattended runs."""
    def ask(self, event) -> Decision:
        return "deny"
```

The terminal adapter implements `ask` with `input()`; the desktop adapter with a modal; the test adapter with a canned script.

### The core loop becomes a generator

```python
# core/loop.py
def run_turn(state: AgentState, user_message: str) -> Iterator[AgentEvent]:
    yield TurnStarted(user_message)
    state.messages.append({"role": "user", "content": user_message})

    while True:
        yield ThinkingStarted(state.model)
        response = call_model(state)              # no printing in here either

        if response.stop_reason != "tool_use":
            text = extract_text(response)
            state.messages.append({"role": "assistant", "content": text})
            yield TextDelta(text)
            yield TurnFinished(reply=text, usage=state.last_usage)
            return

        state.messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        for block in tool_use_blocks(response):
            risk = RISK_LEVELS.get(block.name, "high")
            yield ToolStarted(block.id, block.name, block.input, risk,
                              origin=tool_origin(block.name))

            if not state.auto_approves(block.name, risk):
                decision = state.responder.ask(
                    PermissionNeeded(block.id, block.name, block.input, risk))
                if decision == "deny":
                    tool_results.append(denied_result(block))
                    yield ToolFinished(block.id, block.name,
                                       "denied by user", 0, ok=False)
                    continue
                if decision == "always_allow_this_call":
                    state.remember_approval(block.name, block.input)

            t0 = time.perf_counter()
            result = execute_tool(block.name, block.input)
            yield ToolFinished(block.id, block.name, result,
                               int((time.perf_counter() - t0) * 1000))
            tool_results.append(tool_result_block(block, result))

        state.messages.append({"role": "user", "content": tool_results})
```

Note this structure bakes in the Phase 0 fix — the assistant message is appended in both paths — so the bug cannot silently return.

---

## Fix the permission model while you are in here

**Severity: MED (security)**

`check_permission` (`agent.py:798-821`) has a real flaw beyond the `input()` coupling:

```python
    if resp == "always":
        RISK_LEVELS[name] = "low"       # ← permanent, global risk downgrade
        return True
```

Answering "always" **rewrites the tool's risk tier for the rest of the process**. One "always" on `run_command` — approved for, say, `git status` — silently auto-approves *every* later `run_command`, any command at all, as long as `AUTO_APPROVE_LOW` is set. The user approved one call and got a blanket grant on the most dangerous tool in the system.

**Fix:** never mutate `RISK_LEVELS`. Track approvals separately, scoped to what the user actually saw:

```python
class ApprovalStore:
    """Session-scoped approvals. Never modifies risk tiers."""
    def __init__(self):
        self._approved: set[tuple[str, str]] = set()

    @staticmethod
    def _signature(name: str, args: dict) -> str:
        # Scope to the meaningful argument, not the whole payload — a user
        # approving `git status` should not thereby approve `rm -rf`.
        key = args.get("command") or args.get("file_path") or ""
        return f"{name}:{str(key)[:200]}"

    def is_approved(self, name: str, args: dict) -> bool:
        return (name, self._signature(name, args)) in self._approved

    def approve(self, name: str, args: dict) -> None:
        self._approved.add((name, self._signature(name, args)))
```

Change the prompt wording to match the new semantics — `[y/N/always for this exact command]`. If you want a genuine blanket grant, make it a separate explicit action (`/trust <tool>`) that says what it does.

---

## Implementation strategy — incremental, not a big bang

Do **not** rewrite `agent.py` in one pass. Strangle it:

**Step 1 — create the package skeleton.** `core/{events,loop,state,permissions,tools}.py`. Nothing imports it yet.

**Step 2 — move the loop, keep the shim.** Port `agent_loop` into `core/loop.run_turn` as a generator. Leave `agent.agent_loop` in place as a thin wrapper that drains the generator and prints, so nothing breaks:

```python
def agent_loop(system_prompt: str, messages: list) -> str:
    """Deprecated shim — kept so existing callers keep working."""
    from core.loop import run_turn
    from adapters.terminal import TerminalAdapter
    return TerminalAdapter().drive(run_turn(state_from(system_prompt, messages)))
```

**Step 3 — write `TerminalAdapter`** — a single `render(event)` dispatch that reproduces today's output exactly. All 12 prints from the loop move here, unchanged in appearance. Users should not notice this phase happened.

**Step 4 — write `TestAdapter`** (~20 lines). This is the payoff:

```python
class TestAdapter:
    def __init__(self, responder=None):
        self.events: list[AgentEvent] = []
        self.responder = responder or AutoApprove()

    def drive(self, gen) -> str:
        for ev in gen:
            self.events.append(ev)
        return next((e.reply for e in reversed(self.events)
                     if isinstance(e, TurnFinished)), "")

    def of(self, cls) -> list:
        return [e for e in self.events if isinstance(e, cls)]
```

Now assertions read like specifications:

```python
def test_denied_tool_does_not_execute(self):
    adapter = TestAdapter(responder=DenyAll())
    adapter.drive(run_turn(state, "delete everything"))
    self.assertEqual(adapter.of(ToolFinished)[0].ok, False)
```

**Step 5 — move the tool handlers** into `core/tools.py` and strip their printing. The inline result preview (`agent.py:1206-1213`) becomes the adapter's rendering of `ToolFinished`.

**Step 6 — port `agent_cli.py`.** The TUI (menus, provider pages, session browser) stays terminal-specific — that is fine and correct. What must move out is anything the desktop app will also need: provider management (Phase 4), session listing, skill listing, MCP management. Rule of thumb: **if a GUI would need it, it does not belong in `agent_cli.py`.**

**Step 7 — delete the shim** once nothing calls `agent.agent_loop` directly.

---

## Enforce it

Without enforcement this decays back within a month. Add a test:

```python
CORE_FILES = list(Path("core").rglob("*.py"))

def test_core_has_no_terminal_coupling(self):
    """The core must be renderable by a GUI. No printing, no stdin,
    no Windows-only keyboard APIs."""
    for path in CORE_FILES:
        src = path.read_text(encoding="utf-8")
        for banned in ("print(", "input(", "msvcrt", "\\033["):
            self.assertNotIn(banned, src, f"{path.name} contains {banned!r}")
```

Run it in CI. When someone needs to print from core, the failing test tells them to emit an event instead — which is exactly the conversation you want to have.

---

## Verification

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
.venv\Scripts\python.exe agent.py       # output must be visually identical to before
```

Then confirm the core is genuinely headless:

```python
# Must work with stdout closed and no TTY.
import os, sys
sys.stdout = open(os.devnull, "w")
adapter = TestAdapter()
reply = adapter.drive(run_turn(state, "list the files in this directory"))
assert reply and adapter.of(ToolFinished)
```

## Acceptance criteria

- [ ] `core/` contains no `print()`, `input()`, `msvcrt`, or ANSI escapes, enforced by a test.
- [ ] `run_turn` is a generator yielding typed events.
- [ ] The terminal UI is visually unchanged for the user.
- [ ] A full turn with tool calls runs to completion with stdout redirected to `devnull`.
- [ ] Permission decisions flow through `PermissionResponder`; `DenyAll` and `AutoApprove` both work headlessly.
- [ ] Answering "always" no longer mutates `RISK_LEVELS`.
- [ ] Phase 0's regression tests are rewritten against `TestAdapter` and still pass.

## Next

Phase 3 — [real learning](PHASE-3-real-learning.md), now testable from day one.
