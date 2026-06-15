"""app/backend/db/repositories/user_style.py

Versioned user style. Same version/active semantics as
``InstructionRepository`` but with the additional ``is_empty`` flag
used by the pipeline to skip injection when no style is set.
"""
from __future__ import annotations

import hashlib
import uuid
from typing import Any

from .base import BaseRepository


def _new_id() -> str:
    return str(uuid.uuid4())


def _content_hash(content: str) -> str:
    return hashlib.sha256((content or "").encode("utf-8")).hexdigest()


class UserStyleRepository(BaseRepository):
    """Versioned user style file."""

    def get(self, style_id: str) -> dict[str, Any] | None:
        return self.row_to_dict(
            self._fetchone("SELECT * FROM user_style WHERE id = ?", (style_id,))
        )

    def get_active(self) -> dict[str, Any] | None:
        return self.row_to_dict(
            self._fetchone("SELECT * FROM user_style WHERE is_active = 1")
        )

    def get_active_or_empty(self) -> dict[str, Any]:
        """Convenience: returns the active row, or a synthetic empty one
        with ``is_empty=1`` so the caller can branch uniformly.
        """
        active = self.get_active()
        if active is not None:
            return active
        return {"id": None, "is_empty": 1, "content": ""}

    def save_new_version(
        self,
        content: str,
        *,
        content_path: str | None = None,
    ) -> dict[str, Any]:
        """Save `content` as the new active version.

        If `content` is empty (or only whitespace), ``is_empty`` is set
        to 1 — the pipeline will skip injecting it.
        """
        is_empty = 1 if not (content and content.strip()) else 0
        # Deactivate the current active row (single-active per partial idx).
        self._execute("UPDATE user_style SET is_active = 0 WHERE is_active = 1")
        # Determine next version.
        last = self._fetchone(
            "SELECT MAX(version) AS v FROM user_style"
        )
        next_version = 1 if (last is None or last["v"] is None) else int(last["v"]) + 1
        new_id = _new_id()
        now = self.now()
        self._execute(
            """INSERT INTO user_style
               (id, content_hash, content_path, content,
                is_empty, is_active, version, created_at)
               VALUES (?, ?, ?, ?, ?, 1, ?, ?)""",
            (
                new_id, _content_hash(content), content_path, content,
                is_empty, next_version, now,
            ),
        )
        return self.get(new_id) or {"id": new_id, "is_empty": is_empty}

    def deactivate(self, style_id: str) -> None:
        self._execute("UPDATE user_style SET is_active = 0 WHERE id = ?", (style_id,))


__all__ = ["UserStyleRepository"]
