"""app/backend/db/repositories/settings.py

Key-value settings. Replaces ``app/config/user_preferences.json``
and gives us a single place to read/write typed settings.

All values are stored as JSON-serialised TEXT.
"""
from __future__ import annotations

import json
from typing import Any

from .base import BaseRepository


class AppSettingsRepository(BaseRepository):
    """Key-value settings store."""

    def get(self, key: str, default: Any = None) -> Any:
        row = self._fetchone("SELECT value_json FROM app_settings WHERE key = ?", (key,))
        if row is None:
            return default
        try:
            return json.loads(row["value_json"])
        except (TypeError, ValueError):
            return default

    def set(self, key: str, value: Any) -> None:
        payload = json.dumps(value, ensure_ascii=False, default=str)
        self._execute(
            """INSERT INTO app_settings (key, value_json, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET
                   value_json = excluded.value_json,
                   updated_at = excluded.updated_at""",
            (key, payload, self.now()),
        )

    def delete(self, key: str) -> None:
        self._execute("DELETE FROM app_settings WHERE key = ?", (key,))

    def list_all(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for row in self._fetchall("SELECT key, value_json FROM app_settings"):
            try:
                out[row["key"]] = json.loads(row["value_json"])
            except (TypeError, ValueError):
                out[row["key"]] = None
        return out


__all__ = ["AppSettingsRepository"]
