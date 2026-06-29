"""SQLAlchemy engine factory bound to the SQLCipher cipher creator.

The engine is *not* created on import. Call :func:`make_engine` (or
:func:`get_engine` for the lazy cached default) explicitly — this keeps
importing :mod:`backend.tom.db` cheap and avoids touching the keyring
during tooling that doesn't need the DB.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import Pool, create_engine, event
from sqlalchemy.engine import Engine

from backend.tom.db.cipher import make_cipher_creator
from backend.tom.db.keyring import get_or_create_key
from backend.tom.db.paths import db_file

if TYPE_CHECKING:
    pass

_DEFAULT_KEY_PRAGMAS: tuple[str, ...] = (
    "PRAGMA foreign_keys = ON",
    "PRAGMA journal_mode = WAL",
    "PRAGMA synchronous = NORMAL",
    "PRAGMA busy_timeout = 5000",
    "PRAGMA temp_store = MEMORY",
)


def _attach_pragmas(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn: object, _: object) -> None:
        conn = dbapi_conn
        for pragma in _DEFAULT_KEY_PRAGMAS:
            conn.execute(pragma)  # type: ignore[attr-defined]


def make_engine(
    *,
    path: Path | None = None,
    key_hex: str | None = None,
    enable_vec: bool = True,
    poolclass: type[Pool] | None = None,
) -> Engine:
    """Build a SQLAlchemy engine backed by SQLCipher.

    Defaults to ``data_dir()/tom.db`` and the key fetched from the OS
    keyring. Both can be overridden explicitly for tests.
    """
    db_path = (path or db_file()).expanduser().resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    key = key_hex or get_or_create_key()
    creator = make_cipher_creator(db_path, key, enable_vec=enable_vec)
    engine = create_engine(
        "sqlite:///",
        creator=creator,
        poolclass=poolclass,
        future=True,
    )
    _attach_pragmas(engine)
    return engine


_default_engine: Engine | None = None


def get_engine() -> Engine:
    """Return a process-cached default engine, building it on first use."""
    global _default_engine
    if _default_engine is None:
        _default_engine = make_engine()
    return _default_engine


def reset_default_engine() -> None:
    """Drop the cached engine. Test-only — leaks connections otherwise."""
    global _default_engine
    if _default_engine is not None:
        _default_engine.dispose()
    _default_engine = None


__all__: list[str] = [
    "get_engine",
    "make_engine",
    "reset_default_engine",
]
