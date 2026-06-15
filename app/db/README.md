# app/db — Agent-For-Labs database

Single SQLite file (`agent.db`) holds **everything** the app needs at
rest: templates, instructions, the file library, sessions, pipeline
timing, audit, app settings, and encrypted secrets. The three
LLM/image/document caches live here too (no separate `cache.db`).

> **Source of truth:** `docs/database.md` (Combined/) and
> `Plan/backend3/database/database implementation plan.md`.

## Layout

```
app/db/
├── agent.db                 ← single SQLite file (created on first run)
├── README.md                ← this file
├── schema/
│   ├── 001_init.sql         ← 9 main tables + schema_version
│   ├── 002_cache.sql        ← 3 cache tables
│   ├── 003_indexes.sql      ← all indexes
│   ├── 004_seeds.sql        ← initial templates + global instruction
│   └── migrations/          ← future-version deltas (002_*, 003_*, ...)
└── backups/                 ← automatic .db backups before each migration
```

## Tables at a glance

| # | Table | Purpose |
|---|---|---|
| 1 | `templates` | Built-in (lab1, lab2) and user-created templates |
| 2 | `instructions` | Global / special / user_created (versioned) |
| 3 | `user_style` | Per-user style file (versioned, `is_empty` flag) |
| 4 | `app_settings` | Key-value settings (replaces `user_preferences.json`) |
| 5 | `secrets` | Fernet-encrypted tokens (HF token lives here) |
| 6 | `sessions` | One row per generation run |
| 7 | `session_files` | Many-to-many: session ↔ library_file |
| 8 | `library_file` | Deduplicated attached files (SHA-256) |
| 9 | `custom_template_annotations` | User annotations on custom templates |
| 10 | `pipeline_runs` | Per-stage timing of one session |
| 11 | `audit_log` | Actor / action / target / details audit trail |
| 12 | `llm_cache` | LLM response cache (cache_key = SHA-256 of inputs) |
| 13 | `image_cache` | Image cache (prompt_hash = SHA-256 of render spec) |
| 14 | `document_cache` | Final DOCX/PDF cache (content_hash) |

Plus the bookkeeping table:

| Table | Purpose |
|---|---|
| `schema_version` | Records applied migrations |

## Migrations

`app/backend/db/migrations.py` runs at startup:

1. Ensure `schema_version` table exists.
2. Read the current version (0 if DB is fresh).
3. For every `schema/00N_*.sql` file with a higher number, run it in
   a transaction.
4. Before the first migration on an existing DB, copy `agent.db` to
   `app/db/backups/agent_<timestamp>.db`.

To bump the schema: drop a new file `schema/005_xxx.sql` and call
`apply_pending_migrations()` again. Never edit a committed
`00N_*.sql`.

## Cryptography

`secrets.value_encrypted` is encrypted with **Fernet** (AES-128-CBC
+ HMAC-SHA256). The 32-byte key is generated with `os.urandom(32)`
on first run and written to `app/config/.db_key` with restricted
permissions. Loss of the key requires re-entering every secret.

## First-run behaviour

The first time the app starts on a fresh machine:

1. `app/db/agent.db` is created and the full schema is applied.
2. `seed.py` inserts the built-in templates (`lab1`, `lab2`) and
   the global instruction (content read from
   `app/instructions/global_instructions.md`).
3. If no `secrets` row exists for `hf.token`, the pipeline will
   HARD-STOP at Stage 5 and instruct the user to add it via
   Settings. Use the CLI helper:

   ```bash
   python -m app.backend.db.set_secret hf.token <TOKEN>
   ```

## Public API

Repositories live in `app/backend/db/repositories/`. The single
entry point for the bridge is `app.backend.db.facade.BridgeRepository`.

```python
from app.backend.db.facade import BridgeRepository
from app.backend.db.connection import Database

db = Database()  # opens app/db/agent.db, runs pending migrations
bridge = BridgeRepository(db)

sessions = bridge.sessions.list_recent(limit=50)
templates = bridge.templates.list_all()
bridge.secrets.set_hf_token("hf_xxx...")
```
