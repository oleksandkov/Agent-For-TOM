# Agent-For-TOM - OpenCode Agent Guidance

## Getting Started
- **Run the desktop GUI**: `python app/run_app.py` (recommended; no browser/server)
- **Run the web app**: `python app/run.py` (starts FastAPI on http://127.0.0.1:8000)
- **Run the interactive CLI**: `python app/run_cli.py` (no server needed; prompts you, outputs PDF + DOCX)
- **Install dependencies**: `pip install -r app/requirements.txt`
- **API docs** (web app only): Visit http://127.0.0.1:8000/docs

## Project Structure
- `app/run_app.py` - Desktop GUI launcher (CustomTkinter)
- `app/run.py` - Web entry point (starts Uvicorn server)
- `app/run_cli.py` - CLI launcher (single-user interactive program)
- `app/gui/lab_app.py` - CustomTkinter desktop app
- `app/gui/presets.py` - Document presets (each preset has its own field set + AI prompt)
- `app/cli/lab_generator.py` - The interactive CLI module
- `app/backend/main.py` - FastAPI application with endpoints
- `app/backend/docx_generator.py` & `pdf_generator.py` - Document generation logic (ДСТУ 3008:2015)
- `app/backend/models.py` - Pydantic models for request/response
- `app/backend/ai/hf_service.py` - HuggingFace Inference client (validate-and-retry JSON loop)
- `app/requirements.txt` - Python dependencies
- `app/output/` - Generated documents (PDF/DOCX)
- `app/user_data/` - Persistent data (teacher profiles)

## Desktop GUI (the recommended way)
Run `python app/run_app.py`. A native window opens with:

1. **Left column — Реквізити документа** (university, department, discipline, authors, city, year) + **Пресет** dropdown (currently: «Лабораторна робота» and «Методичні рекомендації»).
2. **Right column** — A dynamic form that re-renders whenever you switch presets. Each preset asks for different fields. «Лабораторна робота» asks for topic, hints, etc.; «Методичні рекомендації» asks for name, audience, list of topics, etc.
3. **Bottom — AI settings**: persona, HF provider, model id, API key. API key is optional; without it, the app uses a built-in template (mock).
4. **Generate** button — runs AI generation + PDF/DOCX rendering. On success, a Save dialog asks where to put the files.

Adding a new preset is purely declarative: append a `Preset(...)` to `PRESETS` in `app/gui/presets.py`. The form, the prompt, and the fallback content all come from that one definition.

## Interactive CLI (the keyboard-only way)
Run `python app/run_cli.py` and the program will:

1. Ask for the metadata that only the teacher knows (university, department, discipline, authors, city, year) — every field has a default, just press Enter to accept.
2. Ask for the academic persona (formal_academic, practical_oriented, etc.).
3. Ask for the lab work requirements (topic, level, required sections) — multi-line input.
4. Call HuggingFace (if `--api-key hf_...` is given) to fill in the academic content, or use a built-in template (mock mode).
5. Render both PDF and DOCX to `app/output/lab_guidelines_{year}_{discipline}.{pdf|docx}`.
6. Offer to open the output folder.

Useful CLI flags:
- `--yes` / `-y` - Use all defaults, no interactive prompts (good for scripting).
- `--no-ai` - Force the built-in template (no API key required).
- `--api-key hf_xxxx` - HuggingFace token to enable real AI generation.
- `--ai-provider cerebras|novita|together|hf-inference` - Inference provider.
- `--ai-model <id>` - Specific model, e.g. `meta-llama/Llama-3.1-8B-Instruct`.
- `--topic "..."` - Pre-fill the lab topic/requirements (skips the multi-line prompt).
- `--university`, `--department`, `--discipline`, `--city`, `--year`, `--authors` - Pre-fill metadata.

Example non-interactive run (mock content, custom topic):
```bash
python app/run_cli.py --yes --no-ai --topic "Програмна реалізація структур діалогу командного типу, меню і екранних форм"
```

Example with real AI:
```bash
python app/run_cli.py --api-key hf_xxxxxxxxxxxx --topic "Дослідження методів сортування в масивах"
```

## Key Web Endpoints
- `GET /api/profile` - Retrieve teacher profile
- `PUT /api/profile` - Update teacher profile
- `POST /api/generate` - Generate document(s) (mock, HF, or Gemini)
- `GET /api/download/{filename}` - Download a generated file
- `GET /api/models` - List chat models available on a HuggingFace inference provider

## Development Notes
- Document generation supports three modes: mock (default), HuggingFace, Gemini (legacy)
- Output files are named: `lab_guidelines_{year}_{discipline}.{pdf|docx}`
- CORS is configured to allow all origins (`*`)
- Profiles are stored as JSON in `app/user_data/profile.json`
- The import path in `run.py` is `backend.main:app` (note the colon syntax)
- The CLI and the GUI both reuse the same AI prompt + validation logic and the same PDF/DOCX generators as the web app — only the input mechanism differs.
- The PDF generator requires Times New Roman TTFs in `C:\Windows\Fonts` (Windows-only).
- **Title page layout**: ministry/university/department at the top, main title block just under, authors on the right, **city and year pinned to the BOTTOM of the page** (centred, ~2.5cm from the bottom edge). The DOCX uses empty paragraphs to push them down since python-docx has no absolute positioning.

## Important Constraints
- Requires Python 3.8+ (based on dependencies)
- HuggingFace mode requires internet access and a valid HF token
- Gemini mode is maintained for backward compatibility only
- Generated files are saved to disk but not automatically cleaned up
- The desktop GUI uses `customtkinter` (added to `app/requirements.txt`); if it's missing, `pip install customtkinter`
