"""app/backend/db/repositories/secrets.py

Encrypted key-value store. The encryption is transparent to the
caller: ``set(key, plain)`` writes an encrypted BLOB, ``get(key)``
returns the plaintext.

The Fernet key is loaded from ``app/config/.db_key`` by
``app.backend.db.crypto``; if that file is missing the user must
re-enter every secret.
"""
from __future__ import annotations

from typing import Any

from ..crypto import decrypt, encrypt, load_or_create_key
from .base import BaseRepository


class SecretsRepository(BaseRepository):
    """Encrypted key-value store backed by the ``secrets`` table."""

    def _key(self):
        # Lazy: the key file may not exist on first call (it is
        # generated the first time anyone asks to encrypt).
        return load_or_create_key()

    def get(self, key: str, default: Any = None) -> Any | None:
        row = self._fetchone(
            "SELECT value_encrypted FROM secrets WHERE key = ?", (key,)
        )
        if row is None:
            return default
        try:
            return decrypt(bytes(row["value_encrypted"]), self._key())
        except Exception:
            return default

    def set(self, key: str, plaintext: str) -> None:
        blob = encrypt(plaintext, self._key())
        self._execute(
            """INSERT INTO secrets (key, value_encrypted, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET
                   value_encrypted = excluded.value_encrypted,
                   updated_at = excluded.updated_at""",
            (key, blob, self.now()),
        )

    def has(self, key: str) -> bool:
        return self._fetchone(
            "SELECT 1 FROM secrets WHERE key = ?", (key,)
        ) is not None

    def delete(self, key: str) -> None:
        self._execute("DELETE FROM secrets WHERE key = ?", (key,))

    # ---- convenience for the HF token ---------------------------------

    def get_hf_token(self) -> str | None:
        value = self.get("hf.token")
        if not value:
            return None
        return str(value).strip() or None

    def set_hf_token(self, token: str) -> None:
        if not token or not token.strip():
            raise ValueError("HF token is empty")
        self.set("hf.token", token.strip())


__all__ = ["SecretsRepository"]
