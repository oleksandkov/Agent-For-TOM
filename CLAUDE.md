# Project guidelines for the demo agent

This is a demo project for the "Build Your Own Claude Code" agent.

Conventions:

- Python 3.10+
- Keep code simple and readable; prefer functions over classes for small scripts.
- Always read a file before editing it.
- Prefer `edit_file` over `write_file` for existing files.
- Run `python agent.py` to start the agent.
- Uses `.venv\Scripts\python.exe` as the Python interpreter.

## New features (implemented)

- **Token usage tracking** — `_session_tokens` and `_last_turn_usage` globals in agent.py track API token consumption; displayed after each response and in `/status`.
- **Skills as slash commands** — `/skills` lists installed skills; `/skill <name>` loads skill content and triggers agent processing. Uses `skills_manager.discover_skills()` and `cmd_skill_run()`.
- **PDF report skill** — `/pdf-report` slash command calls `pdf_report_skill.generate_ai_news_pdf()` which reads `latest_ai_news_report.txt` and creates `latest_ai_news_report.pdf` using fpdf2.
- **Session system** (`session_manager.py`) — Sessions are auto-saved on exit to `~/.tomas/sessions/`. Browse, continue, and delete sessions from the TUI menu or via `/session` slash commands (`/session list`, `/session save`, `/session continue <id>`, `/session delete <id>`, `/session latest`). Includes custom `SessionJSONEncoder` to handle Anthropic SDK pydantic types (TextBlock, ToolUseBlock, etc.) in message history.
- **Session continuation from UI** (`agent_cli.py` + `agent.py`) — Sessions can be continued directly from the TUI menu. `_launch_agent(session_id)` sets `agent_mod.CONTINUE_SESSION_ID` before calling `agent_mod.main()`, which loads existing messages and resumes the conversation. The session detail view offers "Continue" and "Delete" actions.
- **Redesigned session manager UI** (`agent_cli.py`) — `page_sessions()` rewritten with numbered session entries, clear action separation (Continue latest, Refresh, Delete all), session detail view showing metadata + recent messages, and intuitive navigation.
- **Zen proxy logging suppressed** (`zen_proxy.py`) — `log_message()` made a no-op to eliminate `[ZEN PROXY] GET /health HTTP/1.1` noise in output.
- **Instructions system** (`instructions_manager.py`) — Two-tier instruction loading: (1) Global instructions from `~/.tomas/instructions/` apply to every session; (2) Project-level instructions from `AGENT.md`/`agent.md` in the project root or `~/.tomas/instructions/project/<name>.md`. Both are injected into the system prompt.
- **Install.ps1** — Creates `instructions/` and `sessions/` directories during installation. Deploys default instruction files. Updated step numbering to [2/9]–[9/9].
