# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

TOMAS (Terminal Operated Modular Agent System) — a self-hosted AI coding agent written in Python 3.10+, built on the same architecture as Claude Code (agent loop, tool calling, MCP integration, skill system). See `AGENTS.md` for the full architectural deep-dive (system prompt load order, tool risk tiers, quirks); this file covers day-to-day conventions.

## Commands

- Run the agent: `python agent.py` (direct REPL) or `python agent_cli.py` (TUI with provider/MCP/session menus)
- Uses `.venv\Scripts\python.exe` as the interpreter — activate `.venv` before running scripts, or invoke it directly
- Install deps: `pip install -r requirements.txt`
- Run tests: `python -m unittest discover -s tests -p "test_*.py"`; `python test_agent.py` runs the non-interactive integration suite
- Run a single unit test: `python -m unittest tests.test_agent_units.<TestClass>.<test_method>`
- Simulation harness (one entry point, replaced five root scripts):
  - `python -m tests.simulate checks [--offline]` — capability checks, writes `simulation_results.json`
  - `python -m tests.simulate cyrillic` — Ukrainian/Russian support sweep
  - `python -m tests.simulate sessions [--turns N] [--session <name>]` — live goal-driven sessions
  - It resolves every entry point up front; a missing name is a hard FAIL (exit 2), never a WARN

## Conventions

- Python 3.10+
- Keep code simple and readable; prefer functions over classes for small scripts
- Always read a file before editing it
- Prefer `edit_file` over `write_file` for existing files
- **Turn behaviour must be changed on both model paths, or on neither.**
  `core/loop.run_turn` calls the model streamed and non-streamed, and a change
  made in one branch silently does nothing on the other — an output-limit retry
  was added to the non-streamed branch, shipped, and had no effect at all for
  streaming providers, which is most of them. Two rules keep them together:
  `_stream_call` **reports** (it records `state.last_stop_reason`) and
  `run_turn` **decides**; and anything a turn does regardless of path gets a
  test in `tests/test_core_loop.py::PathParity`, which runs each assertion
  against both and names the failing path. Tool handling is the deliberate
  exception — a streamed call wanting tools falls through to the non-streamed
  one, so permissions, loop detection and continuation live in a single place.
- **Permission and continuation are two questions; a mode must answer both.**
  `yolo` only ever answered "may this tool run?". The budget checkpoint asks a
  second question — "may the turn keep going?" — which yolo left in place, so a
  session that auto-approved all 56 of its tool calls still halted at 40 and
  saved with `complete: false`. `bypass` mode sets `AgentState.auto_continue`
  as well, and the core decides via `needs_continuation_approval()` rather than
  a responder that always says yes: policy the core owns is testable without a
  front end, and cannot be got wrong per-adapter. It is bounded by
  `max_auto_continuations` (9 → 400 tool calls at the default budget) because
  an unbounded "never stop" is a way to bill an unattended runaway, and loop
  detection still applies. Add a mode by extending `MODE_CYCLE`/`ALL_MODES` and
  `set_mode()` — the badge, status line, banner and help all derive from
  `current_mode_name()`, so nothing else needs teaching.
- **A truncated turn escalates once, and never returns less than it had.**
  `_can_escalate` fires for a *partial* reply as well as an empty one — the
  retry runs with 4x the budget, so it does not reproduce the cut-off. Two
  invariants come with it: the discarded text is announced via
  `TruncatedOutputDiscarded` (on the streamed path it is already on screen, so
  silence reads as the agent repeating itself), and it is stashed so a retry
  that then fails outright hands it back rather than losing it — escalation
  must never leave the user worse off than not escalating. Note also that
  `Capabilities.max_output_tokens` is **never probed**: `effective_max_tokens`
  therefore lets an explicit `AGENT_MAX_TOKENS` win over it, because the old
  `min(...)` silently clamped every provider to 8192 while the truncation
  message advised raising that very variable.
- **Nothing that varies with the message may be emitted before something that
  does not.** `build_system_prompt` is built in two halves: a `stable` half
  (BASE_PROMPT, environment, instructions, skills catalogue) and a volatile
  `tail` (standing rules, retrieved facts, triggered skill body). Prefix caching
  matches on an exact byte prefix and the system prompt is serialised *before*
  the messages, so the first byte that differs from last turn ends the cache hit
  for the prompt **and the whole conversation behind it**. The skills catalogue
  used to sit last: measured on Zen/DeepSeek over five turns, 52% of prompt
  tokens came from cache; with the halves ordered, a settled cache serves 99.8%
  and bills 73 tokens instead of 14,864. The stable half is memoised on a
  `(path, mtime, size)` fingerprint — call `invalidate_prompt_cache()` if you
  change instructions in-process. Tests live in
  `tests/test_context_economy.py::StablePrefix`.
- **Count JSON as JSON and prose as prose.** `CHARS_PER_TOKEN_JSON` (3.5) for
  tool schemas, `CHARS_PER_TOKEN_PROSE` (4) for messages and the system prompt.
  These decide when compaction fires. Tool schemas were counted at `// 6` and
  messages at `// 3`, so the two errors pulled in opposite directions and the
  total looked plausible while neither half was.
- **The tool block is the largest single line item in a turn, not the prompt.**
  Measured across 64 real MCP tools: 503 chars (~125 tokens) each, so a 128-tool
  ceiling costs ~16,100 tokens *per turn* — four times the whole system prompt.
  `compact_tool_schemas` clips prose and drops documentation-only keys on the
  way out of `build_state`; it is pure, and must stay pure, because `ALL_TOOLS`
  is re-selected every turn and clipping in place would compound.

## Architecture essentials

- `agent.py` — core REPL, agent loop, built-in tool definitions, permission system, slash command handler
- `agent_cli.py` — TUI (arrow-key menus for provider setup, MCP management, session browser, skill listing)
- Built-in tools are hardcoded in `agent.py`. Risk is resolved per call by `risk_for(name, params)`, not by table lookup alone: `run_command` is classified from the command itself (read-only → `low`, anything with a shell separator or a mutating verb → `high`). `RISK_LEVELS` is the fallback table for every other tool.
- MCP tools are merged in at startup. Two layers of name resolution: MCP-vs-built-in collisions get an `mcp_` prefix (`resolve_mcp_tool_conflicts`), and server-vs-server collisions get `mcp_<server>_<tool>` for the second claimant (`MCPManager.discover_and_connect`). Tool names in the payload are unique by construction.
- Which tools are *sent* is decided per turn by `select_tools()`, by relevance to the user's message — not by list order. `ALL_TOOLS` is everything discovered; the budget comes from the provider's probed `max_tools`. Tools that do not fit are named to the model via `withheld_tools_notice()`, so a gap is recoverable rather than silent.
- MCP resources and prompts are reachable too: the `read_mcp_resource` tool (no arguments = list), plus `/mcp-resources` and `/mcp-prompt`.
- `~/.tomas/` is readable by path tools but not writable — write through `save_memory` / `self_notes` / `session_manager`, which own those file formats (`_safe(path, write=...)`).
- `run_command` always returns `[exit N — ok|FAILED]` first, decodes as UTF-8, and round-trips multi-line or nested-quote `python -c` payloads through a temp dir outside the project.
- Tool permission modes: `auto` (auto-approve low-risk), `default` (ask all), `strict` (ask all + clear overrides), `yolo` (approve everything) — set via `/mode` or F5–F8
- `CLAUDE.md` (this file) is injected into every system prompt built by `agent.py`; edit it to change persistent agent behavior for this project. Full load order is in `AGENTS.md`.

## Key modules

| File | Purpose |
|---|---|
| `session_manager.py` | Auto-saves sessions to `~/.tomas/sessions/` on exit; browse/continue/delete via TUI or `/session`. Records `complete`, `turn_metrics`, and `tool_log`; a transcript with a user turn that produced no reply is saved with `complete: false` and an `incomplete_reason` |
| `instructions_manager.py` | Loads global (`~/.tomas/instructions/`) and project-level (`AGENT.md`/`agent.md`) instructions into the system prompt |
| `skills_manager.py` | Discovers skills for `/skills` and `/skill <name>`. One format everywhere (`name`/`description`/`triggers`/`source`/`version`); malformed frontmatter is reported, never fatal; bodies load on demand; `improve_skill()` bumps the version and keeps provenance |
| `mcp_manager.py` | MCP server management (shared config with Claude Code at `~/.claude.json`); tools, resources and prompts |
| `provider_manager.py` | UI-free provider config, activation, and **probed** `Capabilities` (streaming, tool use, system prompt, context window, tool ceiling). Nothing infers behaviour from substrings in a URL or model name at runtime |
| `openai_adapter.py` | In-process Anthropic↔OpenAI translation with real incremental SSE streaming. Used for openai/openrouter/zen/ollama/custom endpoints; no daemon |
| `learning/` | The learning system: reflection over transcripts writes facts with evidence, retrieved per turn (`/self-improve facts`, `/self-improve reflect`) |
| `self_improve.py` / `self_notes.py` | Interaction log (`interactions.jsonl`, consumed by `learning/`), session-purpose analysis, persistent notes. The keyword-counting pattern/skill/tip generator was deleted in Phase 6 — do not reintroduce it |
| `zen_proxy.py` | Standalone HTTP proxy (Anthropic ↔ OpenAI). **No longer auto-started** — `openai_adapter.py` does the same translation in-process. Opt in with `TOMAS_ZEN_PROXY=1` when pointing *other* tools at Zen. Its translation functions are still the ones the adapter uses |
| `pdf_report_skill.py` | `/pdf-report` reads `latest_ai_news_report.txt`, writes `latest_ai_news_report.pdf` via fpdf2 |

## Quirks worth knowing

- Windows-only REPL: keyboard input uses `msvcrt` (arrow keys, F5–F8, Tab). No cross-platform TUI fallback.
- Web search uses Playwright (headless Chrome) by default, falls back to `duckduckgo_search`/`ddgs` if unavailable.
- `packages/backend/` (referenced in `AGENTS.md`) only has `.pyc` cache artifacts left — not wired into the TOMAS entrypoint, ignore it.
