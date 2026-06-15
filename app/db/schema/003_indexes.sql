-- app/db/schema/003_indexes.sql
-- All indexes for the Agent-For-TOM database. Created AFTER tables
-- so that the schema stays in a single coherent forward migration.

-- ── sessions ──────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_sessions_created   ON sessions(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_status    ON sessions(status);
CREATE INDEX IF NOT EXISTS idx_sessions_template  ON sessions(template_id);
CREATE INDEX IF NOT EXISTS idx_sessions_completed ON sessions(completed_at) WHERE completed_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_sessions_name      ON sessions(name);

-- ── session_files ─────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_session_files_session ON session_files(session_id);
CREATE INDEX IF NOT EXISTS idx_session_files_file    ON session_files(file_id);

-- ── library_file ──────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_library_hash       ON library_file(file_hash);
CREATE INDEX IF NOT EXISTS idx_library_user       ON library_file(created_at);
CREATE INDEX IF NOT EXISTS idx_library_last_used  ON library_file(last_used_at);
CREATE INDEX IF NOT EXISTS idx_library_status     ON library_file(conversion_status);

-- ── instructions ──────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_instructions_template  ON instructions(template_id);
CREATE INDEX IF NOT EXISTS idx_instructions_active    ON instructions(is_active);
CREATE INDEX IF NOT EXISTS idx_instructions_type      ON instructions(type);
CREATE UNIQUE INDEX IF NOT EXISTS idx_instructions_active_uniq
    ON instructions(COALESCE(template_id, ''), type)
    WHERE is_active = 1;

-- ── templates ─────────────────────────────────────────────────────────
CREATE UNIQUE INDEX IF NOT EXISTS idx_templates_name ON templates(name);

-- ── user_style ────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_user_style_active ON user_style(is_active);
CREATE UNIQUE INDEX IF NOT EXISTS idx_user_style_active_uniq
    ON user_style(is_active)
    WHERE is_active = 1;

-- ── custom_template_annotations ───────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_annotations_template  ON custom_template_annotations(template_id);
CREATE INDEX IF NOT EXISTS idx_annotations_source    ON custom_template_annotations(source_file_id);

-- ── app_settings ──────────────────────────────────────────────────────
-- key is already PRIMARY KEY; no extra index needed.

-- ── secrets ───────────────────────────────────────────────────────────
-- key is already PRIMARY KEY; no extra index needed.

-- ── pipeline_runs ─────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_pipeline_session ON pipeline_runs(session_id);
CREATE INDEX IF NOT EXISTS idx_pipeline_stage   ON pipeline_runs(stage);
CREATE INDEX IF NOT EXISTS idx_pipeline_status  ON pipeline_runs(status);

-- ── audit_log ─────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_audit_at     ON audit_log(at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action);
CREATE INDEX IF NOT EXISTS idx_audit_target ON audit_log(target_id);

-- ── llm_cache ─────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_llm_cache_key   ON llm_cache(cache_key);
CREATE INDEX IF NOT EXISTS idx_llm_cache_tpl   ON llm_cache(template_name);

-- ── image_cache ───────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_image_cache_hash ON image_cache(prompt_hash);
CREATE INDEX IF NOT EXISTS idx_image_cache_kind ON image_cache(kind);

-- ── document_cache ────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_document_cache_hash ON document_cache(content_hash);
CREATE INDEX IF NOT EXISTS idx_document_cache_session ON document_cache(session_id);
