"""app/backend/db/repositories/cache.py

Unified cache repository backed by the three tables ``llm_cache``,
``image_cache``, and ``document_cache``. Replaces the standalone
``app/backend/cache_manager.py`` for new code; the old file is kept
as a thin shim for back-compat.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .base import BaseRepository


def _hash_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _hash_spec(spec: Any) -> str:
    """Stable hash of an arbitrary JSON-serialisable spec."""
    return _hash_text(json.dumps(spec, sort_keys=True, ensure_ascii=False, default=str))


class CacheRepository(BaseRepository):
    """Combined cache: LLM, image, document."""

    # ----- LLM cache --------------------------------------------------

    def get_llm_response(self, cache_key: str) -> dict[str, Any] | None:
        row = self._fetchone(
            "SELECT * FROM llm_cache WHERE cache_key = ?", (cache_key,)
        )
        if row is None:
            return None
        d = self.row_to_dict(row)
        if d is None:
            return None
        try:
            d["params"] = json.loads(d.get("params") or "{}")
        except (TypeError, ValueError):
            d["params"] = {}
        # Bump hit count.
        self._execute(
            "UPDATE llm_cache SET hit_count = hit_count + 1 WHERE cache_key = ?",
            (cache_key,),
        )
        return d

    def set_llm_response(
        self,
        *,
        cache_key: str,
        template_id: str | None,
        template_name: str | None,
        params: dict[str, Any] | None,
        user_files_hash: str | None,
        style_hash: str | None,
        response_text: str,
        prompt_tokens: int = 0,
        output_tokens: int = 0,
        cached_tokens: int = 0,
    ) -> None:
        self._execute(
            """INSERT INTO llm_cache
               (cache_key, template_id, template_name, params,
                user_files_hash, style_hash, response_text,
                prompt_tokens, output_tokens, cached_tokens)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(cache_key) DO UPDATE SET
                   response_text  = excluded.response_text,
                   prompt_tokens  = excluded.prompt_tokens,
                   output_tokens  = excluded.output_tokens,
                   cached_tokens  = excluded.cached_tokens""",
            (
                cache_key, template_id, template_name,
                json.dumps(params or {}, ensure_ascii=False, default=str),
                user_files_hash, style_hash, response_text,
                prompt_tokens, output_tokens, cached_tokens,
            ),
        )

    def fingerprint_llm(
        self,
        *,
        template_id: int | str | None,
        params: dict[str, Any],
        user_files_content: str | None = None,
        style_hash: str | None = None,
    ) -> str:
        raw = json.dumps(
            {
                "template_id": template_id,
                "params": params,
                "user_files_hash": _hash_text(user_files_content or ""),
                "style_hash": style_hash or "",
            },
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )
        return _hash_text(raw)

    # ----- Image cache ------------------------------------------------

    def get_image(self, prompt_hash: str) -> dict[str, Any] | None:
        row = self._fetchone(
            "SELECT * FROM image_cache WHERE prompt_hash = ?", (prompt_hash,)
        )
        if row is None:
            return None
        d = self.row_to_dict(row)
        if d is None:
            return None
        self._execute(
            "UPDATE image_cache SET last_hit_at = ? WHERE prompt_hash = ?",
            (self.now(), prompt_hash),
        )
        return d

    def set_image(
        self,
        *,
        render_spec: dict[str, Any],
        png_path: str,
        engine: str,
        kind: str,
        width: int = 0,
        height: int = 0,
        file_size: int = 0,
    ) -> str:
        prompt_hash = _hash_spec(render_spec)
        prompt_text = str(render_spec.get("prompt") or render_spec.get("script") or "")
        self._execute(
            """INSERT INTO image_cache
               (prompt_hash, prompt, engine, kind, png_path,
                width_px, height_px, file_size_bytes, last_hit_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(prompt_hash) DO UPDATE SET
                   png_path        = excluded.png_path,
                   file_size_bytes = excluded.file_size_bytes,
                   last_hit_at     = excluded.last_hit_at""",
            (prompt_hash, prompt_text, engine, kind, png_path,
             width, height, file_size, self.now()),
        )
        return prompt_hash

    # ----- Document cache ---------------------------------------------

    def get_document(self, content_hash: str) -> dict[str, Any] | None:
        row = self._fetchone(
            "SELECT * FROM document_cache WHERE content_hash = ?", (content_hash,)
        )
        return self.row_to_dict(row)

    def set_document(
        self,
        *,
        content_hash: str,
        docx_path: str,
        pdf_path: str,
        session_id: str | None = None,
    ) -> None:
        self._execute(
            """INSERT INTO document_cache
               (content_hash, docx_path, pdf_path, session_id)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(content_hash) DO UPDATE SET
                   docx_path  = excluded.docx_path,
                   pdf_path   = excluded.pdf_path,
                   session_id = COALESCE(excluded.session_id, document_cache.session_id)""",
            (content_hash, docx_path, pdf_path, session_id),
        )

    # ----- TTL maintenance --------------------------------------------

    def invalidate_llm_older_than(self, days: int) -> int:
        cur = self._execute(
            "DELETE FROM llm_cache WHERE created_at < datetime('now', ?)",
            (f"-{int(days)} days",),
        )
        return cur.rowcount if cur else 0

    def invalidate_images_older_than(self, days: int) -> int:
        cur = self._execute(
            "DELETE FROM image_cache WHERE created_at < datetime('now', ?)",
            (f"-{int(days)} days",),
        )
        return cur.rowcount if cur else 0

    def invalidate_documents_older_than(self, days: int) -> int:
        cur = self._execute(
            "DELETE FROM document_cache WHERE created_at < datetime('now', ?)",
            (f"-{int(days)} days",),
        )
        return cur.rowcount if cur else 0


__all__ = ["CacheRepository"]
