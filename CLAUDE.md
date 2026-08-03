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
  - `python -m tests.simulate sessions [--turns N] [--session <name>]` — live goal-driven sessions
  - It resolves every entry point up front; a missing name is a hard FAIL (exit 2), never a WARN

## Conventions

- Python 3.10+
- Keep code simple and readable; prefer functions over classes for small scripts
- Always read a file before editing it
- Prefer `edit_file` over `write_file` for existing files

## Architecture essentials

- `agent.py` — core REPL, agent loop, built-in tool definitions, permission system, slash command handler
- `agent_cli.py` — TUI (arrow-key menus for provider setup, MCP management, session browser, skill listing)
- Built-in tools are hardcoded in `agent.py`. Risk is resolved per call by `risk_for(name, params)`, not by table lookup alone: `run_command` is classified from the command itself (read-only → `low`, anything with a shell separator or a mutating verb → `high`). `RISK_LEVELS` is the fallback table for every other tool.
- MCP tools are merged in at startup. Two layers of name resolution: MCP-vs-built-in collisions get an `mcp_` prefix (`resolve_mcp_tool_conflicts`), and server-vs-server collisions get `mcp_<server>_<tool>` for the second claimant (`MCPManager.discover_and_connect`). Tool names in the payload are unique by construction. 128-tool API limit (32 on free tiers); excess MCP tools are dropped.
- `~/.tomas/` is readable by path tools but not writable — write through `save_memory` / `self_notes` / `session_manager`, which own those file formats (`_safe(path, write=...)`).
- `run_command` always returns `[exit N — ok|FAILED]` first, decodes as UTF-8, and round-trips multi-line or nested-quote `python -c` payloads through a temp dir outside the project.
- Tool permission modes: `auto` (auto-approve low-risk), `default` (ask all), `strict` (ask all + clear overrides), `yolo` (approve everything) — set via `/mode` or F5–F8
- `CLAUDE.md` (this file) is injected into every system prompt built by `agent.py`; edit it to change persistent agent behavior for this project. Full load order is in `AGENTS.md`.

## Key modules

| File | Purpose |
|---|---|
| `session_manager.py` | Auto-saves sessions to `~/.tomas/sessions/` on exit; browse/continue/delete via TUI or `/session`. Records `complete`, `turn_metrics`, and `tool_log`; a transcript with a user turn that produced no reply is saved with `complete: false` and an `incomplete_reason` |
| `instructions_manager.py` | Loads global (`~/.tomas/instructions/`) and project-level (`AGENT.md`/`agent.md`) instructions into the system prompt |
| `skills_manager.py` | Discovers skills for `/skills` and `/skill <name>` |
| `mcp_manager.py` | MCP server management (shared config with Claude Code at `~/.claude.json`) |
| `learning/` | The learning system: reflection over transcripts writes facts with evidence, retrieved per turn (`/self-improve facts`, `/self-improve reflect`) |
| `self_improve.py` / `self_notes.py` | Interaction log (`interactions.jsonl`, consumed by `learning/`), session-purpose analysis, persistent notes. The keyword-counting pattern/skill/tip generator was deleted in Phase 6 — do not reintroduce it |
| `zen_proxy.py` | Local HTTP proxy (Anthropic ↔ OpenAI format) auto-started when `ANTHROPIC_BASE_URL` points to `127.0.0.1:6446`; logging is a no-op to suppress health-check noise |
| `pdf_report_skill.py` | `/pdf-report` reads `latest_ai_news_report.txt`, writes `latest_ai_news_report.pdf` via fpdf2 |

## Quirks worth knowing

- Windows-only REPL: keyboard input uses `msvcrt` (arrow keys, F5–F8, Tab). No cross-platform TUI fallback.
- Web search uses Playwright (headless Chrome) by default, falls back to `duckduckgo_search`/`ddgs` if unavailable.
- `packages/backend/` (referenced in `AGENTS.md`) only has `.pyc` cache artifacts left — not wired into the TOMAS entrypoint, ignore it.
