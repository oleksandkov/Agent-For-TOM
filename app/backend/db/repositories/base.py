"""app/backend/db/repositories/base.py

Common parent for all repositories.

Each repository holds a reference to the shared ``Database`` and
delegates the actual SQL execution to it. Repositories are *thin*:
they convert rows to dicts (or domain objects) and provide
high-level methods like ``create``, ``get``, ``list_*``,
``update_status``. They do not contain business logic — that lives
in the orchestrator / bridge.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from ..connection import Database


def _now_iso() -> str:
    """UTC ISO-8601 timestamp with explicit ``+00:00`` offset."""
    return datetime.now(timezone.utc).isoformat()


def _to_jsonb(value: Any) -> str | None:
    """Serialise a Python value to a JSON string for storage."""
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, default=str)


def _from_jsonb(value: str | None) -> Any:
    """Deserialise a JSON column. Returns the original value if it is
    ``None`` or empty.
    """
    if not value:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


class BaseRepository:
    """Thin base for repositories. Owns the Database handle only."""

    def __init__(self, db: Database) -> None:
        self._db = db

    @property
    def db(self) -> Database:
        return self._db

    # --- low-level helpers ------------------------------------------------

    def _fetchone(
        self,
        sql: str,
        params: Sequence[Any] | Mapping[str, Any] = (),
    ) -> sqlite3.Row | None:
        cur = self._db.conn.execute(sql, params)
        return cur.fetchone()

    def _fetchall(
        self,
        sql: str,
        params: Sequence[Any] | Mapping[str, Any] = (),
    ) -> list[sqlite3.Row]:
        cur = self._db.conn.execute(sql, params)
        return list(cur.fetchall())

    def _execute(
        self,
        sql: str,
        params: Sequence[Any] | Mapping[str, Any] = (),
    ) -> sqlite3.Cursor:
        with self._db.transaction() as tx:
            return tx.execute(sql, params)

    def _executemany(
        self,
        sql: str,
        seq_of_params: Iterable[Sequence[Any] | Mapping[str, Any]],
    ) -> sqlite3.Cursor:
        with self._db.transaction() as tx:
            return tx.executemany(sql, seq_of_params)

    # --- conversions ------------------------------------------------------

    @staticmethod
    def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {k: row[k] for k in row.keys()}

    @staticmethod
    def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
        return [BaseRepository.row_to_dict(r) for r in rows]

    @staticmethod
    def jsonify(mapping: dict[str, Any]) -> dict[str, Any]:
        """Replace dict-valued fields with their JSON string representation
        for storage. Inverse of :py:meth:`unjsonify`.
        """
        out: dict[str, Any] = {}
        for k, v in mapping.items():
            if isinstance(v, (dict, list, tuple)):
                out[k] = _to_jsonb(v)
            else:
                out[k] = v
        return out

    @staticmethod
    def unjsonify(mapping: dict[str, Any] | None, json_fields: tuple[str, ...]) -> dict[str, Any] | None:
        """Inverse of :py:meth:`jsonify` for the given field names."""
        if not mapping:
            return mapping
        out = dict(mapping)
        for k in json_fields:
            if k in out:
                out[k] = _from_jsonb(out[k])
        return out

    @staticmethod
    def now() -> str:
        return _now_iso()


__all__ = ["BaseRepository"]
