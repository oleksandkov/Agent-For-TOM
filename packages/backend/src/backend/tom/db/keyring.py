"""OS keyring wrapper for the SQLCipher database key.

The hex DB key never touches disk. ``keyring.id`` (UUID) is the only
identifier written into the data dir, used to detect that the local
keyring slot is still the one this install expects (helps with migrations
and with refusing to use a DB encrypted by a different install).

Functions:
- :func:`get_or_create_key` returns the 64-char hex key from the OS keyring,
  creating one on first run.
- :func:`read_keyring_id` reads the tracker UUID from disk.
- :func:`write_keyring_id` writes the tracker UUID to disk.
- :func:`ensure_keyring_id` writes a fresh UUID IFF no file exists yet.
"""

from __future__ import annotations

import uuid
from contextlib import suppress
from pathlib import Path

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

from backend.tom.db.paths import data_dir, ensure_dirs, keyring_id_file

SERVICE_NAME = "tom"
KEY_USERNAME = "db"
_KEY_BYTES = 32


def _read_keyring() -> str | None:
    try:
        return keyring.get_password(SERVICE_NAME, KEY_USERNAME)
    except KeyringError:
        return None


def _write_keyring(key_hex: str) -> None:
    keyring.set_password(SERVICE_NAME, KEY_USERNAME, key_hex)


def _delete_keyring() -> None:
    with suppress(PasswordDeleteError):
        keyring.delete_password(SERVICE_NAME, KEY_USERNAME)


def get_or_create_key() -> str:
    """Return the hex DB key from the OS keyring, generating one if missing.

    Idempotent: repeated calls return the same key for the lifetime of the
    OS-keyring slot. The returned value is the 64-character hex form
    suitable for SQLCipher's ``PRAGMA key = "x'..'"`` form.
    """
    existing = _read_keyring()
    if existing is not None and len(existing) == _KEY_BYTES * 2:
        return existing
    import secrets

    key_hex = secrets.token_hex(_KEY_BYTES)
    _write_keyring(key_hex)
    ensure_keyring_id()
    return key_hex


def read_keyring_id() -> str | None:
    """Return the tracker UUID from disk, or None if absent / unreadable."""
    path = keyring_id_file()
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def write_keyring_id(value: str) -> None:
    """Persist the tracker UUID to disk. Caller validates format."""
    ensure_dirs()
    keyring_id_file().write_text(value, encoding="utf-8")


def ensure_keyring_id() -> str:
    """Write a fresh UUID to ``keyring.id`` if none exists; return current value."""
    current = read_keyring_id()
    if current:
        return current
    new_id = str(uuid.uuid4())
    write_keyring_id(new_id)
    return new_id


def forget_key() -> None:
    """Remove the key from the OS keyring. Used by tests only.

    Production code must never call this — losing the key bricks the DB.
    """
    _delete_keyring()


def key_path_for_tests() -> Path:
    """Test-only helper: a synthetic path never used to read the key itself.

    Provided so a test can verify the live key is never written to disk in
    the data dir. The path itself is just a sentinel — the value at it is
    not meaningful.
    """
    return data_dir() / "keyring.id"


__all__: list[str] = [
    "KEY_USERNAME",
    "SERVICE_NAME",
    "ensure_keyring_id",
    "forget_key",
    "get_or_create_key",
    "read_keyring_id",
    "write_keyring_id",
]
