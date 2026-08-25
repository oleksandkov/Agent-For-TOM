# Agent-For-TOM (TOMAS)

Self-hosted AI coding agent — Python 3.10+, Windows-only REPL (`msvcrt`), agent loop + MCP + skills + multi-provider. See `CLAUDE.md` for daily conventions; this file is the structural reference.

## Entrypoints & Commands

- **TUI**: `python agent_cli.py` (dev, uses `.venv\Scripts\python.exe`) or `TOMAS` / `TOMAS.ps1` / `TOMAS.bat` after install
- **Direct REPL**: `python agent.py` or `TOMAS --run`
- **Deps**: `pip install -r requirements.txt` — `playwright` is listed but its browser (~170 MB) is not installed; `TOMAS browser` fetches it, otherwise `search_web` falls back to `ddgs`/`duckduckgo_search`
- **Tests**:
  - `python -m unittest discover -s tests -p "test_*.py"` — unit suite (~461 tests, `tests/test_agent_units.py` + `tests/test_*.py`)
  - `python test_agent.py` — integration runner (non-interactive)
  - Single: `python -m unittest tests.test_agent_units.<TestClass>.<test_method>`
  - Simulate harness: `python -m tests.simulate checks --offline` | `cyrillic` | `sessions [--turns N]`
- **MCP/Skills**: `TOMAS mcp list|add|remove|disable|enable|env` · `TOMAS skill list` · `TOMAS setup` (8 default MCPs, none need keys) · `TOMAS update`/`uninstall`

## Architecture — What Actually Matters

- **Built-in tools** hardcoded in `agent.py:TOOLS`; risk per-call via `risk_for(name, params)` — `run_command` classified from the command itself (read-only → `low`, mutating/shell separators → `high`), else `RISK_LEVELS` table. Do not quote a count; use `len(TOOLS)`.
- **MCP merge**: built-in collision → `mcp_` prefix (`resolve_mcp_tool_conflicts`); server-vs-server collision → `mcp_<server>_<tool>` for second claimant (`MCPManager.discover_and_connect`). Tool names in payload are unique by construction.
- **Tool selection per turn**: `select_tools()` picks by `tool_relevance()` score against the current message, not list order. Budget = provider's probed `max_tools`. Tools that don't fit are named via `withheld_tools_notice()` — gap is recoverable. Empty slot left empty beats filling with irrelevant schemas (~125 tokens/tool, ~16k at 128 tools). Quota: `SERVER_CORE_QUOTA=8` + `relevance_floor` + `STICKY_CARRY_OVER=8`.
- **Permissions**: `AGENT_AUTO_APPROVE=1` auto-approves `low`. Modes in `agent.py:ALL_MODES` = `auto`/`default`/`strict`/`yolo`/`bypass` (F5–F9, Tab cycles). `yolo` answers "may tool run?"; `bypass` also sets `AgentState.auto_continue` so budget checkpoint `needs_continuation_approval()` doesn't halt at 40 calls (max `max_auto_continuations=9` → ~400 calls). `a`/`always` approves *that exact call* only.
- **Blocked**: `run_command` rejects `rm -rf /`, `mkfs`, `> /dev/sd`, `dd if=/dev/zero`, fork bombs.
- **Writable location**: `_scratch/` inside `PROJECT_DIR` — sandbox only allows writes under project root; `~/.tomas/tmp` or `%TEMP%` will be denied. `SCRATCH_DIR` is the constant.
- **Blocked read/write**: `~/.tomas/` is readable but not writable via file tools; write via `save_memory`/`self_notes`/`session_manager` (`_safe(path, write=...)`).

## System Prompt — Two Halves (Prefix Cache)

Built by `agent.build_system_prompt`; order is load-bearing for provider prefix caching (exact byte prefix):

**Stable (memoised on `(path, mtime, size)`, identical between turns):**
1. `BASE_PROMPT` 2. Environment 3. Instructions (budgeted via `core.budget.instructions_budget` — share of window, not flat chars; `MIN 8k`/`MAX 40k`) in order: `~/.tomas/instructions/*.md` (alpha) → `AGENTS.md`+`agent.md`+`CLAUDE.md` (all three, every file loaded) → `~/.tomas/instructions/project/<name>.md` — overflow drops whole files from bottom, user is told. 4. Legacy `AGENT_INSTRUCTIONS.md`/`BEHAVIOR.md` 5. Skills catalogue (names only)

**Volatile (per message):** 6. Standing rules (`learning/`) 7. Retrieved facts (`learning.retrieval.recall` — 5 most relevant, not dump) 8. Triggered skill body

Call `invalidate_prompt_cache()` if you change instructions in-process. Stable cap is `MAX_STABLE_PROMPT_CHARS`; tail sits on top — old `MAX_TOTAL_SYSTEM_PROMPT - len(tail)` varied per message and broke cache each turn.

`AGENTS.md` + `CLAUDE.md` both injected every turn — errors there are errors on every turn.

## Config — Survives `TOMAS update`

`TOMAS update` replaces `src/` wholesale. Durable state lives under `~/.tomas/`:

| Path | Role |
|---|---|
| `~/.tomas/.env` | API keys, base URL, model (`python-dotenv`); written via `provider_manager.set_env_key` + `agent_cli.update_dotenv`. Migrated from `PROJECT_DIR/.env` / `providers.json` on first run (`_migrate_*`) |
| `~/.tomas/providers.json` | Multi-provider configs + probed `Capabilities` |
| `~/.tomas/context_budget.json` | `core.budget.Settings` — preset/tool ceiling/output reserve/section toggles/compact threshold |
| `~/.tomas/sessions/` | Auto-saved transcripts, max 50 (oldest deleted), `SessionJSONEncoder` for Anthropic pydantic types |
| `~/.claude.json` | MCP servers (shared with Claude Code) |

## Quirks That Have Bitten Past Sessions

- **Windows-only**: REPL/TUI uses `msvcrt`; no cross-platform fallback. `agent_cli.py` forces UTF-8 on stdout + enables VT100 — without it box-drawing glyphs crash on cp1251/cp437.
- **`install.ps1` must stay ASCII**: BOM-less `.ps1` decoded in ANSI codepage; Cyrillic UTF-8 bytes become a quote and break parsing. Localised text → `instructions_manager.DEFAULT_AGENT_INSTRUCTIONS`.
- **Web search**: Playwright headless Chromium first, `ddgs`/`duckduckgo_search` fallback. Browser not installed by default.
- **Zen**: `openai_adapter.py` does Anthropic↔OpenAI translation in-process with real SSE streaming. `zen_proxy.py` daemon no longer auto-starts; opt in with `TOMAS_ZEN_PROXY=1` only to point *other* tools at Zen. Model list fetched from `opencode.ai/zen/v1/models` + `models.dev` via `zen_catalog.py`; "free" = `cost.input==0 and cost.output==0`, never `-free` suffix. `ZEN_MODELS` is offline fallback only.
- **Context window**: resolved as override → probed (`/v1/models` + `Capabilities`) → catalog → `MODEL_CONTEXT_MAP` → 200k default. Catalogue is cache-only (`allow_network=False`) in hot path to avoid 8s stall.
- **Budget**: `core.budget` stores *shares*; `resolve()` derives numbers from real window + measured per-tool cost (`CHARS_PER_TOKEN_JSON=3.5`, `CHARS_PER_TOKEN_PROSE=4`). `auto` picks preset per `AUTO_TIERS`; manual `tool_ceiling`/`output_reserve` survive model switch. `ALWAYS_ON={learned_facts,standing_rules}` — no preset may disable learning (`test_budget.py` enforces).
- **Compaction**: two-question gate — `compact_plan` + `CompactionPlan.can_help` (transcript must be shrinkable past `POST_COMPACTION_FLOOR`). Overhead vs transcript distinction prevents firing on empty history when overhead alone exceeds trigger.
- **Provider picker**: `agent_cli.PROVIDER_TYPE_TO_DETECT` must map every `PROVIDER_TYPES` entry; fallback reads `providers.json:type` when `ANTHROPIC_BASE_URL` is empty. Model switch calls `refresh_for_model()` — capabilities not inherited.
- **Arrow menu**: `arrow_menu()` uses viewport row counting + `ERASE_DOWN` with truncated lines — fixes duplicate-picker artifact when items soft-wrap or span multiple rows. `show_info_page` is paged/scrollable and consumes whole keys (arrow = 2 bytes via `getwch`).
- **Slash commands**: `agent.SLASH_COMMANDS` is canonical; `/help` prints it. Don't copy list here — `tests/test_command_surface.py` asserts table↔dispatcher parity. Use `/` + Tab to complete.

## Packages & CI

- `packages/backend/` (`src/backend/tom/`) — separate `uv` project with `ruff` + `mypy` + `pytest --cov`. Only `.pyc` artifacts visible here; not wired into `agent.py` entrypoint. CI (`backend-tests.yml`) runs only on `packages/backend/**` changes. Main repo has no lint/typecheck CI — verify via unit tests.

## Instruction File Priority

Loaded via `instructions_manager.instruction_parts` (highest first): `~/.tomas/instructions/*.md` → `AGENTS.md`/`agent.md`/`CLAUDE.md` in repo root → `~/.tomas/instructions/project/<name>.md`. All reach the prompt; excess whole-files dropped and reported — never mid-document cut.
