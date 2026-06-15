# Agent-For-Labs

Desktop Python application that uses free HuggingFace LLM models to generate
standardized Ukrainian academic documents (lab reports, methodological guides,
etc.) filled into user-selected DOCX/PDF templates. Fully local — no logins,
no cloud accounts.

## What it does

The user picks a template (e.g. "Lab 1 — sorting algorithms"), fills a short
form (name, theme, goal, difficulty, length, image mode), optionally attaches
reference files, and clicks **Generate**. The app:

1. Converts attached files (PDF/DOCX/PPTX/images) into text.
2. Sends the text LLM (Llama-3.3-70B-Instruct by default) the template's
   Python scaffold + all instructions. The LLM fills the placeholders.
3. If images are enabled, the LLM also writes an `image_manifest.json`
   describing which diagrams/illustrations to draw.
4. The post-processor runs the filled Python to produce DOCX + PDF, then
   generates the requested images (matplotlib for diagrams, FLUX.1 for
   illustrations) and embeds them at the anchor markers.
5. The finished DOCX and PDF are stored locally; the session is saved to
   the database for later restore / duplicate / delete.

The whole pipeline is cached at 3 levels (LLM response, image PNG, final
document) so identical re-runs are instant.

## Tech stack

| Layer | Choice | Why |
|---|---|---|
| **UI** | PyQt6 + QML / Qt Quick | Native threading via QThread, QSyntaxHighlighter for code, QML Canvas for annotation overlays. Best fit for a Python-centric desktop app. |
| **Backend** | Python scripts (no web framework) | Orchestrator + 3-level cache manager + file converters. |
| **Database** | SQLite (single-file, local) | Single-user desktop. All PKs are UUID v4. JSON columns stored as TEXT. |
| **File storage** | Local filesystem inside the app folder | Attached files deduped by SHA-256, deduplicated library. |
| **Text LLM** | HuggingFace Inference API — `meta-llama/Llama-3.3-70B-Instruct` (default, overridable via env) | Writes Ukrainian academic text and fills Python placeholders. |
| **Image LLM** | HuggingFace Inference API — `black-forest-labs/FLUX.1-schnell` (default) | Generates illustration-style PNGs only. Diagrams use local matplotlib (no API cost). |
| **Document output** | `python-docx` (DOCX) + `reportlab` (PDF) | Two parallel functions, identical text — enforced by the validation step. |

Auth: **none**. The HF token is the only secret, read from `.env` at startup.

## Team

| Member | Role | Main areas |
|---|---|---|
| **Yaroslav Sych** | Frontend developer | PyQt6/QML screens, QThread integration, file dialogs, rich-text preview, annotation overlay. Currently producing the first UI prototype (see `Combined/preview/` for HTML mockups — **not yet wired into AGENTS.md**, will be linked here in a later revision). |
| **Koval Oleksander** | Backend developer | Pipeline orchestrator, file converters, SQLite schema, cache manager, HF API client, custom-template generator, document composer. |

When working in this repo, the AI agent on the team should ask which
side of the boundary it is touching before changing anything in
`Combined/apps_loogic.md`, `Combined/database.md`, or the pipeline
components — those files are the source of truth shared between both
sides.

## How the application works — short version

Full diagrams and step-by-step details live in **`Combined/apps_loogic.md`**.
The short version:

### 5 input elements the LLM receives per run

In priority order (later overrides earlier):

1. **Global instructions** — `Combined/instructions/global_instructions.md`. Static. Sent every run. Defines output format rules, ДСТУ 3008:2015 typography, image reference protocol.
2. **Template-specific instructions** — `Combined/instructions/labN_fill.md`. One file per template. Defines which placeholders to fill, in what order, with what constraints.
3. **User style file** — `Combined/instructions/user_style.md`. Per-user. Skipped if empty. Holds the user's preferred vocabulary, tone, phrasing.
4. **Attached files** — converted to text by `Combined/Scripts/{pdf,docx,pptx,image}2txt.py`. If a file > 4000 tokens after conversion, it gets summarized to 500 tokens.
5. **User parameters** from the form — `length`, `hardness`, `image_mode`, plus the per-template fields (name, theme, goal, gap values).

### 2-pass pipeline

- **Pass 1 — Text LLM.** Tries the 5-tier synthesis chain: (1) Google
  Gemini 2.5 Flash, (2) OpenRouter (free model), (3) Groq, (4) local
  Qwen 2.5 1.5B GGUF, (5) pass-through of the user's typed values.
  Within Tier 1, the order is set by `REMOTE_LLM_PROVIDER` in `.env`
  (default: `gemini,openrouter,groq`). The first provider that
  returns JSON matching the schema wins; missing API keys are
  skipped, not failed.
- **Pass 2 — Image & document stage.** Deterministic code: AST-validates the Python, runs it in a sandboxed subprocess to produce DOCX + PDF, then generates PNGs for every `[[ANCHOR:...]]` and embeds them at the matching marker. Failed image generation becomes a placeholder, never breaks the run.

### Image modes

| Mode | What the LLM does | What Pass 2 does |
|---|---|---|
| `none` (default) | No mention of figures, no `[[ANCHOR]]` markers. | Nothing extra — runs `python filled.py` and ships the document. |
| `references` | Inserts text like «(див. Рис. 1)» into the prose. No manifest. | Nothing extra. |
| `full` | Inserts `[[ANCHOR:figN:<rand>]]` markers AND emits an `image_manifest.json` block. | Generates PNGs (matplotlib for `diagram`, HuggingFace for `illustration`), embeds them, strips the markers. |

## Data model (high level)

Full schema: **`Combined/database.md`**. The 9 tables are:

- `templates` — built-in (lab1, lab2, …) and user-created. Stores script path, instructions path, placeholder schema, gap schema, and whether the template has been annotated.
- `instructions` — versioned. Three types: `global` (one active, `template_id=NULL`), `special` (per template), `user_created`. Saving a new version flips the old one to `is_active=0`.
- `user_style` — single-row-per-version, same versioning rule. `is_empty=1` → skipped at runtime.
- `sessions` — one row per generation run. Stores the frozen `input_snapshot` JSON, output paths, token usage, duration, status (`draft`/`processing`/`completed`/`failed`/`cancelled`), `error_stage`, hashes of the global instructions and style used.
- `session_files` — join table linking a session to the library files it used.
- `library_file` — every attached file ever seen. Deduplicated by `file_hash` (SHA-256). Holds the original and the converted text.
- `custom_template_annotations` — the user's selections (text-and-format / format-only / AI-replace / gap) when building a template from a PDF/DOCX.
- `llm_cache`, `image_cache`, `document_cache` — three write-through SQLite tables for the 3-level cache. Cache key = SHA-256 of the relevant inputs.

UI rules (cascade on session delete, restore from `input_snapshot`, etc.)
are in the "Правила для UI" section of `Combined/database.md`.

## User-visible features

| Feature | What it does | Where to look |
|---|---|---|
| **Sessions list** | Browse, restore, duplicate, delete past runs. Filter by status. | `Combined/database.md` § sessions |
| **New session form** | Pick a template, fill parameters, attach files, generate. | `Combined/frontend suggestion.md` § Screen 2 |
| **Pipeline progress** | Live step labels (Convert → Text → Validate → Images → Execute → Compose) with progress bar, log, and cancel. | `Combined/apps_loogic.md` § Pipeline |
| **Result screen** | Download DOCX + PDF, see token usage, word count vs target, image count, syntax-highlighted `filled.py`. | `Combined/frontend suggestion.md` § Screen 4 |
| **Custom template builder** | Upload PDF/DOCX → converted text appears in left panel → mark regions with 4 annotation types → save as new template. PDF format may degrade; user can manually fix. | `Combined/the configurate own template.md` |
| **Custom annotation types** | A: keep text+format • B: keep format, model writes text • C: AI replaces freely • D: named gap (user fills manually every run) | same file, § Step 2 |
| **Instructions editor + manager** | Markdown editor with auto-filled placeholder keys. Versions with diff. List view with type/attached-to filters. | `Combined/the configurate own template.md` § Instructions |
| **Library browser** | View all attached files, download or add to current session. | derived from `library_file` table |

## Conventions and rules

These are non-negotiable — the LLM and the post-processor both depend on them.

### Output format the LLM must follow

- Return **only valid Python code**, no Markdown fences, no commentary.
- First line: `import os`. Last line: a call to `create_docx(...)` and `create_pdf(...)` inside `if __name__ == "__main__":`.
- Every `[Вставте ...]` placeholder must be replaced — none left over.
- DOCX and PDF functions must contain **identical text** in every section (the manifest validator relies on this).
- Quotes inside placeholders are **double** `"`. Apostrophes in Ukrainian words (e.g. `однозв'язний`, `алгоритмів`) are fine — they do not conflict with `"`.
- **No** triple quotes `"""` inside placeholders.
- **No** `print(...)` inside the functions except the final "file created" message that already exists in the template.
- Do not change `firstLineIndent=35` (PDF) or `DocxCm(1.25)` (DOCX) — this is the 1.25 cm first-line indent required by ДСТУ 3008:2015.
- Do not rename styles, do not change `SimpleDocTemplate` or `DocxCm(...)` parameters.

### Lab2-specific (do not break when editing)

- Section order: **Title → Name → Meta → Загальні відомості → Контрольні запитання → Завдання → Варіанти → Література**. (Контрольні запитання are **before** Завдання — opposite of Lab1.)
- "Варіанти" header is **left-aligned bold**, not centered.
- Output filenames are hard-coded: `Lab_Template_Lab2_Style.docx` / `.pdf`. Do not rename.
- `create_docx(filename)` and `create_pdf(filename)` have **no default value** for `filename`. Do not add one.
- The `TA_LEFT` import from `reportlab.lib.enums` is required for the `style_body_bold` used by "Варіанти". Do not delete.
- `Spacer(1, 14)` after the Meta line in the PDF is intentional. Do not remove.

### Image reference protocol

- Only used when `image_mode=full`.
- Format: `[[ANCHOR:figN:<rand>]]` placed **inside** the text of the `Paragraph` / `add_paragraph` that the figure should follow.
- `<rand>` is 6 chars `[A-Za-z0-9]`, unique across the whole `filled.py`. Invent it yourself.
- The same `id` and `rand` must appear in **both** DOCX and PDF versions of the same section — otherwise the composer inserts the image into only one of the two.
- The manifest is the single JSON block after `<!--IMAGE_MANIFEST-->` at the end of the LLM's reply. `caption` is **Ukrainian**; `render.prompt` for `engine=huggingface` is **English** (FLUX/SDXL handle English far better).
- For technical content (UML, flowcharts, graphs, data structure visualizations) use `kind=diagram` and `engine=matplotlib` or `engine=graphviz` — never `illustration`. Reserve `illustration` for purely decorative art.

### ДСТУ standards baked into the system

- **ДСТУ 3008:2015** — typography (Times New Roman 14pt, 1.5 spacing, 1.25 cm first-line indent, margins left 3 cm / right 1.5 cm / top-bottom 2 cm, justified text, centered bold headings, centered figure captions "Рис. N — …"). These are hard-coded in the templates; do not change them.
- **ДСТУ 8302:2015** — bibliographic entries: `Прізвище, І. Б. Назва : тип. Місто : Вид, Рік. N с.`

### Coding style

- Python: PEP 8, type hints where they add clarity, no comments unless asked. Match the style of the existing files in `Combined/Scripts/`.
- Never add new top-level `print(...)` calls. The only allowed one is the final "file created" message inside the template's `if __name__ == "__main__":`.
- New code should be importable on its own (no import-time side effects).

## Repository layout (current)

```
AGENTS.md                       ← this file (team-context entry point)
Combined/                       ← authoritative shared specs (see below)
  apps_loogic.md                ← full pipeline + image reference protocol
  database.md                   ← SQLite schema, all 9 tables + UI rules
  frontend suggestion.md        ← 7 UI screens + PyQt6 vs alternatives
  the configurate own template.md ← custom template builder + 4 annotation types
  instructions/                 ← LLM instruction files
    global_instructions.md      ← sent every run
    lab1_fill.md, lab2_fill.md  ← sent per template
    user_style.md               ← per-user, skipped if empty
  Scripts/                      ← existing reference Python (file converters, response parser, cache manager, manifest validator)
  templates/                    ← lab1-template.py, lab2-template.py
  preview/                      ← HTML mockups (Yaroslav's UI prototype) — NOT linked into AGENTS.md yet
None ai/                        ← earlier high-level concept (kept for reference, Combined/ supersedes it)
```

**Rule of thumb:** `Combined/` is the source of truth. `None ai/` is kept
for reference only; if the two disagree, `Combined/` wins.

## Working agreements

- Before changing any shared spec under `Combined/`, confirm with the other team member whose side is affected. Backend owns `apps_loogic.md` and `database.md`; frontend owns `frontend suggestion.md` and the screen-by-screen behaviour described there.
- The custom-template builder behaviour (`the configurate own template.md`) is the most fluid part — expect additions as Yaroslav's prototype lands.
- Cache invalidation: when changing global instructions or template-specific instructions, the relevant `llm_cache` and `document_cache` rows become stale. The cache is hash-keyed, so this happens automatically — just be aware that local dev runs may need a cache wipe after spec changes.
- When a session fails, the `error_stage` column tells you which step broke. The matrix in `Combined/apps_loogic.md` ("Матриця помилок") maps every known failure to HARD (stop) or WARN (continue).
- All paths inside the app are **relative to the program root**, not absolute. The only exception is user-supplied paths (templates, file dialog selections).
