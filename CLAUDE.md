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