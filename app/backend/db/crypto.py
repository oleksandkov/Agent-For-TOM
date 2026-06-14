"""app/backend/db/crypto.py

Fernet wrapper for encrypting secrets in the `secrets` table.

Key handling
------------
The 32-byte key is generated with ``os.urandom(32)`` and URL-safe
base64-encoded on first run, then written to
``app/config/.db_key``. The file is created with mode 0600 on POSIX.
On Windows, ACLs are not adjusted — Windows already restricts access
to the user's profile directory by default. If the key file is lost
or deleted, every encrypted secret becomes unreadable and the user
must re-enter them.

Algorithm
---------
We use Fernet from the ``cryptography`` package, which provides
AES-128-CBC + HMAC-SHA256 authenticated encryption. We pin the
version byte to ``b"v01"`` so that future migrations can read older
payloads with the right key derivation.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken  # type: ignore

from .exceptions import SecretKeyMissingError


_KEY_MAGIC = b"AFT1"  # written as the first 4 bytes of every key file


def _key_file_path() -> Path:
    from app.backend.pipeline.utils import APP_DIR
    return APP_DIR / "config" / ".db_key"


def load_or_create_key() -> bytes:
    """Return the Fernet key (generating it on first use)."""
    path = _key_file_path()
    if path.is_file():
        raw = path.read_bytes()
        if raw.startswith(_KEY_MAGIC):
            return raw[len(_KEY_MAGIC):].rstrip(b"\n")
        # Legacy / corrupted key file: refuse to overwrite silently.
        raise SecretKeyMissingError(
            f"key file at {path} has an invalid magic header; refusing to overwrite. "
            "Delete it manually if you want to rotate the key (every secret will be lost)."
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    try:
        path.write_bytes(_KEY_MAGIC + key + b"\n")
        os.chmod(path, 0o600)
    except OSError:
        # On Windows chmod may not enforce POSIX semantics; that's fine.
        pass
    return key


def _fernet(key: Optional[bytes] = None) -> Fernet:
    if key is None:
        key = load_or_create_key()
    return Fernet(key)


def encrypt(plaintext: str, key: Optional[bytes] = None) -> bytes:
    """Encrypt a UTF-8 string, returning a Fernet token as bytes."""
    if plaintext is None:
        plaintext = ""
    f = _fernet(key)
    return f.encrypt(plaintext.encode("utf-8"))


def decrypt(ciphertext: bytes, key: Optional[bytes] = None) -> str:
    """Decrypt a Fernet token back to a UTF-8 string.

    Raises SecretKeyMissingError if the key file is gone (i.e. the
    encrypted blob is signed with a key we cannot recover).
    """
    if not ciphertext:
        return ""
    f = _fernet(key)
    try:
        return f.decrypt(ciphertext).decode("utf-8")
    except InvalidToken as exc:
        raise SecretKeyMissingError(
            "Failed to decrypt secret — the .db_key file is missing or wrong. "
            "Re-enter the secret via Settings."
        ) from exc
