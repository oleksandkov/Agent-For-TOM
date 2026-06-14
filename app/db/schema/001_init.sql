-- app/db/schema/001_init.sql
-- Agent-For-TOM: 9 main tables + schema_version tracker.
-- Source of truth: docs/database.md (Combined/) +
--   Plan/backend3/database/database implementation plan.md
-- Conventions:
--   * PKs are TEXT UUIDs for business entities, INTEGER AUTOINCREMENT
--     for join tables and audit_log.
--   * DATETIME columns are ISO 8601 TEXT, UTC.
--   * JSON columns are TEXT (serialised).
--   * BOOL is INTEGER 0/1 (SQLite has no native bool).

PRAGMA foreign_keys = ON;

-- ─────────────────────────────────────────────────────────────────────
-- 0. schema_version
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    description TEXT,
    applied_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ─────────────────────────────────────────────────────────────────────
-- 1. templates
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS templates (
    id                  TEXT    PRIMARY KEY,
    name                TEXT    NOT NULL,
    display_name        TEXT,
    description         TEXT,
    script_path         TEXT,                           -- relative to repo root
    instructions_path   TEXT,                           -- relative to repo root
    is_builtin          INTEGER NOT NULL DEFAULT 0,    -- 0/1
    has_instructions    INTEGER NOT NULL DEFAULT 1,
    supports_images     INTEGER NOT NULL DEFAULT 1,
    placeholder_schema  TEXT,                           -- JSON
    gap_schema          TEXT,                           -- JSON
    source_file_id      TEXT,                           -- FK library_file.id
    created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (source_file_id) REFERENCES library_file(id) ON DELETE SET NULL
);

-- ─────────────────────────────────────────────────────────────────────
-- 2. instructions (versioned)
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS instructions (
    id              TEXT    PRIMARY KEY,
    template_id     TEXT,                                       -- NULL = global
    type            TEXT    NOT NULL,                            -- global|special|user_created
    content_hash    TEXT    NOT NULL,
    content_path    TEXT,                                        -- relative path of the .md file
    content         TEXT,                                        -- optional inline copy
    is_active       INTEGER NOT NULL DEFAULT 1,
    version         INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (template_id) REFERENCES templates(id) ON DELETE CASCADE
);

-- ─────────────────────────────────────────────────────────────────────
-- 3. user_style (versioned, has is_empty flag)
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS user_style (
    id              TEXT    PRIMARY KEY,
    content_hash    TEXT    NOT NULL,
    content_path    TEXT,
    content         TEXT,
    is_empty        INTEGER NOT NULL DEFAULT 1,                 -- 0/1
    is_active       INTEGER NOT NULL DEFAULT 1,
    version         INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ─────────────────────────────────────────────────────────────────────
-- 4. app_settings (key-value; replaces user_preferences.json)
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS app_settings (
    key         TEXT    PRIMARY KEY,
    value_json  TEXT    NOT NULL,                                -- JSON-serialised
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ─────────────────────────────────────────────────────────────────────
-- 5. secrets (Fernet-encrypted BLOB)
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS secrets (
    key              TEXT    PRIMARY KEY,
    value_encrypted  BLOB    NOT NULL,
    updated_at       TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ─────────────────────────────────────────────────────────────────────
-- 6. sessions
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sessions (
    id                       TEXT    PRIMARY KEY,
    name                     TEXT    NOT NULL,
    template_id              TEXT    NOT NULL,                  -- FK templates.id
    status                   TEXT    NOT NULL DEFAULT 'draft',  -- draft|processing|completed|failed|cancelled
    input_snapshot           TEXT    NOT NULL,                  -- JSON
    validation_result        TEXT,                              -- JSON NULL
    token_usage              TEXT,                              -- JSON NULL
    error_stage              TEXT,                              -- NULL = no error
    error_message            TEXT,
    duration_ms              INTEGER,
    global_instructions_hash TEXT,
    style_hash               TEXT,
    image_count              INTEGER NOT NULL DEFAULT 0,
    output_dir               TEXT,                              -- relative path
    docx_path                TEXT,
    pdf_path                 TEXT,
    created_at               TEXT    NOT NULL DEFAULT (datetime('now')),
    started_at               TEXT,
    completed_at             TEXT,
    cancelled_at             TEXT,
    failed_at                TEXT,
    FOREIGN KEY (template_id) REFERENCES templates(id) ON DELETE RESTRICT
);

-- ─────────────────────────────────────────────────────────────────────
-- 7. session_files  (join table)
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS session_files (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id       TEXT    NOT NULL,
    file_id          TEXT    NOT NULL,
    was_summarized   INTEGER NOT NULL DEFAULT 0,
    token_count_used INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
    FOREIGN KEY (file_id)    REFERENCES library_file(id) ON DELETE RESTRICT
);

-- ─────────────────────────────────────────────────────────────────────
-- 8. library_file (SHA-256 dedup)
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS library_file (
    id                TEXT    PRIMARY KEY,
    file_hash         TEXT    NOT NULL UNIQUE,                   -- SHA-256 hex
    original_name     TEXT    NOT NULL,
    original_type     TEXT,                                       -- MIME
    stored_path       TEXT    NOT NULL,                          -- relative path
    converted_text    TEXT,                                       -- NULL until converted
    conversion_status TEXT    NOT NULL DEFAULT 'pending',         -- pending|done|failed
    conversion_error  TEXT,
    file_size_bytes   INTEGER,
    token_count       INTEGER,
    converted_at      TEXT,
    created_at        TEXT    NOT NULL DEFAULT (datetime('now')),
    last_used_at      TEXT
);

-- ─────────────────────────────────────────────────────────────────────
-- 9. custom_template_annotations
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS custom_template_annotations (
    id             TEXT    PRIMARY KEY,
    template_id    TEXT    NOT NULL,
    source_file_id TEXT    NOT NULL,
    annotations    TEXT    NOT NULL,                             -- JSON array
    created_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (template_id)    REFERENCES templates(id)        ON DELETE CASCADE,
    FOREIGN KEY (source_file_id) REFERENCES library_file(id)     ON DELETE CASCADE
);

-- ─────────────────────────────────────────────────────────────────────
-- 10. pipeline_runs (per-stage timing)
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS pipeline_runs (
    id            TEXT    PRIMARY KEY,
    session_id    TEXT    NOT NULL,
    stage         TEXT    NOT NULL,                                -- file_convert|text_model|validate|image_gen|execute|compose
    status        TEXT    NOT NULL DEFAULT 'started',              -- started|ok|warn|error
    started_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    ended_at      TEXT,
    duration_ms   INTEGER,
    error_message TEXT,
    log_excerpt   TEXT,
    metrics       TEXT,                                            -- JSON
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

-- ─────────────────────────────────────────────────────────────────────
-- 11. audit_log
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS audit_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    at        TEXT    NOT NULL DEFAULT (datetime('now')),
    actor     TEXT    NOT NULL,                                    -- ui|pipeline|cli|system
    action    TEXT    NOT NULL,                                    -- session.create | session.cancel | template.edit | ...
    target_id TEXT,                                                 -- UUID of the affected entity
    details   TEXT                                                  -- JSON
);
