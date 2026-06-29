"""SQLCipher connection factory and per-connection setup.

This module owns the *connection*, not the :class:`sqlalchemy.Engine`. It
exposes a creator callable that the engine uses via SQLAlchemy's
``creator`` argument. Each new connection:

1. Opens a ``sqlcipher3`` DB-API connection to the path.
2. Issues ``PRAGMA key = "x'<hex>'"`` to load the raw hex key.
3. Loads the ``vec0`` extension from ``sqlite-vec`` via ``loadable_path``.

Why not merge this into :mod:`engine`? Keeping cipher concerns isolated
means tests can exercise the connection without bringing in the SQLAlchemy
session machinery, and the security boundary is obvious from the import
graph.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import sqlcipher3

if TYPE_CHECKING:
    from sqlite_vec import loadable_path as _loadable_path  # noqa: F401


def _load_vec_extension(conn: sqlcipher3.Connection) -> None:
    """Enable extension loading and load ``vec0`` from ``sqlite-vec``."""
    try:
        conn.enable_load_extension(True)
    except AttributeError as exc:
        msg = "sqlcipher3 build does not support extension loading"
        raise RuntimeError(msg) from exc
    import sqlite_vec

    conn.load_extension(sqlite_vec.loadable_path())


def make_cipher_creator(
    db_path: Path,
    key_hex: str,
    *,
    enable_vec: bool = True,
) -> Callable[[], Any]:
    """Return a zero-arg creator suitable for ``sqlalchemy.create_engine``.

    Each invocation opens a fresh SQLCipher connection, sets the key, and
    (optionally) loads the sqlite-vec extension. The connection is then
    handed to SQLAlchemy which wraps it in its DB-API adapter.
    """
    db_str = str(db_path)
    key_literal = _format_key_pragma(key_hex)

    def creator() -> Any:
        conn = sqlcipher3.connect(db_str)
        conn.execute(key_literal)
        if enable_vec:
            _load_vec_extension(conn)
        return conn

    return creator


def _format_key_pragma(key_hex: str) -> str:
    """Build a SQLCipher ``PRAGMA key`` statement for a raw hex key.

    SQLCipher interprets ``PRAGMA key = "x'..'"`` as a raw key (no KDF).
    The hex must be lowercase and an even number of hex digits.
    """
    if len(key_hex) % 2 != 0:
        msg = "key_hex must have an even number of hex digits"
        raise ValueError(msg)
    try:
        bytes.fromhex(key_hex)
    except ValueError as exc:
        msg = "key_hex must be valid hex"
        raise ValueError(msg) from exc
    escaped = key_hex.replace('"', '""')
    return f"PRAGMA key = \"x'{escaped}'\""


def platform_vec_filename() -> str:
    """Diagnostic helper: the on-disk filename of the vec0 extension."""
    import sqlite_vec

    p = Path(sqlite_vec.loadable_path())
    return p.name + (" [windows]" if sys.platform == "win32" else "")


__all__: list[str] = [
    "_format_key_pragma",
    "make_cipher_creator",
    "platform_vec_filename",
]
