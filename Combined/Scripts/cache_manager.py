"""cache_manager.py — 3-level caching for Agent-For-TOM pipeline.

Level 1 — LLM Cache: stores full LLM responses keyed by prompt fingerprint.
    Avoids redundant API calls when the same template + params + files
    are used again.

Level 2 — Image Cache: stores generated PNGs keyed by SHA256 of the
    render spec (prompt for huggingface, script for matplotlib).
    Avoids regenerating images across generation runs.

Level 3 — Document Cache: stores final DOCX/PDF paths keyed by SHA256
    of filled.py content. Avoids re-executing Python when filled.py
    hasn't changed.

All caches use SQLite as the backing store (see database.md for schema).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any


class CacheManager:
    """3-level cache backed by SQLite."""

    def __init__(self, db_path: str | Path = "cache.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        """Create tables if they don't exist."""
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS llm_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cache_key TEXT UNIQUE NOT NULL,
                    template_id INTEGER,
                    params TEXT,
                    user_files_hash TEXT,
                    response_text TEXT NOT NULL,
                    prompt_tokens INTEGER DEFAULT 0,
                    output_tokens INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT (datetime('now')),
                    hit_count INTEGER DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS image_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    prompt_hash TEXT UNIQUE NOT NULL,
                    prompt TEXT NOT NULL,
                    engine TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    png_path TEXT NOT NULL,
                    width_px INTEGER,
                    height_px INTEGER,
                    file_size_bytes INTEGER,
                    created_at TEXT DEFAULT (datetime('now')),
                    last_hit_at TEXT DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS document_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content_hash TEXT UNIQUE NOT NULL,
                    docx_path TEXT,
                    pdf_path TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                );

                CREATE INDEX IF NOT EXISTS idx_llm_cache_key ON llm_cache(cache_key);
                CREATE INDEX IF NOT EXISTS idx_image_cache_hash ON image_cache(prompt_hash);
                CREATE INDEX IF NOT EXISTS idx_document_cache_hash ON document_cache(content_hash);
            """)

    def _conn(self) -> sqlite3.Connection:
        """Get a new connection (context manager closes it)."""
        # Using check_same_thread=False for simplicity; in production
        # use a connection pool or thread-local connections.
        return sqlite3.connect(str(self.db_path), check_same_thread=False)

    # -- Level 1: LLM Cache ------------------------------------------------

    def _fingerprint(
        self,
        template_id: int,
        params: dict[str, Any],
        user_files_content: str | None = None,
    ) -> str:
        """Generate a deterministic cache key from generation parameters."""
        raw = json.dumps(
            {
                "template_id": template_id,
                "params": params,
                "user_files_hash": (
                    hashlib.sha256(
                        (user_files_content or "").encode("utf-8")
                    ).hexdigest()
                ),
            },
            sort_keys=True,
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get_llm_response(
        self,
        template_id: int,
        params: dict[str, Any],
        user_files_content: str | None = None,
    ) -> str | None:
        """Retrieve cached LLM response, or None if cache miss."""
        key = self._fingerprint(template_id, params, user_files_content)
        with self._conn() as conn:
            row = conn.execute(
                "SELECT response_text FROM llm_cache WHERE cache_key = ?",
                (key,),
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE llm_cache SET hit_count = hit_count + 1 "
                    "WHERE cache_key = ?",
                    (key,),
                )
                conn.commit()
                return row[0]
        return None

    def set_llm_response(
        self,
        template_id: int,
        params: dict[str, Any],
        user_files_content: str | None,
        response_text: str,
        prompt_tokens: int = 0,
        output_tokens: int = 0,
    ) -> None:
        """Store LLM response in cache."""
        key = self._fingerprint(template_id, params, user_files_content)
        user_hash = (
            hashlib.sha256(
                (user_files_content or "").encode("utf-8")
            ).hexdigest()
        )
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO llm_cache
                   (cache_key, template_id, params, user_files_hash,
                    response_text, prompt_tokens, output_tokens)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    key, template_id, json.dumps(params),
                    user_hash, response_text, prompt_tokens, output_tokens,
                ),
            )
            conn.commit()

    def invalidate_llm_cache(self, template_id: int | None = None) -> int:
        """Clear LLM cache. Returns number of rows deleted."""
        with self._conn() as conn:
            if template_id is not None:
                cursor = conn.execute(
                    "DELETE FROM llm_cache WHERE template_id = ?",
                    (template_id,),
                )
            else:
                cursor = conn.execute("DELETE FROM llm_cache")
            conn.commit()
            return cursor.rowcount

    # -- Level 2: Image Cache ----------------------------------------------

    @staticmethod
    def _hash_render_spec(render_spec: dict[str, Any]) -> str:
        """Hash the render specification for image deduplication."""
        # Use the relevant field: prompt for HF, script for matplotlib
        content = json.dumps(render_spec, sort_keys=True)
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def get_image(self, render_spec: dict[str, Any]) -> str | None:
        """Retrieve cached image path, or None if cache miss."""
        h = self._hash_render_spec(render_spec)
        with self._conn() as conn:
            row = conn.execute(
                "SELECT png_path FROM image_cache WHERE prompt_hash = ?",
                (h,),
            ).fetchone()
            if row:
                path = Path(row[0])
                if path.exists():
                    conn.execute(
                        "UPDATE image_cache SET last_hit_at = "
                        "datetime('now') WHERE prompt_hash = ?",
                        (h,),
                    )
                    conn.commit()
                    return str(path)
                # File missing — delete stale entry
                conn.execute(
                    "DELETE FROM image_cache WHERE prompt_hash = ?",
                    (h,),
                )
                conn.commit()
        return None

    def set_image(
        self,
        render_spec: dict[str, Any],
        png_path: str,
        engine: str = "",
        kind: str = "",
        width: int = 0,
        height: int = 0,
        file_size: int = 0,
    ) -> None:
        """Store generated image in cache."""
        h = self._hash_render_spec(render_spec)
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO image_cache
                   (prompt_hash, prompt, engine, kind,
                    png_path, width_px, height_px, file_size_bytes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    h,
                    json.dumps(render_spec, sort_keys=True),
                    engine, kind, png_path,
                    width, height, file_size,
                ),
            )
            conn.commit()

    # -- Level 3: Document Cache -------------------------------------------

    @staticmethod
    def _hash_filled_py(content: str) -> str:
        """Hash filled.py content for document deduplication."""
        # Strip anchor markers before hashing so cosmetic changes
        # to image placement don't invalidate the cache.
        clean = re.sub(r"\[\[ANCHOR:[^\]]+\]\]", "", content)
        return hashlib.sha256(clean.encode("utf-8")).hexdigest()

    def get_document(
        self, filled_py_content: str
    ) -> tuple[str | None, str | None]:
        """Retrieve cached document paths, or (None, None) on miss."""
        h = self._hash_filled_py(filled_py_content)
        with self._conn() as conn:
            row = conn.execute(
                "SELECT docx_path, pdf_path FROM document_cache "
                "WHERE content_hash = ?",
                (h,),
            ).fetchone()
            if row:
                docx, pdf = row
                docx_ok = docx and Path(docx).exists()
                pdf_ok = pdf and Path(pdf).exists()
                if docx_ok and pdf_ok:
                    return docx, pdf
                # Stale entry
                conn.execute(
                    "DELETE FROM document_cache WHERE content_hash = ?",
                    (h,),
                )
                conn.commit()
        return None, None

    def set_document(
        self, filled_py_content: str, docx_path: str, pdf_path: str
    ) -> None:
        """Store generated document paths in cache."""
        h = self._hash_filled_py(filled_py_content)
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO document_cache
                   (content_hash, docx_path, pdf_path)
                   VALUES (?, ?, ?)""",
                (h, docx_path, pdf_path),
            )
            conn.commit()

    # -- Maintenance --------------------------------------------------------

    def clear_all(self) -> None:
        """Clear all caches (for testing)."""
        with self._conn() as conn:
            conn.execute("DELETE FROM llm_cache")
            conn.execute("DELETE FROM image_cache")
            conn.execute("DELETE FROM document_cache")
            conn.commit()

    def stats(self) -> dict[str, int]:
        """Return cache statistics."""
        with self._conn() as conn:
            return {
                "llm_entries": conn.execute(
                    "SELECT COUNT(*) FROM llm_cache"
                ).fetchone()[0],
                "image_entries": conn.execute(
                    "SELECT COUNT(*) FROM image_cache"
                ).fetchone()[0],
                "document_entries": conn.execute(
                    "SELECT COUNT(*) FROM document_cache"
                ).fetchone()[0],
                "total_hits": conn.execute(
                    "SELECT COALESCE(SUM(hit_count), 0) FROM llm_cache"
                ).fetchone()[0],
            }


import re  # noqa: E402 (needed for _hash_filled_py)
