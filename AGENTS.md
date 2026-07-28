# Agent-For-TOM (TOMAS)

A self-hosted AI coding agent (Python 3.10+). Agent loop, MCP integration, skill system, session management, multi-provider support.

## Startup

- **CLI entrypoint**: `agent_cli.py` — launches the TUI (arrow-key menus for provider setup, MCP management, session browser, skill listing).
- **Agent core**: `agent.py` — contains the REPL, agent loop, built-in tool definitions, permission system, and slash command handler.
- **Dev run**: `python agent_cli.py` (uses `.venv\Scripts\python.exe`).
- **After install**: `TOMAS` (Windows: `TOMAS.ps1` / `TOMAS.bat`).
- **Direct agent**: `TOMAS --run` or `python agent.py`.

## Python dependencies (3 total)

Only `anthropic>=0.40.0`, `python-dotenv>=1.0.0`, `fpdf2>=2.7.0` (see `requirements.txt`). Optional: `playwright`, `duckduckgo_search`.

## Key architecture notes

- **Built-in tools** are hardcoded in `agent.py` (~10 tools: read, write, edit, list, run, search, save_memory, fetch_url, fetch with browser, search web). Each has a risk tier (`low`/`medium`/`high`) in `RISK_LEVELS`.
- **MCP tools** are merged with built-in tools at startup. If an MCP tool name conflicts with a built-in, it gets an `mcp_` prefix. API limit: 128 tools total — excess MCP tools are silently dropped.
- **Tool permissions**: `AGENT_AUTO_APPROVE=1` (default) auto-approves low-risk tools. Modes: `auto` (low auto), `default` (ask all), `strict` (ask all + clear overrides), `yolo` (approve everything).
- **Permission override**: typing `always` at a permission prompt permanently downgrades that tool to `low` risk.
- **Blocked commands** in `run_command`: `rm -rf /`, `mkfs`, `> /dev/sd`, `dd if=/dev/zero`, fork bombs.

## System prompt loading order (each turn)

1. Built-in `BASE_PROMPT`
2. `AGENT_INSTRUCTIONS.md` or `BEHAVIOR.md` (project root, first match)
3. `CLAUDE.md` or `.claude/CLAUDE.md` (project root, first match)
4. Global instructions from `~/.tomas/instructions/*.md` (alphabetical)
5. Project instructions from `AGENT.md` / `agent.md` (project root) or `~/.tomas/instructions/project/<name>.md`
6. Memory index from `~/.tomas/memory/MEMORY.md`
7. Self-improvement context (purpose, stage, tips from `~/.tomas/self-improve/`)

**Takeaway**: `CLAUDE.md` is injected into every prompt. Edit it to change persistent agent behavior for this project.

## Configuration files

| File | Purpose |
|---|---|
| `.env` | API keys, base URL, model, auto-approve flag (loaded by python-dotenv) |
| `providers.json` | Saved multi-provider configurations (OpenRouter, Anthropic, Zen, etc.) |
| `~/.claude.json` | MCP server definitions (shared with Claude Code) |

## Important quirks

- **Windows-only REPL** — keyboard input uses `msvcrt` (arrow keys, F5-F8, Shift+Space for mode cycling, Tab completion for slash commands). No cross-platform fallback for the TUI.
- **UTF-8 forced** on stdout in `agent_cli.py` — handles emoji/Unicode in skill names.
- **Web search** uses `duckduckgo_search` (free, no API key). Falls back to error if not installed.
- **Zen proxy** (`zen_proxy.py`) auto-starts a local HTTP proxy when `ANTHROPIC_BASE_URL` points to `127.0.0.1:6446`. Converts Anthropic ↔ OpenAI format. Provides free models.
- **Session system**: auto-saves to `~/.tomas/sessions/` on exit. Max 50 sessions (oldest auto-deleted). Uses custom `SessionJSONEncoder` for Anthropic SDK pydantic types.
- **No test suite** — `packages/backend/tests/` has only `__pycache__`. No pytest config, no lint/typecheck setup.
- **Provider detection fallback**: `page_choose_model()` in `agent_cli.py` uses `_detect_provider()` (checks `ANTHROPIC_BASE_URL`) but falls back to `_detect_provider_from_config()` if it returns `"other"`. The config fallback reads the active provider's `type` from `providers.json` — this ensures Zen models appear even when `ANTHROPIC_BASE_URL` isn't set in `.env`.
- **Arrow menu redraw**: `arrow_menu()` now does a full redraw of all items + footer on every UP/DOWN key press (moves cursor up by `n + (1 if footer else 0)` lines and redraws everything). This eliminates "duplicate picker" artifacts from the old partial-redraw math.

## Slash commands (in-agent)

`/help`, `/clear`, `/status`, `/model`, `/mode [auto|default|strict|yolo]`, `/compact`, `/skills`, `/skill <name>`, `/pdf-report`, `/session {list|save|continue|delete|latest}`, `/self-improve` (or `/si`), `/note`, `/notes`, `/exit`.

Type `/` then Tab for auto-complete.

## packages/backend/

A separate, larger backend package (`src/backend/tom/`) with its own provider system, memory, chat orchestration, MCP bridge, and database migrations. Only `.pyc` cache artifacts remain (no `.py` sources visible). Not wired into the main TOMAS entrypoint.

## Files that affect agent behavior

- `CLAUDE.md` — project guidelines (injected into every system prompt)
- `AGENT.md` or `agent.md` — project-level instructions (loaded via `instructions_manager.py`)
- `~/.tomas/instructions/*.md` — global instructions applying to all sessions
