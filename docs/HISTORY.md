# TOMAS — Development History

A condensed record of the work that produced the current codebase. It replaces the
eight `docs/plan/PHASE-*.md` files, `IMPROVEMENT_PLAN.md`, `QA_REPORT.md`, the three
`TOMAS_SIMULATION_REPORT*.md` files, and the four one-off analysis notes from
August 2026. Those documents described work that is now finished; the code and
tests are the current source of truth. This file exists so the *reasoning* behind
the architecture is not lost.

For how the system works today, read `README.md`, `AGENTS.md` and `CLAUDE.md`.

---

## The starting point (2026-08-02)

A QA pass against branch `prototype2-refactoring` found TOMAS **architecturally
complete but non-functional end to end**. Tools, MCP, sessions, memory, skills and
the installer were all in reasonable shape, but three defects in the agent loop
meant the first message a user sent failed after ~70 s of retries, and every
tool-using turn after it failed too.

The improvement plan written alongside it set five goals that still govern the
project: connect almost any provider; make self-improvement + memory the
differentiator; support MCP and skills like the larger agents; stay small; keep
the architecture ready for a terminal-free desktop app.

Five rules were adopted for every phase, and they still hold:

1. No `print()` in core code.
2. No `input()` in core code — permission requests are events with a response channel.
3. One mechanism per job. A second mechanism for a job that already has one is how an agent gets large.
4. Every bug fixed gets a test.
5. User state lives in `~/.tomas/`, never in the source directory — the updater replaces the source directory wholesale.

---

## Phase 0 — Make it work · delivered 2026-08-02

Three blocking bugs, under 40 lines of fixes between them.

| Bug | Effect |
|---|---|
| The agent never recorded its own turns | Conversation memory was silently absent |
| Streaming was 100% broken and the fallback never fired | Every request 502'd |
| `UnicodeEncodeError` on non-UTF-8 Windows consoles | Hard crash on output |

Result: tool round-trip 7.8 s (was total failure), streaming 2.4 s, conversation
memory confirmed. 17 regression tests were written rather than the 3 sketched.

Two findings worth keeping: the tool-limit nudge is merged into the *same* user
message as the tool results so the transcript keeps alternating cleanly; and the
Zen SSE path sends `Connection: close`, because that server speaks HTTP/1.0 with no
`Content-Length` — end of stream *is* the socket closing, so advertising keep-alive
leaves clients waiting forever.

## Phase 1 — Close the learning loop · delivered

The self-improvement machinery recorded interactions, detected patterns and wrote
notes, but almost none of it came back to the model. This phase connected the
existing pipes rather than redesigning them: generated skills were written to a
directory the skill loader never scanned; self-notes were never injected and were
the wrong shape; `providers.json` was destroyed by every update; and the whole
interaction log was re-read and re-analysed on every single message.

## Phase 2 — Core / UI split · delivered

The highest-leverage structural change in the plan. `agent_loop` did not *return* a
conversation, it **printed** one — 71 `print()` calls in `agent.py`, 12 of them
inside the loop itself, plus 114 in `agent_cli.py` and blocking `input()` for
permissions. A GUI had literally no way to render a tool call, because the tool
call *was* a print statement.

The engine now emits typed events (`core/events.py`) from a generator loop
(`core/loop.py`), with permissions as a request/response channel
(`core/permissions.py`) and conversation state in `core/state.py`. Rendering lives
in `adapters/` — `terminal.py` for the TUI, `test.py` for headless assertions.
This is what makes the core testable without a live model, and it is the hard
prerequisite for any desktop app.

## Phase 3 — Real learning · delivered

Replaced keyword counting with genuine learning; net *negative* effect on codebase
size (~700 lines deleted, ~380 added).

The old system extracted keywords minus a stop-word list, scored similarity by token
overlap, and filled in a Markdown template once a counter passed 3. It also ignored
the highest-quality signal available — the user correcting the agent — and kept four
stores, two of them write-only.

The replacement (`learning/`) makes the model the learner: reflection over
transcripts writes facts **with evidence**, corrections are mined as a free signal,
facts are promoted only once evidence supports them, and retrieval per turn is what
makes it scale. Inspectable via `/self-improve facts` and `/self-improve reflect`.

## Phase 4 — Providers and extensions · delivered 2026-08-03

All 9 acceptance criteria passed; test count 184 → 258.

- Provider logic was trapped in the TUI — eight functions in `agent_cli.py` that
  `agent.py` could not reach. Extracted into `provider_manager.py`.
- Capabilities were guessed by string-matching URLs and model names. They are now
  **probed**: streaming, tool use, system prompt, context window, tool ceiling.
  Nothing infers behaviour from a substring at runtime.
- Degrade, never fail.
- `zen_proxy` was folded into the provider layer as `openai_adapter.py`, doing the
  Anthropic↔OpenAI translation in-process with real incremental SSE. Verified live
  against OpenCode Zen: blocking, streaming (12 deltas over 100 ms), and a tool
  round-trip — **no daemon**. The standalone proxy remains only for pointing *other*
  tools at Zen (`TOMAS_ZEN_PROXY=1`).
- First-class local models (Ollama).
- Deliberately **no plugin system** — MCP is the one extension mechanism.
- Tool *selection* by relevance replaced arbitrary truncation, with withheld tools
  named to the model so a gap is recoverable rather than silent.
- MCP resources and prompts exposed, not just tools.
- One skill format everywhere.

## Phase 6 — Hardening from simulation · delivered 2026-08-03

Different in kind from the others: Phases 0–5 were derived from reading the code,
Phase 6 from **running** it. Evidence was 16 real multi-turn sessions —
786.7 KB, 524 messages, 209 tool calls — plus three agent-generated simulation
reports. Two of those reports contained claims the session files contradicted,
which is the main reason they are not preserved here: the sessions were the
evidence, the reports were commentary.

Fourteen acceptance criteria; test count 118 → 184. What the real sessions broke on:

- Sessions saved with the replies missing.
- The sandbox locked the agent out of its own memory in `~/.tomas/`.
- `run_command` reported failure as success — it now always returns `[exit N — ok|FAILED]` first.
- Shell output was decoded with the system codepage instead of UTF-8.
- `search_code` silently found nothing when pointed at a file.
- Windows shell quirks the agent had to rediscover three times — multi-line and
  nested-quote `python -c` payloads now round-trip through a temp dir outside the project.
- `edit_file` forced one call per edit site.
- Permission tiering cost 30–100 s of human wait per turn; risk is now resolved per
  call by `risk_for(name, params)` — `run_command` is classified from the command itself.
- Context budget spent on generated filler.
- MCP routed by first match across servers — collisions now resolve deterministically.
- Sessions recorded nothing debuggable; they now carry `turn_metrics` and `tool_log`.
- The test harness tested functions that did not exist, across five separate scripts.
  Replaced by one entry point, `tests.simulate`, where a missing name is a hard FAIL.

## Phase 7 — The chat itself, and Cyrillic · delivered 2026-08-03

Twelve acceptance criteria; test count 268 → 337, including 50 new Cyrillic tests.
Measured against the plan's own targets:

| | Before | After |
|---|---|---|
| Cyrillic keystrokes at the prompt | rejected outright | all accepted |
| Ukrainian keyword extraction | `[]` | real |
| Ukrainian tool selection | list order | by relevance |
| MCP connect | 21.5 s | 5.3 s |
| Startup error lines | 6 | 1 |
| `tests.simulate cyrillic` | 18/24 | 18/18 |

Also in this phase: PDF export no longer crashes on Cyrillic, stop words are no
longer English-only, tool calls no longer render Cyrillic as escape sequences, the
chat reads real terminal width, and a denied tool call now teaches the model
something instead of nothing.

## Phase 5 — Desktop app · not started

The only phase never begun. The decision it records is still the intended one: run
the core as a local daemon and talk to it over IPC (127.0.0.1 only, token auth);
do **not** embed Python in the UI process. The terminal adapter and the desktop UI
would speak the same event protocol. Phase 2 was the hard prerequisite and is done,
so this remains additive — UI, transport and packaging only, no agent logic.

---

## Later fixes (2026-08-04)

**Word/document generation.** `write_file` was being used to produce `.docx` — a zip
container of XML parts, so the bytes landed on disk but the file was not a valid Word
document, and the conversion step then failed against it. The
[Office-Word-MCP-Server](https://github.com/GongRzhe/Office-Word-MCP-Server) was
added as the `word-docs` MCP server (54 tools, stdio via `uvx`) and made a default
install. File/URL context support was verified and the PDF/PPTX/XLSX reading gap
closed. One finding worth keeping: the `convert_to_pdf` failure was never a
conversion failure.

**The stalled session.** A session where the agent stopped producing files was traced
to tool selection forgetting the task the moment the user confirmed it — the
confirmation message ("yes, do it") carried none of the original keywords, so the
tools needed for the job were no longer selected. Contributing causes: instructions
that encouraged clarifying questions, and narrate-then-stop behaviour. Fixed, with
444 unit tests and 39 integration tests passing.

**Ctrl+C → Esc Esc.** Ctrl+C was the wrong key to quit on: it is also copy, and it
fired mid-stream. Exiting is now Esc Esc.
