"""app/backend/db/repositories/library_file.py

Deduplicated attached file library. The primary key for dedup is
``file_hash`` (SHA-256 of the original file content); an
``UNIQUE(file_hash)`` constraint on the table guarantees a single
row per file.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..exceptions import PathValidationError
from ..path_utils import normalize
from .base import BaseRepository


def _new_id() -> str:
    return str(uuid.uuid4())


def _hash_file(path: Path | str) -> str:
    p = Path(path)
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class LibraryFileRepository(BaseRepository):
    """CRUD for the library_file table with SHA-256 dedup."""

    def get(self, file_id: str) -> dict[str, Any] | None:
        return self.row_to_dict(
            self._fetchone("SELECT * FROM library_file WHERE id = ?", (file_id,))
        )

    def get_by_hash(self, file_hash: str) -> dict[str, Any] | None:
        return self.row_to_dict(
            self._fetchone("SELECT * FROM library_file WHERE file_hash = ?", (file_hash,))
        )

    def list_all(self, limit: int = 200) -> list[dict[str, Any]]:
        return self.rows_to_dicts(
            self._fetchall(
                "SELECT * FROM library_file ORDER BY last_used_at DESC LIMIT ?",
                (limit,),
            )
        )

    def attach(
        self,
        *,
        original_path: Path | str,
        original_name: str,
        original_type: str | None = None,
        stored_path: str,
    ) -> dict[str, Any]:
        """Register a new file. Returns the existing row if the
        SHA-256 hash is already known (dedup).
        """
        # Validate the stored path BEFORE doing any I/O. The path is
        # the value that ends up in the DB; an absolute or
        # parent-traversal path is a programming error.
        norm_stored = normalize(stored_path)
        file_hash = _hash_file(original_path)
        existing = self.get_by_hash(file_hash)
        if existing is not None:
            self.touch(existing["id"])
            return existing
        size = Path(original_path).stat().st_size if Path(original_path).is_file() else 0
        new_id = _new_id()
        now = self.now()
        self._execute(
            """INSERT INTO library_file
               (id, file_hash, original_name, original_type, stored_path,
                conversion_status, file_size_bytes, created_at, last_used_at)
               VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?)""",
            (new_id, file_hash, original_name, original_type, norm_stored, size, now, now),
        )
        return self.get(new_id) or {"id": new_id, "file_hash": file_hash}

    def attach_with_hash(
        self,
        *,
        file_hash: str,
        original_name: str,
        original_type: str | None = None,
        stored_path: str,
        file_size_bytes: int | None = None,
    ) -> dict[str, Any]:
        """Register a file by an externally-computed hash. Used when
        the file has already been hashed elsewhere (e.g. the bridge
        materialising a transit snapshot).
        """
        norm_stored = normalize(stored_path)
        existing = self.get_by_hash(file_hash)
        if existing is not None:
            self.touch(existing["id"])
            return existing
        new_id = _new_id()
        now = self.now()
        self._execute(
            """INSERT INTO library_file
               (id, file_hash, original_name, original_type, stored_path,
                conversion_status, file_size_bytes, created_at, last_used_at)
               VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?)""",
            (new_id, file_hash, original_name, original_type, norm_stored, file_size_bytes, now, now),
        )
        return self.get(new_id) or {"id": new_id, "file_hash": file_hash}

    def mark_converted(
        self,
        file_id: str,
        *,
        converted_text: str,
        token_count: int | None = None,
    ) -> None:
        self._execute(
            """UPDATE library_file
               SET conversion_status = 'done',
                   converted_text    = ?,
                   token_count       = COALESCE(?, token_count),
                   converted_at      = ?,
                   conversion_error  = NULL
               WHERE id = ?""",
            (converted_text, token_count, self.now(), file_id),
        )

    def mark_failed(self, file_id: str, error: str) -> None:
        self._execute(
            """UPDATE library_file
               SET conversion_status = 'failed',
                   conversion_error  = ?
               WHERE id = ?""",
            (error, file_id),
        )

    def touch(self, file_id: str) -> None:
        """Update ``last_used_at`` to mark that the file was just used."""
        self._execute(
            "UPDATE library_file SET last_used_at = ? WHERE id = ?",
            (self.now(), file_id),
        )

    def delete_if_unused(self, file_id: str) -> bool:
        """Delete a file only if no session_files row references it.
        Returns True if a row was deleted.
        """
        row = self._fetchone(
            "SELECT COUNT(*) AS n FROM session_files WHERE file_id = ?",
            (file_id,),
        )
        if row and row["n"] > 0:
            return False
        self._execute("DELETE FROM library_file WHERE id = ?", (file_id,))
        return True


__all__ = ["LibraryFileRepository"]
