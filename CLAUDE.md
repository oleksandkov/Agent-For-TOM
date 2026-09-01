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
- **A tool call the model wrote as text is still a tool call.** Small local
  models advertise `tools` and then answer in prose. Measured on
  qwen2.5-coder:3b (Ollama 0.30.6), tools attached, every reply: `tool_calls:
  null` and `` ```json\n{"name": "read_file", ...} `` in `content`. Its own
  template tells it to emit `<tool_call>` and not to use backticks; it uses
  backticks. Ollama's shim lifts only the exact `<tool_call>` form, so the
  turn reached `run_turn` as `end_turn` with no tool_use block and the JSON
  was printed at the user — the 3B was deciding correctly and the decision was
  being discarded. `core/toolcall_text.recover` parses it back and `run_turn`
  builds a response from it, on **both** paths, so permissions, loop detection
  and the budget all still apply. Two guards are not optional: the name must be
  a tool offered *this turn* (the same model answered "what your name?" with
  `{"name": "TOMAS", "arguments": {}}`), and the object may carry no keys
  beyond a call, so a fenced JSON *example* is not executed. Recovery runs
  after the truncation branch — half a call is not a call. Tests:
  `tests/test_toolcall_text.py`, whose fixtures are verbatim live replies.
- **The Zen catalogue is fetched, and "free" is a price, not a suffix.**
  `zen_proxy.ZEN_MODELS` was hand-written and dated; checked against upstream
  it offered three withdrawn models and hid four served ones, and the picker
  filed all sixty under "Zen free-tier models" while **eight** were free — the
  first-run path printed "using the OpenCode Zen free tier" and then selected
  `claude-fable-5`, which bills. `zen_catalog.py` takes availability from
  `opencode.ai/zen/v1/models` (5 KB, no auth) and description from
  `models.dev/api.json` (the catalogue OpenCode publishes; 89 opencode entries,
  28 of them no longer served — so it may never decide availability). Free is
  `cost.input == 0 and cost.output == 0`: `big-pickle` is free without the
  suffix and `minimax-m2.1` bills while `minimax-m2.1-free` does not. Degrades
  fresh cache → network → stale cache → the static list, never raises, and
  `Catalog.freshness` says which the user is looking at. `ZEN_MODELS` is the
  offline fallback only — adding to it by hand will not change the picker.
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
- **A turn cut off mid-work is not a finished turn.** `run_turn` ended on any
  reply carrying no tool call, so a model that spent its output budget
  reasoning and truncated mid-phrase closed the turn with 21 of 40 tool calls
  and 800 of 1200 seconds unspent — saved `complete: true`, `failed_turns:
  []`, no deliverable. `_maybe_continue` decides, in the core, on **both**
  paths, once (`ended_mid_work`: `stop_reason == max_tokens`, or a last line
  ≥ `MIN_UNFINISHED_LINE` ending on a letter).
  `session_manager.audit_transcript` answers the same question about a stored
  transcript and duplicates the rule rather than importing it — it must audit
  files written by older builds — so `test_session_integrity` asserts they
  agree. The length floor is load-bearing: without it the user's required
  `My Lord` sign-off marked every finished session incomplete.
- **A check that cannot see the defect is not a check.** Four scripts in
  `skills/document-style-match` compared page size, fonts, spacing and row
  density, and all four passed a rebuild whose headings were flush left where
  the sample centres them — none looked at alignment.
  `verify_docx.verify_signatures` compares the *share* of each
  `(align, bold, size, indent)` combination against the sample's, separating a
  correct rebuild (eight signatures, all within a point) from that one (title
  style absent, centred headings 12% → 1.6%, left+bold 0% → 6.3%). A share
  alone makes every paragraph structural in a short document, so
  `PROMINENT_MIN_BLOCKS` floors it at two.
- **The gate has to be cheaper than going round it.** That skill was nine
  scripts and a numbered list; one session ran none of them, wrote its own
  generator, checked the result by extracting its *text*, and shipped a
  US-Letter document at 1.15 spacing. Skipping cost nothing; complying cost
  eight sequential tool calls. `run.py measure` / `run.py build` is now the
  whole procedure — one command, one `VERDICT: PASS|FAIL`, scaffolding
  deleted on a pass — and the individual scripts are for reading a failure.
- **A tool that returns less than it was asked for must say so.** `read_file`
  extracted a PDF's words unlabelled; a session read them, saw the right text
  in the right order, and rebuilt the document on the wrong paper size — page
  geometry is not in the text and nothing said it was missing. Extraction now
  carries a banner, and formats with no text at all (`_UNREADABLE_AS_TEXT`)
  are refused rather than decoded to replacement characters: a PNG read as
  UTF-8 is not an error, so nothing stopped that session doing it twice.
- **The cut has to be a constant.** `build_system_prompt` capped the stable
  half at `MAX_TOTAL_SYSTEM_PROMPT - len(tail)`, which varies per message: two
  prompts trimmed 8,600 chars apart, no shared prefix, whole conversation
  re-billed. `MAX_STABLE_PROMPT_CHARS` bounds it alone and the tail sits on
  top. Instruction files must fit under it — over the cap, the skills
  catalogue is what falls off the end.
- **Count JSON as JSON and prose as prose.** `CHARS_PER_TOKEN_JSON` (3.5) for
  tool schemas, `CHARS_PER_TOKEN_PROSE` (4) for messages and the system prompt.
  These decide when compaction fires. Tool schemas were counted at `// 6` and
  messages at `// 3`, so the two errors pulled in opposite directions and the
  total looked plausible while neither half was.
- **A budget is shares of a window, never a table of flat numbers.**
  `core/budget.py` owns what may occupy the context. The tool ceiling was 64
  for every Ollama model whether the window was 8,192 or 262,144; the output
  reserve was 8,192 whether that was 4% or 25% of it. Measured on
  qwen2.5-coder:3b at 32,768: tools 18,079 (61%), output 8,192 (28%), system
  prompt 3,353 — 29,625 of fixed cost before the user typed. A `Profile`
  stores *shares*, and `resolve()` turns them into numbers from the real
  window and the *measured* per-tool cost of the connected pool, so "what does
  a 128k model get?" needs no new row. `auto` picks a preset per window class
  (`AUTO_TIERS`); an explicit `tool_ceiling`/`output_reserve` is the user's
  number and survives a model switch, while a preset follows the model.
- **No preset may switch off the learning system.** TOMAS is a self-improving
  agent; a profile that quietly stops it learning has not economised, it has
  changed what the program is. `learned_facts` and `standing_rules` are in
  `ALWAYS_ON` and every preset — `economy` included — leaves them on, costing
  nothing until something has been learned. Users may still turn them off by
  hand; the TUI confirms first. Enforced by
  `test_budget.py::TestLearningSurvivesEveryPreset`.
- **Compaction must not fire when compacting cannot help.** The rule stopped
  at "is the request over the trigger", which is half a decision — a request
  can be over it for a reason compaction cannot touch. It only shrinks the
  transcript, never below `POST_COMPACTION_FLOOR`. With 29,625 of overhead
  against a 24,576 trigger it fired on the first message, with a
  five-character transcript, and on every message after: two full local
  inferences per turn to summarise what was never the problem.
  `CompactionPlan.can_help` is the second question, `reason` is `"overhead"`
  when it answers no, and `maybe_compact` says so once per session. The two
  toggles in the *stable* prompt half (instructions, skills) are carried in
  `_stable_fingerprint` — switching one moves no file, so the memoised prefix
  would otherwise outlive the setting that built it.
- **Ollama is asked, not experimented on.** `_probe_feature` establishes
  capabilities by sending a request and seeing what comes back. That fails
  locally: the model loads into VRAM at the expense of the triggering request —
  measured on 0.30.6, a streamed probe cost 15.7 s and an image probe 14.9 s
  *warm*, against an 8 s timeout — and a timeout returns `optimistic`, not a
  measurement. Streaming was recorded "yes" without a stream ever being seen,
  vision "no" for every vision model present. `ollama_model_facts` instead
  reads `capabilities` and `<arch>.context_length` from `/api/show` in one
  call. The window reported is the **served** one: `/api/ps` is ground truth
  for a loaded model, else `OLLAMA_CONTEXT_LENGTH` or `OLLAMA_DEFAULT_NUM_CTX`
  caps it, because the shim exposes no `num_ctx` and a 262,144-token model
  still loads at 32,768. Cloud-routed models escape that cap, identified by
  `remote_host` on `/api/tags` rather than a `:cloud` suffix — this module does
  not read behaviour out of names. `_PROBE_TIMEOUT_LOCAL` covers any *other*
  local endpoint, still probed the slow way.
- **The model picker must reach every provider type.** A type missing from
  `agent_cli.PROVIDER_TYPE_TO_DETECT` falls through to
  `_provider_model_entries("other")`, a static cloud list. `ollama` was
  missing, so choosing Ollama and opening "Choose model" offered
  `openai/gpt-4o` and hid all eight installed models. `test_agent_units.py`
  asserts every `PROVIDER_TYPES` entry is mapped and labelled. A model switch
  also re-measures via `refresh_for_model` — capabilities were inherited
  wholesale, so a 32k model's window followed you to a 262k one.
- **An empty tool slot is cheaper than a wrong tool.** `select_tools` filled
  the budget to the brim, so once the tools a message actually scored ran out,
  the rest of the payload was decided by name length and list order. Measured
  on the live 257-tool pool at 36 MCP slots: "what time is it in Tokyo" spent
  21 slots on the pdf server, "remember that I prefer Ukrainian" got 8
  chrome-devtools tools and zero memory ones, and "hello" got 36 tools none of
  which scored — ~5,300 tokens per turn of tools chosen by accident, all of
  which the model reads. Three rules fixed it, to a mean of 12.3 tools and
  ~2,100 tokens over the same requests: `relevance_floor` is a *share of the
  best score on the same message* (a description hit is worth 1.0 whatever
  else is on offer, so a fixed cut-off tuned for a strong match discards
  everything on a weak one); `SERVER_CORE_QUOTA` requires
  `tool_name_matches` — eight slots is too large a commitment to make on
  incidental description overlap; and sticky carry-over applies only when the
  message scores nothing at all, or it becomes a ratchet that refills the
  payload one topic at a time. Under-filling is safe *because*
  `withheld_tools_notice` names what is missing — a gap the model can see
  beats one hidden behind 36 irrelevant schemas. Tests:
  `tests/test_tool_selection.py`.
- **The tool block is the largest single line item in a turn, not the prompt.**
  Measured across 64 real MCP tools: 503 chars (~125 tokens) each, so a 128-tool
  ceiling costs ~16,100 tokens *per turn* — four times the whole system prompt.
  `compact_tool_schemas` clips prose and drops documentation-only keys on the
  way out of `build_state`; it is pure, and must stay pure, because `ALL_TOOLS`
  is re-selected every turn and clipping in place would compound.
- **Esc had one cancellation point; MCP tool calls had none.**
  `handle_run_command` polls `_CURRENT_INTERRUPT` inside its own wait loop and
  kills its subprocess within a fraction of a second of Esc; every other tool
  only checked `state.interrupted()` *between* tool calls, so a slow MCP call
  (a browser action, a big fetch, OCR) held the turn hostage until it finished
  on its own — no keypress reached it. `_call_mcp_tool_interruptibly` runs the
  call on a background thread and polls the same `_CURRENT_INTERRUPT` the
  shell handler already uses, so the *turn* stops waiting the moment Esc is
  pressed. The call is not killed, only abandoned: an MCP server is one
  long-lived process for the whole session, not a per-call subprocess like
  `run_command`'s, so tearing it down would break every later call to it and,
  for `playwright` specifically, drop whatever page state the user had. The
  cost is narrow — `mcp_manager.py` holds a per-server lock for the call's
  duration, so a server that truly never answers stays locked for the rest of
  the session — which is still strictly better than today's alternative: the
  whole agent hanging with no way to interrupt at all. Tests:
  `tests/test_mcp_collision.py::TestMcpCallInterrupt`.
- **Spending and behaviour are two settings files, not one.** `core/budget.py`
  answers "how much of the window may this occupy" in shares that follow the
  model; `core/features.py` answers "is this on at all" as switches that
  survive a model change. Both are pure, and the TUI renders `FEATURES` rather
  than keeping its own list, so a switch cannot be added to the file and
  forgotten in the menu.
- **A deliberate limit must not be fought by the recovery for an accidental
  one.** `_can_escalate` retries any `max_tokens` stop at 4x, so the
  every-3rd-reply cap would be undone on the turn it applied.
  `AgentState.reply_capped` is its own flag because `max_tokens` cannot say
  *why* it is small; `_can_escalate` refuses on it.
- **An event nothing handles is a feature that silently does not exist.**
  `run_turn` yields `ThinkingStarted` before every model call and
  `TerminalAdapter.render` had no branch for it, so the watcher `drive()`
  starts was stopped by the first event and never restarted: every model call
  ran with a dead screen. On a reasoning model that is ~44 s of nothing
  (measured, `big-pickle`) then the reply at once — reported as "streaming is
  broken" when streaming was fine. `ReasoningProgress` carries the *size* of
  hidden reasoning, never its text, and relabels the spinner in place because
  it fires once per chunk. The Esc watcher lives in that loop, so
  `Thinking(silent=True)` turns the display off without turning off the only
  way to interrupt.
- **A threshold nobody can reach is not a setting.** `MIN_FIT_PERCENT` was 40,
  so every lower compaction choice was silently rewritten to 40% and a menu
  offering 4% would be a lie. The floor predated `CompactionPlan.can_help`,
  which answers the same worry better, so it drops to 1. The low end makes
  compaction observable: at 75% of a 200k window nothing happens until
  150,000 tokens.
- **Recording every payload is the largest thing the program would hold.** A
  session re-sends its whole conversation each turn, so `core/debug_log.py`
  records nothing until `features.debug_view` switches it on, and is bounded
  in entries and per-value size. It stores a `json.dumps` snapshot, not a
  reference: `messages` is mutated in place after the call. `_snapshot`
  catches `Exception` — `default=str` calls `str()` on unknown objects, which
  can raise anything. A live mirror file is what the Ctrl+Alt+X window tails:
  the REPL owns its console, so a live view cannot share it.
- **A wait that starts after "done" reads as a hang, not as more work.**
  `reflect_on_session_end` calls the model over the whole transcript, so it
  gets slower exactly as a session gets longer — and it ran, silently, in the
  `finally` block *after* `main()` already printed `Session saved: <id>`.
  Nothing distinguished a long reflection call from the process being stuck,
  because the one line the user had just seen already said the work was
  finished. Wrapped in the same `Thinking` spinner (`adapters.terminal`)
  `run_turn` uses mid-turn, so a long exit shows *why* it is still running
  instead of leaving the terminal looking frozen right after "saved".

- **The browser the user already has open is a different tool from one we
  launch.** `fetch_url_with_browser` starts a headless, logged-out Chrome per
  call; `core/browser.py` attaches over CDP so `tab_*` acts in the user's real
  tabs. Four constraints, each of which cost a live run — the full reasoning is
  in that module's docstring: the connection cannot live under `asyncio.run`
  (one loop on a daemon thread, which also gives Esc a cancellation point);
  the endpoint must say `localhost`, because Chrome answers `127.0.0.1` on the
  DevTools port with a bare 404 while LISTENING; the launcher must pass
  `--user-data-dir`, because Chrome 136+ silently ignores
  `--remote-debugging-port` on the default profile; and the tools are `tab_*`,
  because `browser_navigate`/`browser_snapshot` belong to the playwright MCP
  server and a built-in taking those renames the user's own tools.
- **Two lists that must align come from one traversal, never two queries that
  ought to agree.** `tab_snapshot` paired `page.query_selector_all` with
  `document.querySelectorAll`; Playwright's engine pierces open shadow roots
  and the browser's does not, so on gemini.google.com they were 48 against 42
  on every call. Had they matched, the outline would have silently omitted the
  controls a component app keeps there. `_WALK_JS` returns the nodes once,
  `_DESCRIBE_JS` describes that array. The same trap is in
  `OFFICE_LIVE_PLAN.md` §5, where the user may type between two passes over
  `doc.Paragraphs`. Corollary: **never advise retrying a deterministic
  failure** — the old message said "call tab_snapshot again" and manufactured
  the loop the loop guard had to stop, so `mismatch_message` is a function a
  test holds to its words.

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
| `skills/document-style-match/` | Reproduces a sample document's layout with new content. `run.py` is the entry point — `measure` then `build`, one verdict; the other scripts are its steps and are read when one fails |
| `core/budget.py` | Context budget policy — presets as shares of the window, section toggles, per-tool/server enable. Pure: computes and persists, never draws. `/budget` and the TUI page both render `agent.render_budget`, so they cannot disagree |
| `core/office.py` | Attaches over COM to the Word the user already has running and edits their open document live — the `doc_*` built-ins. Owns the COM thread, the busy-retry and the outline fingerprint. Not a file tool: `python-docx` and the `word-docs` MCP server write the file, which an open document ignores and then overwrites |
| `core/browser.py` | Attaches over CDP to the browser the user already has running and drives their open tab — the `tab_*` built-ins. Owns the session's one event loop, the CDP connection and the snapshot ref map |
| `core/features.py` | Feature switches (`~/.tomas/features.json`). Pure like `budget.py`; `/settings` and the TUI both render `FEATURES` |
| `core/debug_log.py` | Bounded recorder for raw payloads, off unless `features.debug_view` is on. Shown by `/debug`, Ctrl+Alt+X |
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
