# Plan: AGENTS.md content for Agent-For-TOM

## Goal

Produce an `AGENTS.md` at the repo root that gives any future Kilo session (or any AI coding agent) the minimum context it needs to avoid the most likely mistakes in this repo, derived from the personal backend plan at `.kilo/plans/1782719707360-backend-personal-plan.md`.

## Decisions

- **Audience**: future Kilo sessions working on the backend. Frontend/Desktop sessions get their own AGENTS.md (their domain, out of scope here).
- **Length target**: ≤ ~80 lines. Every line must answer "would an agent miss this without help?" — otherwise omit.
- **Repo state as observed**: greenfield. Only `README.md`, `LICENSE`, `.gitignore`, `.kilo/` exist. No code, no manifests yet. AGENTS.md must be forward-looking (what will be true once Section 1 of the personal plan lands) but flag the current state so agents don't invent files that don't exist.
- **Out of scope to mention**: PyInstaller bundling, Tauri/desktop code, `packages/desktop/**`. Frontend is a colleague's job; mentioning the boundary is in-scope, the work itself is not.
- **Linkage**: Reference the personal plan path so agents know where to find the section-level execution order.

## Content to include (high-signal only)

### Header
- One-line repo purpose (lift from `README.md` headline, don't paraphrase).

### Repo state (as of this writing)
- Greenfield — no source code yet. Section 1 of the personal plan lands the first commit.
- Backend Python work only. `packages/desktop/**` does not exist yet and is owned by the frontend colleague — do not create.

### Backend layout (canonical)
- Root: `packages/backend/`
- Code: `packages/backend/src/backend/tom/`
- Tests: `packages/backend/tests/` (unit + `tests/e2e/`)
- Built-in MCP servers: `packages/mcp-servers/builtin/{tom-docx,tom-pdf,tom-fmt}/`
- Docs: `docs/{api,memory,skills,security,fork-upstream,smoke-checks}.md`
- Scripts: `scripts/{dev,backup,restore,build}.sh` (build.sh is colleague's)
- CI: `.github/workflows/backend-tests.yml`

### Stack (forward-looking, once Section 1 ships)
- Python 3.12, `uv` for deps (not pip/poetry/pdm)
- FastAPI + uvicorn, pydantic v2, SQLAlchemy 2 + Alembic
- SQLite + SQLCipher + sqlite-vec extension (encrypted at-rest)
- OS keyring (DPAPI/Keychain/libsecret) — service `tom`, user `db`
- Deep fork of Langflow, Letta SDK embedded
- Official MCP Python SDK (FastMCP for servers)
- pytest + pytest-asyncio (`asyncio_mode=auto`), mypy `--strict`, ruff

### Dev commands (exact, run from `packages/backend/`)
- `uv sync` — install deps from lockfile
- `uv run pytest -q` — unit + e2e
- `uv run pytest tests/unit/test_xxx.py -q` — single file
- `uv run pytest -k pattern -q` — by name
- `uv run mypy src/backend/tom`
- `uv run ruff check src tests`
- `uv run python -m backend.tom serve --port 7878 --bind 127.0.0.1` — local backend (only on loopback)
- `uv run python -m backend.tom db init` — initialize data dir
- `uv run python -m backend.tom providers health <name>` — provider health
- `make test` (when Makefile exists) — single pre-push gate

### Required order before push
1. `ruff check`
2. `mypy src/backend/tom`
3. `pytest -q`
4. CI workflow `backend-tests.yml` green (matrix: Ubuntu + Windows)

### Hard rules (do not violate)
- Backend listens **only** on `127.0.0.1:7878`. Never `0.0.0.0`.
- Never edit Langflow core. All new logic in `packages/backend/src/backend/tom/`. Record rebase policy in `docs/fork-upstream.md`.
- API keys go to OS keyring only (`tom/provider/{type}/{name}`). Never in DB plain, never in `.env`.
- Never auto-approve an auto-generated skill. Approval flow is the user's only path.
- Auto-skill writes confined to `data_dir/skills/generated/{uuid}/`. Sandbox blocks all other paths + network egress.
- Core memory is user-editable. Backend must not mutate without explicit user action.
- No telemetry, no auto-update, no CDN dependencies.

### Sync points (must freeze API contract before building)
Before starting these sections, write the contract into `docs/api.md` and notify the frontend colleague:
- Section 4 — `GET/PATCH /v1/memory/core`
- Section 6 — `POST /v1/chat`, `/v1/sessions*`, `/v1/sessions/{id}/messages`
- Section 7 — MCP manifest schema (`schemas/mcp-manifest.schema.json`)
- Section 10 — `GET /v1/improvement/inbox`, `POST /v1/skills/{id}/{approve,reject}`

### Conventions
- pyproject is PEP 621; commit `uv.lock`
- `pytest-asyncio` mode `auto`; mark async tests accordingly
- `mypy --strict` on `src/backend/tom/**`
- Alembic for migrations; initial migration creates all tables + vec index on `memory_records.embedding`
- All state-changing API writes an `audit_log` row
- Provider keys via keyring; live integration tests gated by `RUN_LIVE_PROVIDERS=1`

### Data dir (canonical, read by both roles)
- Windows: `%APPDATA%\tom\`
- macOS: `~/Library/Application Support/tom/`
- Linux: `~/.local/share/tom/`
- Contents: `tom.db` (SQLCipher), `core_memory.json`, `uploads/`, `skills/{builtin,generated}/`, `keyring.id` (UUID, never the key)
- Backup = copy the whole directory. Restore = copy back.

### Source of truth for execution order
- `.kilo/plans/1782719707360-backend-personal-plan.md` — read this before starting any non-trivial work. Sections 1–13 are the order. Each `### Feedback` block is the human author's notes; do not overwrite.

### Out of scope for this AGENTS.md (mention once, don't elaborate)
- `packages/desktop/**`, Tauri, React, Rust — colleague's domain
- Phase 7 packaging (PyInstaller + Tauri bundle) — colleague's
- Mobile, cloud sync, multi-user

## Validation

After landing AGENTS.md, sanity-check by:
1. New Kilo session reads it cold and answers correctly: "Where do I add a new MCP server tool?" → `packages/mcp-servers/builtin/<server>/server.py`, registered via manifest.
2. New session knows not to touch `packages/desktop/**` or Langflow core.
3. New session knows the bind address restriction without prompting.

## Risks

- **Docs drift**: AGENTS.md may go stale as the codebase grows. Treat as living document; revisit after Section 1 (Bootstrap) and Section 4 (Memory Layer) ships, since those introduce the first real entrypoints.
- **Over-specific commands**: lockfile-driven commands (`uv run pytest`) break if `pyproject.toml` changes. Acceptable trade-off — the alternative is vague advice that helps no one.

## Open question

- Whether `packages/desktop/AGENTS.md` should be cross-linked here or be fully standalone. Default plan: standalone, but mention "frontend agent: see `packages/desktop/AGENTS.md` (if it exists)" once that file lands.