# TOM — Personal Backend Working Plan

> Plan for the **human backend developer**. Frontend (Tauri desktop, Phase 7 packaging) is the colleague's job and is excluded here.
> Each section = **Description → Feedback (yours, do not edit) → What to do**.
> Edit only the `### Feedback` blocks; agent will never overwrite them.

---

## Conventions

- **Backend root**: `packages/backend/` (deep fork of Langflow)
- **TOM layer**: `packages/backend/src/backend/tom/`
- **Tests**: `packages/backend/tests/`
- **Python**: 3.12, **uv** for deps, **PyInstaller** for sidecar (deferred — colleague)
- **CI**: `.github/workflows/backend-tests.yml`
- **Sync points**: freeze an API contract before crossing a major boundary (chat, memory, MCP, self-improvement). See `/v1/*` table in master plan.

---

## Section 1 — Environment & Repository Bootstrap

### Description
Stand up the repo skeleton, Python tooling, and CI. No business logic yet. Goal: `pytest` and `mypy` run green on an empty TOM module; CI is wired and required to pass before any PR merges.

### Feedback
Today's goal is to prepare the branch for future steps and development. Create the foundational repository structure with Python tooling and CI configuration.

### What to do
- [ ] `mkdir packages/backend`, `mkdir packages/mcp-servers`, `mkdir docs`, `mkdir scripts`
- [ ] Create `pyproject.toml` (PEP 621) with: `python = ">=3.12"`, deps placeholder for `letta-sdk`, `mcp`, `sqlalchemy>=2`, `alembic`, `pydantic>=2`, `fastapi`, `uvicorn`, `httpx`, `keyring`, `sqlcipher3`, `sqlite-vec`, `python-dotenv`
- [ ] Dev deps: `pytest`, `pytest-asyncio`, `pytest-cov`, `mypy`, `ruff`, `types-*`
- [ ] `uv sync` to materialize `uv.lock`
- [ ] Create empty `src/backend/tom/__init__.py`
- [ ] `pytest -q` → 0 collected, exit 0
- [ ] `mypy src/backend/tom` → success
- [ ] `.github/workflows/backend-tests.yml`: jobs = `lint` (ruff), `typecheck` (mypy), `test` (pytest + coverage). Run on Ubuntu + Windows matrix.
- [ ] First commit `chore(backend): bootstrap uv project + CI` — must be green
- [ ] Add `LICENSE` (MIT, already at repo root) reference in `pyproject.toml`

---

## Section 2 — Langflow Fork + TOM Layer Skeleton

### Description
Establish Langflow as a fork and a parallel TOM module that owns our custom code. Rule of thumb: **never edit Langflow core**; all new logic in `backend/tom/`. Keeps upstream merges tractable.

### Feedback
<!-- Add notes about Langflow version, which release we pin, merge cadence, any vendor quirks. -->

### What to do
- [ ] Add Langflow as a sub-module / pinned dependency in `pyproject.toml` (`langflow = "1.x.y"`) OR vendor a fork at `packages/backend/vendor/langflow` — decide based on disk + update policy
- [ ] Pin commit SHA; record in `docs/fork-upstream.md` (which Langflow commit we fork from, how to rebase)
- [ ] Create `src/backend/tom/` tree: `memory/`, `db/`, `api/`, `providers/`, `mcp_bridge/`, `improvement/`, `branding/`, `security/`, `instructions.py`, `config.py`
- [ ] `branding/__init__.py` exposes product name, version, support links
- [ ] Add a `pytest` smoke that imports `from backend.tom import branding` and asserts TOM version string
- [ ] Local `dev.sh` script: `uv run python -m backend.tom serve --port 7878 --bind 127.0.0.1`
- [ ] Re-run CI; must stay green
- [ ] Document the merge policy in `docs/fork-upstream.md` (when, how, who reviews)

---

## Section 3 — Data Storage Layer (SQLite + SQLCipher + sqlite-vec + Keyring)

### Description
Set up encrypted local DB, vector storage in the same `.db` file, and key management via OS keyring. After this section, the DB initializes cleanly, key generation is idempotent, and migrations run.

### Feedback
<!-- Notes about SQLCipher version, sqlite-vec loading tricks on Windows, keyring backend behaviors. -->

### What to do
- [ ] `src/backend/tom/db/__init__.py`: re-exports `engine`, `session`, `init_db`
- [ ] `paths.py` returns platform-correct data dir: `%APPDATA%\tom\` (Win), `~/Library/Application Support/tom/` (mac), `~/.local/share/tom/` (Linux). Idempotent `ensure_dirs()`.
- [ ] `keyring.py`: `get_or_create_key()` → uses `keyring` lib, service name `tom`, username `db`. Writes `keyring.id` (UUID) into data dir. Never the key itself.
- [ ] `cipher.py`: opens SQLCipher connection with hex key from keyring, sets `PRAGMA key`, loads `sqlite-vec` extension on connection
- [ ] `models.py`: SQLAlchemy 2 models for `Session`, `Message`, `MemoryRecord`, `Skill`, `Pattern`, `ProviderConfig`, `AuditLog` (schemas from master plan §"Таблиці БД")
- [ ] `migrations/` via Alembic; initial migration creates all tables + indexes on `embedding` (vec)
- [ ] `init_db()` runs migrations on app startup; logs timing
- [ ] Unit tests: `test_paths.py`, `test_keyring.py` (mocked), `test_init_db.py` (in-memory SQLCipher equivalent or tempdir), `test_models_roundtrip.py`
- [ ] Manual smoke: run `python -m backend.tom db init`, file appears at expected path, re-run is a no-op

---

## Section 4 — Memory Layer (Letta Wrapper, 3 Tiers)

### Description
Embed Letta SDK as a Python module (not a separate service) and expose a `TomMemory` class with three tiers: **core** (editable structured facts), **archival** (vector store of past context), **recall** (recency buffer). All access goes through this wrapper — no Letta types leak into the rest of the codebase.

### Feedback
<!-- Letta version, how it serializes core memory, embedding model choice, dedup/capacity policy. -->

### What to do
- [ ] `src/backend/tom/memory/__init__.py`
- [ ] `types.py`: `Tier = Literal["core", "archival", "recall"]`; `MemoryRecord(id, tier, content, embedding, source_session_id, created_at, confidence)`
- [ ] `tom_memory.py`: `class TomMemory` with `read_core()`, `write_core()`, `add_archival(session_id, summary, embedding)`, `search_archival(query_embedding, k=10)`, `push_recall(msg)`, `drain_recall_for_session()`
- [ ] Storage adapter: keep **canonical structured memory in our SQLite** (so it's encrypted with SQLCipher); Letta stores only indexes / mirrors if useful, or we implement 3 tiers directly on top of `sqlite-vec` to avoid cross-process pain
- [ ] Confirm choice in `docs/memory.md` and link from `Feedback` above
- [ ] `embed_on_close.py`: hook called by chat API when a session closes — produces session summary + embedding, writes to archival
- [ ] REST: `GET /v1/memory/core`, `PATCH /v1/memory/core` (user-editable). Contract frozen here.
- [ ] Unit tests: core roundtrip, archival search, recall order, write-conflict on core
- [ ] E2E test (curl script in `tests/e2e/`): create session, post messages, close → assert archival row exists with embedding

---

## Section 5 — Provider Abstraction (Ollama + OpenAI + Anthropic + Google + Custom)

### Description
Single interface so the agent and chat API don't care which LLM runs. Health checks per provider, key storage via keyring, fallback chains.

### Feedback
<!-- Which cloud providers for MVP, whether to enable streaming everywhere, Ollama default model. -->

### What to do
- [ ] `src/backend/tom/providers/__init__.py`
- [ ] `base.py`: `class Provider(Protocol)` → `chat(messages, **kw) -> AsyncIterator[Token]`, `embed(texts) -> list[list[float]]`, `health() -> HealthReport`
- [ ] `ollama.py`: hit `http://localhost:11434`, list models, stream
- [ ] `openai_compat.py`: works for OpenAI + any OpenAI-compatible endpoint (custom URL). Streaming via SSE.
- [ ] `anthropic.py`: official SDK, streaming
- [ ] `google.py`: Vertex / Gemini API
- [ ] `registry.py`: reads `provider_configs` table; instantiates the right provider; applies `fallback_chain_json` on failure
- [ ] API keys via keyring (`tom/provider/{type}/{name}`); never store in DB plain
- [ ] CLI: `python -m backend.tom providers health <name>` prints `{"ok": true/false, "latency_ms": ..., "models": [...]}`
- [ ] Unit tests with mocked HTTP; one live integration test gated by `RUN_LIVE_PROVIDERS=1` and per-provider env var
- [ ] Run health check against local Ollama — required to pass before Phase 6 (chat) opens

---

## Section 6 — Chat API, Sessions, Streaming

### Description
The single biggest user-facing backend surface: send a message, stream a response, persist to history, close a session. Sessions are independent, scoped to single user.

### Feedback
<!-- Streaming format (SSE vs WebSocket), token budget per session, eviction policy, message ordering. -->

### What to do
- [ ] `src/backend/tom/api/chat.py`: `POST /v1/chat` accepts `{session_id, content, attachments?}`, streams via SSE
- [ ] `src/backend/tom/api/sessions.py`: `GET /v1/sessions`, `POST /v1/sessions` (open with provider + model), `PATCH /v1/sessions/{id}` (rename), `POST /v1/sessions/{id}/close` (triggers embed-on-close hook)
- [ ] `src/backend/tom/api/messages.py`: `GET /v1/sessions/{id}/messages`
- [ ] `orchestrator.py`: composes memory recall → system prompt → provider call → tool dispatch → write-back; this is where core memory gets injected on every turn
- [ ] Tool-call loop: if provider returns tool_calls, dispatch to MCP bridge (Section 7), feed results back
- [ ] Token accounting: estimate and store `total_tokens` per session
- [ ] Concurrency: per-session lock to avoid interleaved writes
- [ ] Contracts frozen here and documented in `docs/api.md` (chat, sessions, messages)
- [ ] Tests: unit for orchestrator (mocked provider), e2e with local Ollama: send → receive stream → close → assert archival row

---

## Section 7 — MCP Infrastructure (Client + Server Lifecycle)

### Description
MCP is how TOM extends itself. We need (a) an MCP client lifecycle in-process and stdio, (b) a loader for built-in servers and user-approved auto-generated servers, and (c) a manifest schema each server must satisfy.

### Feedback
<!-- Which Python MCP SDK version, manifest format (JSON vs TOML), skill activation permissions. -->

### What to do
- [ ] `src/backend/tom/mcp_bridge/__init__.py`
- [ ] `manifest.py`: dataclass `McpServerManifest(name, version, entrypoint, capabilities: list[str], permissions: list[str], risk_flags: list[str])`. Validated against JSON schema in `schemas/mcp-manifest.schema.json`
- [ ] `client.py`: connect to stdio-based MCP server, list tools, invoke tools, enforce per-call timeout
- [ ] `loader.py`: scans `data_dir/skills/builtin/` and `data_dir/skills/generated/` (the latter only `status='active'`)
- [ ] `registry.py`: in-memory map `name -> running server`, with start/stop/health
- [ ] `dispatcher.py`: maps `(tool_name, args)` from chat orchestrator → actual server call
- [ ] Health check: every tool invocation goes through `invocation_log` table for audit
- [ ] Tests: unit for manifest validation and dispatcher, e2e with one fixture server (echo + ping)

---

## Section 8 — Built-in MCP Servers (tom-docx, tom-pdf, tom-fmt)

### Description
Three concrete servers the user can call from chat on day one. Each is a small Python package with a manifest, registered in the loader, and tested with real fixtures.

### Feedback
<!-- Exact doc features needed (TOC, tables, images?), PDF generation library (reportlab vs weasyprint), formatting tool scope (whitespace? lint?). -->

### What to do
- [ ] `packages/mcp-servers/builtin/tom-docx/`
  - [ ] `manifest.json`, `server.py` (FastMCP), `pyproject.toml`
  - [ ] Tools: `read_docx(path)`, `write_docx(path, content_json)`, `append_section(...)`, `extract_text(...)`
  - [ ] Use `python-docx`; tests with fixture `.docx`
- [ ] `packages/mcp-servers/builtin/tom-pdf/`
  - [ ] Tools: `read_pdf(path)`, `extract_pages(path, n)`, `render_pdf_from_html(html, out_path)`
  - [ ] Use `pypdf`, `weasyprint` (or vendor static binary if WeasyPrint too heavy — document the choice)
- [ ] `packages/mcp-servers/builtin/tom-fmt/`
  - [ ] Tools: `format_text(text, style)`, `detect_language(text)`, `lint_markdown(text)`
  - [ ] No external deps beyond stdlib + `langdetect` (optional)
- [ ] Each: pytest unit tests, e2e (call via dispatcher, assert response shape)
- [ ] Update `tests/e2e/full_flow.py`: open session → chat triggers `tom_docx.read_docx` on fixture file → result returned → assert

---

## Section 9 — Agent Instructions & System Prompt

### Description
The instructions file is what makes TOM behave like TOM, not generic Langflow. Lives in code (version-controlled) but copyable per-user overrides into `core_memory.json` are allowed for personalization.

### Feedback
<!-- Tone, language defaults, what the agent must NEVER do, what it must always ask the user about. -->

### What to do
- [ ] `src/backend/tom/instructions/base_prompt.md` — full system prompt
- [ ] Sections the prompt must cover: identity, tool-use policy, memory policy (what goes to core vs archival), self-improvement policy (never auto-modify without approval), refusal rules, language preference, formatting style
- [ ] `src/backend/tom/instructions/loader.py` merges base + user overrides from `data_dir/core_memory.json`
- [ ] Expose `/v1/instructions` endpoint to read, `PUT /v1/instructions/override` to set per-user override (max length cap, audit-logged)
- [ ] Tests: render prompt for empty user → stable SHA; render with override → user section included
- [ ] Manual review: sit and skim with colleague + self — at least one human pass, document reviewer in commit

---

## Section 10 — Self-Improvement Pipeline

### Description
The "kill feature": hourly pattern detection → LLM-generated MCP skill draft → sandbox test → user approval queue. Backend builds A, B, C, D; frontend (E) is colleague's. Contract between them must be frozen before either starts.

### Feedback
<!-- Detection thresholds (similarity X, min N occurrences), which LLM does drafting, sandbox platform rules. -->

### What to do
- [ ] **10.A Pattern detector**
  - [ ] `improvement/detector.py`: asyncio cron, every hour by default (configurable)
  - [ ] Embedding-based clustering of `archival` rows from last 7 days
  - [ ] Threshold: ≥3 sessions with cosine > X in window → `Pattern` row
  - [ ] Unit: threshold logic; e2e: seed sessions, run detector, assert patterns
- [ ] **10.B Skill scaffolder**
  - [ ] `improvement/scaffolder.py`: takes a Pattern + sample sessions, builds a prompt to our chosen LLM, asks for an MCP server (Python, stdio), constrained to a fixed template
  - [ ] Generates files under `data_dir/skills/generated/{uuid}/`: `manifest.json`, `server.py`, `pyproject.toml` (with deps pinned)
  - [ ] `py_compile` check; second LLM call reviews code for red flags (network calls, `subprocess`, `os.system`, `eval`, file writes outside scope)
  - [ ] Marks skill `status='draft'` if clean
- [ ] **10.C Sandbox**
  - [ ] `improvement/sandbox.py`: subprocess invocation of generated server, with per-call timeout, **no network** (per-platform: Linux namespaces via `unshare`, macOS `pf` rule, Windows outbound firewall rule — pick one and document), read-only FS except `data_dir/skills/generated/{uuid}/`
  - [ ] Smoke test the sandbox on each platform (CI matrix if feasible)
- [ ] **10.D Approval API**
  - [ ] `GET /v1/improvement/inbox`: lists Pattern + draft Skill pairs (preview payload includes code + risk flags)
  - [ ] `POST /v1/skills/{id}/approve`: `draft → active`, registers in MCP loader; audit log entry
  - [ ] `POST /v1/skills/{id}/reject`: marks `rejected`, archives pattern; audit log
  - [ ] Contracts frozen here and posted to colleague via `docs/api.md` (this is the [SYNC] boundary)
- [ ] Tests: full e2e: seed pattern → scaffolder → sandbox runs → pending in inbox → approve → active in registry

---

## Section 11 — Permissions & Security Hardening

### Description
Cross-cutting. Some items are colocated with the section that introduces them — this section is the audit + final pass before any release work.

### Feedback
<!-- Items you want extra-strict on, e.g. "never allow generated skill to write outside generated dir." -->

### What to do
- [ ] Confirm bind: backend listens **only** on `127.0.0.1:7878` (no `0.0.0.0`)
- [ ] Audit all write-paths: can a malicious or buggy input reach outside `data_dir`? Add guard test
- [ ] Tool-call allowlist per session: by default only manifest-declared tools; deny rest
- [ ] Auto-skill risk flags: at minimum must flag `urllib.request`, `socket.*`, `requests.`, `subprocess`, `shutil.rmtree`, `os.remove`, `eval`/`exec`, `open(path, 'w')` outside manifest `writable_paths`
- [ ] Rate limit: `/v1/chat` per session (default 30 req/min, configurable)
- [ ] Audit log: every state-changing API writes an `audit_log` row
- [ ] `.env.example` documents required env vars without values
- [ ] Threat-model doc in `docs/security.md` (frontend developer may need to read it — link from colleague's plan)

---

## Section 12 — Testing Strategy

### Description
Continuous through every section, consolidated here. You run unit + e2e before each PR; CI gates the merge.

### Feedback
<!-- Coverage target, slowest tests, which ones you allow to be skipped in CI. -->

### What to do
- [ ] `pytest -q` config: `asyncio_mode=auto`, coverage threshold 75% (increases each section, document target in commit)
- [ ] `mypy --strict` on `src/backend/tom/**`
- [ ] `ruff check` and `ruff format --check` in CI
- [ ] E2E tests in `tests/e2e/`: each is a Python script using `httpx` against `localhost:7878`
- [ ] Fixture data directory generated in tempdir for test isolation
- [ ] Smoke checklist per section appended to `docs/smoke-checks.md`
- [ ] Make `make test` the single command backend runs locally before push

---

## Section 13 — Release Readiness (Documentation, Backup/Restore, Cleanup)

### Description
Backend deliverable for Phase 8 minus packaging. Docs that the colleague needs to integrate, backup/restore tested, license clean.

### Feedback
<!-- Which docs formats you prefer (Markdown vs MDX), how detailed API examples should be. -->

### What to do
- [ ] `docs/api.md`: full REST contract with curl examples for every endpoint (this is the contract colleague consumes)
- [ ] `docs/memory.md`: user-facing description of the 3 tiers + how core_memory.json is edited
- [ ] `docs/skills.md`: how MCP servers are written, where they live, how to enable
- [ ] `scripts/backup.sh` and `scripts/restore.sh`: tar/untar data_dir, encrypt with `age`/`gpg` optional; smoke-test restore against tempdir
- [ ] `scripts/dev.sh`: starts backend + spawns any built-in MCP servers; readable by colleague
- [ ] LICENSE audit: confirm every runtime dep is MIT/BSD/Apache-compatible; flag any GPL
- [ ] `README.md` of backend: how to run, how to test, how to add a provider, how to add a skill
- [ ] Hand off `docs/api.md` to colleague via PR comment

---

## Out of Scope (your job)

- Phase 7 — PyInstaller + Tauri bundling (colleague)
- Any frontend code, React components, Tauri Rust
- `packages/desktop/**` directory
- Mobile, cloud sync, multi-user

---

## Open Questions

- Which Langflow pin? (record in Section 2 Feedback once known)
- Skill scaffolder: which model does the drafting? Same as user's chat provider, or a separate "system" provider configured in `provider_configs`?
- Sandbox on Windows: firewall rule vs WSL — pick one and lock in Section 10.C Feedback
- Where do we draw the API contract freeze deadlines relative to Section 10 (so colleague has time)?
