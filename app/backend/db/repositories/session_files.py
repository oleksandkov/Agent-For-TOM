"""app/backend/db/repositories/session_files.py

Join table between ``sessions`` and ``library_file``.

This table records which files were used by which session, whether
they were summarised, and the token count actually sent to the LLM
(after compaction).
"""
from __future__ import annotations

from typing import Any

from .base import BaseRepository


class SessionFilesRepository(BaseRepository):
    """Join between sessions and library_file."""

    def attach(
        self,
        *,
        session_id: str,
        file_id: str,
        was_summarized: bool = False,
        token_count_used: int = 0,
    ) -> None:
        self._execute(
            """INSERT INTO session_files
               (session_id, file_id, was_summarized, token_count_used)
               VALUES (?, ?, ?, ?)""",
            (session_id, file_id, 1 if was_summarized else 0, token_count_used),
        )

    def list_for_session(self, session_id: str) -> list[dict[str, Any]]:
        return self.rows_to_dicts(
            self._fetchall(
                """SELECT sf.*, lf.original_name, lf.file_hash
                   FROM session_files sf
                   JOIN library_file lf ON lf.id = sf.file_id
                   WHERE sf.session_id = ?
                   ORDER BY sf.id""",
                (session_id,),
            )
        )

    def count_for_session(self, session_id: str) -> int:
        row = self._fetchone(
            "SELECT COUNT(*) AS n FROM session_files WHERE session_id = ?",
            (session_id,),
        )
        return int(row["n"]) if row else 0


__all__ = ["SessionFilesRepository"]
