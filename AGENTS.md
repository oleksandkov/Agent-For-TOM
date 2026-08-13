# Agent-For-TOM (TOMAS)

A self-hosted AI coding agent (Python 3.10+). Agent loop, MCP integration, skill system, session management, multi-provider support.

## Startup

- **CLI entrypoint**: `agent_cli.py` — launches the TUI (arrow-key menus for provider setup, MCP management, session browser, skill listing).
- **Agent core**: `agent.py` — contains the REPL, agent loop, built-in tool definitions, permission system, and slash command handler.
- **Dev run**: `python agent_cli.py` (uses `.venv\Scripts\python.exe`).
- **After install**: `TOMAS` (Windows: `TOMAS.ps1` / `TOMAS.bat`).
- **Direct agent**: `TOMAS --run` or `python agent.py`.

## Python dependencies

See `requirements.txt` — it is the list, and it has grown past the three this
section used to name. `playwright` is in it but its *browser* is not installed
by default (~170 MB); `web_search` falls back to `ddgs`/`duckduckgo_search`
without it, and `TOMAS browser` fetches it on demand.

## Key architecture notes

- **Built-in tools** are hardcoded in `agent.py` — `len(TOOLS)` is the count, do not quote a number here. Risk is resolved per call by `risk_for(name, params)`: `run_command` is classified from the command itself, and `RISK_LEVELS` is the fallback table for everything else.
- **MCP tools** are merged with built-in tools at startup. Built-in collisions get an `mcp_` prefix; server-vs-server collisions get `mcp_<server>_<tool>` for the second claimant.
- **Which tools are sent** is decided per turn by `select_tools()`, by relevance to the message — not by list order, and not by truncating at a fixed ceiling. A tool that does not fit is *named* to the model by `withheld_tools_notice()`, so a gap is recoverable rather than silent. An empty slot is left empty rather than filled with an irrelevant tool.
- **Tool permissions**: `AGENT_AUTO_APPROVE=1` (default) auto-approves low-risk tools. Modes are `ALL_MODES` in `agent.py`: `auto`, `default`, `strict`, `yolo`, `bypass` (F5–F9). `yolo` answers "may this tool run?"; `bypass` also answers "may the turn keep going?".
- **Permission scope**: answering `a`/`always` approves *that exact call* for the session. It does not downgrade the tool's risk tier — an approval of `git status` must not become a blanket grant on every future `run_command`.
- **Blocked commands** in `run_command`: `rm -rf /`, `mkfs`, `> /dev/sd`, `dd if=/dev/zero`, fork bombs.

## System prompt loading order (each turn)

Built by `agent.build_system_prompt` in two halves, and the split is the point:
prefix caching matches on an exact byte prefix, so anything that varies with
the message must come after everything that does not.

**Stable half** (memoised, byte-identical between turns):

1. Built-in `BASE_PROMPT`
2. Environment section
3. Instructions, in priority order and budgeted as a share of the context
   window (`core.budget.instructions_budget`). Every file that exists is
   loaded — they are not alternatives:
   1. `~/.tomas/instructions/*.md` (alphabetical)
   2. `AGENTS.md`, `agent.md`, `CLAUDE.md` in the project root
   3. `~/.tomas/instructions/project/<project-name>.md`
   When they do not all fit, whole files are dropped from the bottom of that
   order and the user is told which — never a cut mid-document.
4. Legacy `AGENT_INSTRUCTIONS.md` / `BEHAVIOR.md`, if present
5. Skills catalogue (names only)

**Volatile tail** (rebuilt per message):

6. Standing rules (`learning/`)
7. Retrieved facts for this message (`learning.retrieval.recall`)
8. The body of a skill this message triggers

**Takeaway**: `AGENTS.md` and `CLAUDE.md` are both injected into every prompt.
Anything wrong in them is wrong in the agent's own understanding of itself, on
every turn — which is why the numbers above were replaced with the names of
the values rather than copies of them.

## Configuration files

| File | Purpose |
|---|---|
| `~/.tomas/.env` | API keys, base URL, model, auto-approve flag (loaded by python-dotenv) |
| `~/.tomas/providers.json` | Saved multi-provider configurations. Under `~/.tomas`, not the source tree: `TOMAS update` replaces the source wholesale, so config kept there was wiped on every update (`_migrate_providers_config` moves old copies) |
| `~/.tomas/context_budget.json` | Budget settings — preset, tool ceiling, output reserve, section toggles, auto-compaction threshold |
| `~/.claude.json` | MCP server definitions (shared with Claude Code) |

## Important quirks

- **Windows-only REPL** — keyboard input uses `msvcrt` (arrow keys, F5–F9 for the five modes, Tab for mode cycling and slash-command completion). No cross-platform fallback for the TUI.
- **UTF-8 forced** on stdout in `agent_cli.py` — handles emoji/Unicode in skill names.
- **`install.ps1` must stay ASCII.** Windows PowerShell reads a BOM-less `.ps1` in the machine's ANSI codepage, so on cp1251 the UTF-8 bytes of a Cyrillic letter decode to a character PowerShell treats as a quote — which opened an unterminated string and made the whole installer fail to parse before running a line. Localised text belongs in Python (`instructions_manager.DEFAULT_AGENT_INSTRUCTIONS`), which the installer calls.
- **Web search** prefers Playwright (headless Chromium) and falls back to `ddgs`/`duckduckgo_search`. The browser is *not* installed by default — `TOMAS browser` fetches it.
- **Zen proxy** (`zen_proxy.py`) is **no longer auto-started**: `openai_adapter.py` does the same Anthropic ↔ OpenAI translation in-process, with real incremental streaming and no daemon. Opt in with `TOMAS_ZEN_PROXY=1` only when pointing *other* tools at Zen.
- **Zen's model list is fetched, not hardcoded** — `zen_catalog.py` reads availability from `opencode.ai/zen/v1/models` and per-model metadata from `models.dev`. "Free" is read from price, never from a `-free` suffix.
- **Session system**: auto-saves to `~/.tomas/sessions/` on exit. Max 50 sessions (oldest auto-deleted). Uses custom `SessionJSONEncoder` for Anthropic SDK pydantic types.
- **Test suite** — Comprehensive unit test suite in `tests/test_agent_units.py` and integration runner in `test_agent.py`. Run: `python -m unittest discover -s tests -p "test_*.py"`.
- **Provider detection fallback**: `page_choose_model()` in `agent_cli.py` uses `_detect_provider()` (checks `ANTHROPIC_BASE_URL`) but falls back to `_detect_provider_from_config()` if it returns `"other"`. The config fallback reads the active provider's `type` from `providers.json` — this ensures Zen models appear even when `ANTHROPIC_BASE_URL` isn't set in `.env`.
- **Arrow menu redraw**: `arrow_menu()` now uses windowed viewport scrolling to eliminate duplicate picker artifacts when lists exceed terminal height.

## Slash commands (in-agent)

`agent.SLASH_COMMANDS` is the list, and `/help` prints it. It is not copied
here: the copy that used to be went stale by twelve commands, and a list of
capabilities that under-reports them is worse than no list, because it reads
as complete. `tests/test_command_surface.py` holds the table and the
dispatcher to each other.

Type `/` then Tab for auto-complete.

## packages/backend/

A separate, larger backend package (`src/backend/tom/`) with its own provider system, memory, chat orchestration, MCP bridge, and database migrations. Only `.pyc` cache artifacts remain (no `.py` sources visible). Not wired into the main TOMAS entrypoint.

## Files that affect agent behavior

In the order they are loaded, highest authority first
(`instructions_manager.instruction_parts`):

- `~/.tomas/instructions/*.md` — global, every session on this machine
- `AGENTS.md`, `agent.md`, `CLAUDE.md` in the project root — all three, not the first match
- `~/.tomas/instructions/project/<project-name>.md`

All of them reach every system prompt. When they exceed the window's share
(`core.budget.instructions_budget`) the lowest-priority whole files are
dropped and the user is told which — so a rule is either in force or visibly
absent, never half-present.
