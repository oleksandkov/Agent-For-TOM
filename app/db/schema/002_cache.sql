-- app/db/schema/002_cache.sql
-- 3 cache tables (LLM, image, document) that live alongside the main
-- schema in agent.db. No more separate cache.db.

-- ─────────────────────────────────────────────────────────────────────
-- 12. llm_cache
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS llm_cache (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    cache_key         TEXT    NOT NULL UNIQUE,                     -- SHA-256 of inputs
    template_id       TEXT,
    template_name     TEXT,
    params            TEXT,                                          -- JSON
    user_files_hash   TEXT,
    style_hash        TEXT,
    response_text     TEXT    NOT NULL,                             -- LLM raw response
    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
    output_tokens     INTEGER NOT NULL DEFAULT 0,
    cached_tokens     INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT    NOT NULL DEFAULT (datetime('now')),
    hit_count         INTEGER NOT NULL DEFAULT 0
);

-- ─────────────────────────────────────────────────────────────────────
-- 13. image_cache
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS image_cache (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    prompt_hash       TEXT    NOT NULL UNIQUE,                     -- SHA-256 of render spec
    prompt            TEXT    NOT NULL,                             -- free-form text/prompt
    engine            TEXT    NOT NULL,                             -- matplotlib|huggingface|graphviz
    kind              TEXT    NOT NULL,                             -- diagram|illustration
    png_path          TEXT    NOT NULL,                             -- relative path
    width_px          INTEGER,
    height_px         INTEGER,
    file_size_bytes   INTEGER,
    created_at        TEXT    NOT NULL DEFAULT (datetime('now')),
    last_hit_at       TEXT
);

-- ─────────────────────────────────────────────────────────────────────
-- 14. document_cache
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS document_cache (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    content_hash    TEXT    NOT NULL UNIQUE,                         -- SHA-256 of filled.py minus [[ANCHOR:...]]
    docx_path       TEXT,
    pdf_path        TEXT,
    session_id      TEXT,                                            -- nullable: cache can outlive the session
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE SET NULL
);
