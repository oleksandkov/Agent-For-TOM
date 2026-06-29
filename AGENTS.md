# AGENTS.md — TOM

TOM is a local-first desktop AI agent (deep fork of Langflow + embedded Letta + Tauri UI). This file is for agents working on the **backend**. Frontend lives at `packages/desktop/**` (colleague's domain).

## Repo state (greenfield)

No source code yet. First commit ships with Section 1 of the execution plan. Do not invent files outside the paths below before that section lands.

## Backend layout

- Code: `packages/backend/src/backend/tom/`
- Tests: `packages/backend/tests/` (unit + `tests/e2e/`)
- Built-in MCP servers: `packages/mcp-servers/builtin/{tom-docx,tom-pdf,tom-fmt}/`
- Docs: `docs/{api,memory,skills,security,fork-upstream,smoke-checks}.md`
- Scripts: `scripts/{dev,backup,restore}.sh` (`build.sh` is colleague's)
- CI: `.github/workflows/backend-tests.yml`

## Stack

Python 3.12, `uv` for deps. FastAPI + uvicorn, pydantic v2, SQLAlchemy 2 + Alembic. SQLite + SQLCipher + sqlite-vec (encrypted at-rest). OS keyring (DPAPI/Keychain/libsecret). Deep fork of Langflow, Letta SDK embedded. Official MCP Python SDK (FastMCP). pytest + pytest-asyncio (`asyncio_mode=auto`), mypy `--strict`, ruff.

## Dev commands (run from `packages/backend/`)

- `uv sync` — install from lockfile
- `uv run pytest -q` — unit + e2e
- `uv run pytest tests/unit/test_x.py -q` — single file
- `uv run pytest -k pattern -q` — by name
- `uv run mypy src/backend/tom`
- `uv run ruff check src tests`
- `uv run python -m backend.tom serve --port 7878 --bind 127.0.0.1` — local backend
- `uv run python -m backend.tom db init` — init data dir
- `uv run python -m backend.tom providers health <name>` — provider health

## Required order before push

1. `ruff check`
2. `mypy src/backend/tom`
3. `pytest -q`
4. CI green (matrix: Ubuntu + Windows)

## Hard rules

- Backend binds **only** `127.0.0.1:7878`. Never `0.0.0.0`.
- Never edit Langflow core. All new logic in `packages/backend/src/backend/tom/`. Record rebase policy in `docs/fork-upstream.md`.
- API keys → OS keyring (`tom/provider/{type}/{name}`). Never in DB plain, never in `.env`.
- Never auto-approve an auto-generated skill. The user approves via the inbox.
- Auto-skill writes confined to `data_dir/skills/generated/{uuid}/`. Sandbox blocks other paths + network egress.
- Core memory is user-editable. Backend must not mutate without explicit user action.
- No telemetry, no auto-update, no CDN dependencies.

## Sync points — freeze contract before building

Write the contract into `docs/api.md` and notify the frontend colleague before starting:

- Section 4 — `GET/PATCH /v1/memory/core`
- Section 6 — `POST /v1/chat`, `/v1/sessions*`, `/v1/sessions/{id}/messages`
- Section 7 — MCP manifest schema (`schemas/mcp-manifest.schema.json`)
- Section 10 — `GET /v1/improvement/inbox`, `POST /v1/skills/{id}/{approve,reject}`

## Conventions

- PEP 621 `pyproject.toml`; commit `uv.lock`
- `mypy --strict` on `src/backend/tom/**`
- Alembic for migrations; initial migration creates all tables + vec index on `memory_records.embedding`
- Every state-changing API writes an `audit_log` row
- Live provider tests gated by `RUN_LIVE_PROVIDERS=1` (per-provider env var for the key)

## Data dir

- Windows: `%APPDATA%\tom\`
- macOS: `~/Library/Application Support/tom/`
- Linux: `~/.local/share/tom/`

Contains: `tom.db` (SQLCipher), `core_memory.json`, `uploads/`, `skills/{builtin,generated}/`, `keyring.id` (UUID only — never the key). Backup = copy the whole directory. Restore = copy back.

## Source of truth for execution order

`.kilo/plans/1782719707360-backend-personal-plan.md` — read before starting non-trivial work. Sections 1–13 are the order. `### Feedback` blocks are the human author's notes; never overwrite them.

## Out of scope (do not touch)

`packages/desktop/**`, Tauri, React, Rust, Phase 7 packaging (PyInstaller + Tauri bundle). Mobile, cloud sync, multi-user. If a request needs any of these, hand off to the frontend colleague.