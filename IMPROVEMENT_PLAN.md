# TOMAS — Improvement Plan

**Date:** 2026-08-02
**Scope:** architecture review against the stated product goals, not a bug hunt
**Companion doc:** `QA_REPORT.md` (the three blocking runtime bugs — those gate everything below)

Goals this plan is written against:

1. Connect **almost any provider** and work as a general assistant that understands the user.
2. A **self-improving system + memory** as the core differentiator — the agent learns from interaction, writes skills and tips for itself, improves existing skills, and the user never sees the machinery.
3. Support **MCP, skills, plugins** like the bigger agents.
4. Stay **small**.
5. A **desktop app with no terminal** is coming — the architecture has to be ready.

---

## The one thing that matters most

**The self-improvement loop is open. It records, it analyses, it writes files — and then nothing reads them back.** Three separate dead ends, each verified by inspection:

| # | Dead end | Evidence |
|---|---|---|
| 1 | **Auto-generated skills can never be loaded** | `self_improve._register_skill` (`:553`) writes skills to `~/.tomas/self-improve/skills/` and records them in `skill-registry.json`. But `skills_manager.SKILL_DIRS` (`:15-20`) only scans `~/.claude/skills`, `~/.agents/skills`, and a VS Code prompts folder. `~/.tomas/self-improve/skills` **is not in the list**, and `skill-registry.json` is read by exactly one function — `get_auto_generated_skills()` — which is only used to print a count in the `/si` status screen. The docstring at `:556-558` claims "the skills_manager already scans global dirs"; it does not. |
| 2 | **Self-notes never reach the model** | `self_notes.get_notes_for_context()` (`:369`) exists specifically to build a prompt section. It is referenced **nowhere** in the codebase. `build_system_prompt()` (`agent.py:861-924`) never calls it. Every note the agent writes for itself is write-only. |
| 3 | **What *does* reach the model is noise** | The only self-improvement content actually injected (`agent.py:905-910`) is `get_active_tips()`, and those tips are template strings like *"You frequently use `read_file` (12×). Consider creating shortcuts or aliases for this tool."* (`self_improve.py:618-624`). That is advice addressed to a human developer, not an instruction that changes model behaviour. It costs context on every turn and teaches nothing. |

So the current state is: the agent generates skills it cannot use, writes notes it never reads, and injects tips that don't change what it does. **The differentiating feature is not wired up.**

The good news: the plumbing (logging, storage, thresholds, the `/si` UI) already exists. What's missing is the *return path*, and a change in what gets learned.

---

## Part 1 — Confirmed bugs and how to fix them

### Self-improvement / memory

**S1 · Generated skills are unreachable — HIGH**
*Fix:* add `Path.home() / ".tomas" / "self-improve" / "skills"` to `skills_manager.SKILL_DIRS`, and make `build_skills_section()` include them. One line plus a test that generates a skill and asserts it appears in the next system prompt. Without this test the bug silently returns.

**S2 · Self-notes never injected — HIGH**
*Fix:* call `self_notes.get_notes_for_context()` inside `build_system_prompt()` alongside the memory index. But do it **with retrieval, not a dump** — see Part 2.

**S3 · Tips are useless and cost context — MED**
*Fix:* delete the template tip generator (`self_improve.py:600-704`, ~100 lines) and replace it with LLM-written lessons (Part 2). This is a net *reduction* in code.

**S4 · The whole interaction log is re-read and re-analysed on every user message — HIGH (scaling)**
`record_user_message` → `get_all_interactions()` → `_read_jsonl()` reads the **entire** `interactions.jsonl` into memory, then `_maybe_analyze` runs `analyze_patterns()` over all of it (`self_improve.py:937-951, 102-118, 211`). This is O(total history) per message, forever, with no rotation or cap. It is fine at 50 interactions and unusable at 50,000 — and it runs synchronously in the user's turn latency.
*Fix:* keep a rolling window (last N interactions) plus a persisted aggregate; rotate `interactions.jsonl` at a size cap; run analysis **off the hot path** (after the reply is delivered, or on session end).

**S5 · The analysis trigger fires unpredictably — LOW**
`_maybe_analyze` gates on `len(interactions) % 5 != 0`, but `interactions.jsonl` holds *both* user messages and tool calls. A turn with 3 tool calls can step over the multiple of 5 and skip analysis entirely.
*Fix:* count user turns explicitly, or trigger on session end.

**S6 · Memory is dumped, never retrieved — MED**
`load_memory_index()` (`agent.py:930`) pastes the whole of `MEMORY.md` into every system prompt, and the individual memory files it points at are never read back automatically. When the index outgrows `MAX_MEMORY_CHARS` it is silently truncated, so memories disappear with no signal to anyone.
*Fix:* retrieval (Part 2). At minimum, log a warning when truncation drops entries.

**S7 · Everything is global, nothing is project-scoped — MED**
`~/.tomas/memory`, `~/.tomas/self-improve` and the session analysis are shared across every project. Patterns learned in project A leak into project B's prompt. *User-level* preferences ("prefers short answers", "speaks Ukrainian") should be global; *task-level* patterns should not.
*Fix:* two tiers — `~/.tomas/learned/global/` and `~/.tomas/learned/projects/<hash-of-path>/`.

**S8 · Privacy: full user messages are logged in plaintext forever — MED (design)**
`log_user_message` stores raw content in `~/.tomas/self-improve/interactions.jsonl` with no retention limit, redaction, or opt-out. Fine for a personal tool; a liability for a shipped desktop app that will inevitably ingest pasted keys and client data.
*Fix:* retention window, a `--no-learn` / incognito session flag, and a redaction pass for high-entropy strings before write.

### Provider layer

**S9 · `providers.json` is destroyed by every update — HIGH**
`agent_cli.PROVIDERS_CONFIG_PATH = PROJECT_DIR / "providers.json"` where `PROJECT_DIR = Path(__file__).parent` (`agent_cli.py:48, 301`) — i.e. the **source directory**. The updater replaces `$SrcDir` wholesale (`install.ps1:231`). Every provider the user has configured is wiped on upgrade. (It also means provider config is per-checkout, and it's why the repo's `providers.json` is sitting empty at `{"active": null, "providers": {}}`.)
*Fix:* move to `~/.tomas/providers.json`, next to the other user state that the updater correctly preserves. Add a one-time migration.

**S10 · Provider logic is trapped in the TUI — HIGH (architectural)**
Every provider function — `_load_providers_config`, `_activate_provider`, `_detect_provider`, `_update_provider_model` — lives in `agent_cli.py`. `agent.py` cannot switch providers; a desktop app would have to reimplement all of it; a headless run can't either.
*Fix:* extract `provider_manager.py`. This is prerequisite work for both goal 1 and goal 5.

**S11 · Provider detection is string-matching on URLs — MED**
`_detect_provider()` (`agent_cli.py:1523`) infers the provider from substrings in `ANTHROPIC_BASE_URL` (`"openrouter" in base`, `"zen" in base`…). Any self-hosted or unrecognised endpoint falls through to `"other"` and loses model lists, context windows and quirk handling.
*Fix:* store the provider type explicitly in `providers.json` (the field already exists) and treat URL sniffing as a last-resort fallback only.

### Not a bug, but load-bearing

**No plugin system exists.** Zero occurrences of "plugin" anywhere in the code or docs. See Part 4 — my recommendation is *not* to build one.

---

## Part 2 — Redesign the self-improvement system (the actual product)

### The core problem

The current system learns with **word counting**. `analyze_patterns` extracts keywords minus stop-words, computes similarity by token overlap, and when a counter crosses 3 it fills in a Markdown template. The output is necessarily generic — *"Always verify the path before calling `read_file`"* — because a keyword counter has no idea what happened in the conversation.

**You already have a language model in the loop. Make it the learner.** The heuristics can't produce an insight; the model can. This also makes the system *smaller*: ~600 lines of pattern/tip machinery collapses into roughly 150 lines of reflection + retrieval.

### The redesign, in four pieces

**1 · Reflection pass (write side).** At session end — or every N turns, off the hot path — send the session transcript to a *cheap* model with a strict output schema:

```json
{
  "user_preferences": [
    {"fact": "Prefers PowerShell commands over bash on Windows",
     "confidence": 0.8, "evidence": "corrected the agent twice in this session"}
  ],
  "corrections":  [
    {"what_i_did": "used bash syntax", "what_was_wanted": "PowerShell", "lesson": "..."}
  ],
  "skill_candidates": [
    {"name": "ps-file-ops", "trigger": "user asks for file operations on Windows",
     "body": "..."}
  ]
}
```

This is one extra API call per session against a small model. It costs almost nothing and produces material that is *actually specific to this user*.

**2 · Mine the highest-signal event you currently ignore: corrections.** When the user says "no", "not like that", "I meant…", re-asks the same thing, denies a tool call, or edits a file the agent just wrote — that is a labelled training example, free, and unambiguous. Nothing in the codebase detects any of it today. A correction detector feeding the reflection pass would be the single highest-value addition to the learning system.

**3 · Promotion with evidence (the anti-noise rule).** Never let one session write a permanent rule — LLM reflection will hallucinate preferences from a single ambiguous exchange. Use a staged store:

```
observed (1 session)  →  candidate (2-3 sessions)  →  active (confirmed)
```

Only `active` items enter the system prompt. Anything not re-confirmed within N sessions decays out. This is what makes the system trustworthy enough to run invisibly, which is the stated requirement.

**4 · Retrieval, not dumping (read side — the missing return path).** This is the piece that closes the loop. Before each turn, score stored lessons/skills/memories against the current user message and inject only the top 3-5:

```python
def build_learned_context(user_message: str, k: int = 5) -> str:
    candidates = load_active_lessons() + load_learned_skills() + load_memories()
    scored = [(relevance(user_message, c), c) for c in candidates]
    return render(top_k(scored, k))
```

Start with keyword/TF-IDF overlap — you already have `_extract_keywords` and `_similarity_score` and they are good enough for v1. Swap in embeddings later behind the same function signature. This keeps the prompt **flat in size** as knowledge grows, which is what makes the memory system viable long-term. Today's design gets slower and more expensive with every memory saved, until truncation silently starts dropping things.

### What "invisible to the user" should mean

Silent-by-default, but **inspectable and reversible**. Keep `/si` (and its desktop equivalent) as the window: what has been learned, when, from what evidence, and a one-click "forget this". An agent that silently accumulates wrong beliefs about a user with no way to see or correct them is worse than one that doesn't learn. This is also a genuine selling point over the closed competition.

---

## Part 3 — Memory system

Consolidate. There are currently **four** overlapping stores: `~/.tomas/memory/` (the `save_memory` tool), `~/.tomas/self-improve/` (patterns/tips/skills), `~/.tomas/self-notes/` (notes), and `~/.tomas/sessions/`. Three of them are trying to be the same thing, and two of them are never read.

Proposed single hierarchy:

```
~/.tomas/
  learned/
    global/preferences.jsonl     # durable facts about the user (confidence + evidence)
    global/skills/*.md           # learned skills, real frontmatter, discoverable
    projects/<hash>/notes.jsonl  # project-scoped lessons
  sessions/                      # raw transcripts (unchanged, already works)
  providers.json                 # moved out of the source dir (S9)
```

with **one** write API (`remember(kind, content, evidence)`) and **one** read API (`recall(query, k)`). Every subsystem goes through those two functions. That collapses three storage layers into one, deletes code, and makes retrieval a single place to optimise.

Keep the raw session transcripts — they are the training material for the reflection pass, and they're the one store that currently works correctly.

---

## Part 4 — MCP, skills, plugins

**Do not build a plugin system.** MCP *is* the plugin system, you already support it, and it has an ecosystem. A bespoke plugin API would be a second extension mechanism to document, secure and maintain — exactly the kind of thing that makes an agent large.

Three things to do instead:

1. **Fix the tool-budget problem properly.** Today 110 MCP tools get truncated to 22 on free tier (`agent.py:2310-2316`), chosen by arbitrary list order, with the model never told anything is missing. That's not a plugin system, it's a coin flip. Replace with **relevance-based tool selection**: score servers/tools against the session purpose (you already compute `purpose` and `keywords` in `analyze_session_purpose`) and load the top N. Same retrieval machinery as Part 2 — one mechanism, two uses.
2. **Make skills first-class and uniform.** One skill format, one directory contract, discovered from user dirs *and* the learned dir (S1). Then "the agent improves its own skills" and "the user installs a skill" are the same code path — which is the elegant version of the feature you're describing.
3. **Support MCP resources and prompts, not just tools.** `mcp_manager` only surfaces tools. Resources and prompts are cheap to add and are where a lot of the ecosystem's value sits.

---

## Part 5 — "Connect almost any provider"

Current design: Anthropic SDK + `base_url` override + `zen_proxy` translating Anthropic↔OpenAI. That covers Anthropic and every OpenAI-compatible endpoint, which is genuinely most of the market — the strategy is sound. What it needs:

- **Extract `provider_manager.py`** (S10) — config, activation, detection, model lists, capability flags.
- **A capability record per provider,** because the differences that break agents are not the URL: does it support streaming, tool use, parallel tool calls, system prompts, prompt caching, vision; what is the real context window; what is the tool-count ceiling. Today these are guessed by string-matching (`agent.py:2307` decides the tool cap by checking whether `"free"` appears in the model name). Store them, and let a **capability probe** fill them in on first connect — you already have the probe logic scattered in `_fetch_model_context_window`.
- **Degrade gracefully instead of failing.** The streaming bug in `QA_REPORT.md` is exactly this failure mode: a provider couldn't stream, and instead of falling back the agent died. With a capability record, "this provider can't stream" is data, not an exception.
- **Ollama / llama.cpp deserve first-class support.** They're OpenAI-compatible, they're free, they're local, and they're the natural default for a desktop app's offline mode.
- **Test against providers, not mocks.** A `pytest` matrix that runs one tool round-trip against each configured provider would have caught every provider bug found in the QA pass.

---

## Part 6 — Staying small

The instinct is right, but "small" needs a definition or it erodes. Concretely: **the core should stay under ~2,500 lines**, and everything else should be an adapter or a plugin.

Where the weight actually is today:

| File | Lines | Assessment |
|---|---|---|
| `agent_cli.py` | 2,644 | TUI. Should become a thin adapter over the core (see Part 7) — most of this is not core logic. |
| `agent.py` | 2,436 | Core + REPL + tools + permissions + slash commands all in one file. Split; the core is maybe 800 lines of it. |
| `self_improve.py` | 1,041 | Roughly 600 lines of keyword heuristics that the redesign **deletes**. |
| `zen_proxy.py` | 645 | Should be a general OpenAI-compat adapter inside `provider_manager`, not a separate HTTP daemon. |

The redesign in this document is net **negative** lines: delete the tip templates, the skill templates, the pattern taxonomy and the standalone proxy daemon; add reflection (~80 lines), retrieval (~60), promotion (~40), provider manager (~200 mostly moved).

The rule that keeps it small: **one mechanism per job.** One retrieval function serving memory, skills, and tool selection. One storage API. One extension mechanism (MCP). One event stream (below). Every time a second mechanism appears for a job that already has one, that's the bloat starting.

---

## Part 7 — Desktop-app readiness (do this before adding features)

This is the highest-leverage structural change, and it gets more expensive every day you wait.

**Today the core cannot run without a terminal.** Measured: 71 `print()` calls in `agent.py` (12 of them *inside* `agent_loop` itself), 114 in `agent_cli.py`, plus `msvcrt` keyboard handling in both, raw ANSI escapes everywhere, and `input()` calls for permission prompts. `agent_loop` doesn't return a conversation — it prints one. A GUI has literally no way to render a tool call, because the tool call is a `print` statement.

**The fix: make the core emit events and ask questions through an interface.**

```python
# core/events.py
@dataclass
class AgentEvent: ...
class TextDelta(AgentEvent):      text: str
class ToolStarted(AgentEvent):    name: str; args: dict; risk: str
class ToolFinished(AgentEvent):   name: str; result: str; ms: int
class PermissionNeeded(AgentEvent): name: str; args: dict; risk: str
class TurnFinished(AgentEvent):   reply: str; usage: dict
class LearnedSomething(AgentEvent): kind: str; summary: str
```

`agent_loop` becomes a generator: `def run_turn(msg) -> Iterator[AgentEvent]`. Then:

- **Terminal adapter** — renders events with ANSI, answers `PermissionNeeded` via `msvcrt`. (Today's UI, ~300 lines.)
- **Desktop adapter** — same events over a local WebSocket/IPC to Electron/Tauri, answers `PermissionNeeded` with a dialog.
- **Test adapter** — collects events into a list. *This is what makes the agent testable*, and would have caught all three blocking bugs in `QA_REPORT.md` in one test.

Two rules to enforce from now on: **no `print()` in core**, and **no `input()` in core** — permission requests are events with a response channel, not blocking reads.

Also worth deciding early: the desktop app should talk to the core over a **local IPC/WebSocket protocol**, not by embedding Python in the UI process. That gives you a headless daemon for free, remote/multi-device later, and a clean language boundary if the UI ends up in TypeScript. `zen_proxy` already proves the local-HTTP-service pattern works here.

---

## Part 8 — Suggested order

**Phase 0 — make it work (days).** The three blocking bugs in `QA_REPORT.md`: the missing assistant message, the streaming fallback, the Unicode crash. Nothing else matters until a tool round-trip completes. Add one integration test that drives a full turn through a stub client.

**Phase 1 — close the learning loop (1 week).** S1 (skills discoverable), S2 (notes injected), S9 (`providers.json` out of the source dir), S4 (analysis off the hot path). Small fixes; they turn a feature that does nothing into a feature that does something.

**Phase 2 — the core/UI split (1-2 weeks).** Events, generator-based `agent_loop`, terminal adapter, test adapter. Do this *before* the self-improvement rewrite, so the new system is testable from day one — and before the desktop app exists, so it isn't a rewrite under deadline.

**Phase 3 — real learning (2-3 weeks).** Reflection pass, correction detection, evidence-based promotion, retrieval. Delete the heuristics. This is where the product's actual differentiator gets built.

**Phase 4 — provider + extension maturity.** `provider_manager` with capability records, relevance-based tool selection, MCP resources/prompts, Ollama support.

**Phase 5 — desktop app.** By this point it is an adapter, not a rewrite.

---

## Closing note

The architecture is sound and the ambition is right — a small, provider-agnostic agent that genuinely learns its user is a real gap in the market, and most of the scaffolding is already here. The gap between the current state and the goal is not missing features; it's **return paths**. Things get written and never read: skills that can't be discovered, notes that never load, memories that get truncated away, tool calls the model never sees.

Fix the return paths first. The feature list is already long enough.
