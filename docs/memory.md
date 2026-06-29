# Memory layer (Section 4 — `backend.tom.memory`)

## Goal
Three tiers — **core** (user-editable structured memory), **archival** (vector
store of past context), **recall** (recency buffer) — exposed through a single
`TomMemory` facade. No Letta types, no DB sessions, no raw SQLCipher leaks out.

## Storage choice — direct on `sqlite-vec`, not the Letta SDK

`backend-imp.md` §4 gives us two options for the storage adapter:

> keep canonical structured memory in our SQLite (so it's encrypted with
> SQLCipher); Letta stores only indexes / mirrors if useful, **or we implement
> 3 tiers directly on top of `sqlite-vec` to avoid cross-process pain**.

We chose the **direct** path. `letta==0.16.8` stays in `pyproject.toml` only
because other Sections planned to consume it; nothing under
`backend/tom/memory/` imports it.

Why:

- Memory is per-user local; scale is tiny compared to production embedding stores.
- Cross-process state (a Letta server or agent process) makes tests heavier and
  harder to hermetic-ise. The `memory.api` router is plain FastAPI + SQLCipher
  on the same encrypted file.
- Tier 1 (core) is small enough to JSON-encode as a single string column.
  Tier 2 (archival) is a vec0 KNN query against the same DB. Tier 3 (recall)
  is process-local memory and is drained into archival on session close.

## Tier contracts

### 1. Core memory

- DB-canonical store: `memory_records` rows with `tier='core'` whose
  `content` is a `CoreMemory.model_dump_json()` snapshot.
- User-edit mirror: `data_dir/core_memory.json` is regenerated on every
  successful PATCH. On first read of a fresh DB we opportunistically
  re-import the JSON mirror (so a hand-edited file survives a DB reinstall).
- Optimistic concurrency: every PATCH carries `expected_version`; mismatch
  → `409 Conflict`. The body is also rejected with `422` if it has
  unrecognised fields (`extra="forbid"`).

### 2. Archival memory

- `memory_records.embedding_blob` (BLOB, float32 little-endian) holds the raw
  vector for inspection/redundancy.
- `memory_records_vec` (vec0 virtual table) holds the index used for KNN.
- `add_archival` writes both in a single transaction; `search_archival` is
  a single `vec_distance_cosine(...)` query joined to `memory_records`,
  filtered to `tier='archival'`.
- Embedding dimension is fixed at migration time (`EMBEDDING_DIM = 384` in
  `db/migrations/versions/0001_initial.py`). Changing dimension requires a new
  migration.

### 3. Recall memory

- Process-local `RecallBuffer` (thread-safe, bounded FIFO, per session).
- Drain on session close: `embed_on_close(session_id)` pulls the messages
  for the session, summarises + embeds (Section 5 wires in the real
  provider; `embed_on_close` ships with deterministic stubs so the rest of
  the pipeline is testable).
- Optionally pass `embedder=` / `summarizer=` injectables for tests.

## REST contract (frozen here)

| Method | Path                | Body                                  | Result                                |
| ------ | ------------------- | ------------------------------------- | ------------------------------------- |
| GET    | `/v1/memory/core`   | —                                     | `CoreMemory` (version, blocks, facts) |
| PATCH  | `/v1/memory/core`   | `CoreMemoryPatch` (with expected_version) | `CoreMemory` (new version)            |

- PATCH 200 → returns the new `CoreMemory`.
- PATCH 409 → `detail.reason == "version_mismatch"` with `current_version`.
- PATCH 422 → body validation (missing `expected_version`, unrecognised
  fields, etc.).

## Embed-on-close hook

`backend.tom.memory.embed_on_close.embed_on_close(session_id, ...)` is
called when a session is closed (chat API, Section 6). It:

1. Reads all `MessageORM` rows for `session_id`.
2. Returns `None` for empty sessions (no DB write).
3. Otherwise summarises + embeds and calls `TomMemory.add_archival`.

The default `_v1_summarise` / `_v1_embed` are deterministic stubs. They
take `summarizer=` / `embedder=` callables so the real provider wiring
(Section 5) is a one-line swap.

## Public API

```python
from backend.tom.memory import (
    TomMemory,
    CoreMemory,
    CoreMemoryPatch,
    CoreMemoryBlock,
    MemoryRecord,
    ArchivalHit,
    RecallMessage,
    Tier,
    EMBEDDING_DIM,
)

tom = TomMemory()
core = tom.read_core()                      # CoreMemory
tom.write_core(CoreMemoryPatch(...))        # CoreMemory
rec = tom.add_archival(
    session_id=sid,
    summary="…",
    embedding=[0.0] * EMBEDDING_DIM,
)
hits = tom.search_archival(query_embedding=…, k=5)
tom.push_recall(session_id=sid, role="user", content="…")
tom.drain_recall_for_session(sid)
```

The router (`backend.tom.memory.api`) exposes it under `/v1/memory/*` and
holds a process-wide singleton; tests can swap it via `set_memory(...)`.

## Out of scope here

- Embedding provider integration (Section 5)
- Session lifecycle + chat loop (Section 6)
- Memory auto-merge / summarisation policy (later sections; v0.1 keeps the
  raw `MemoryRecord`-per-write model so each row is independently retrievable
  and auditable).
