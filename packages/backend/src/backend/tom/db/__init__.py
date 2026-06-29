"""TOM data storage layer.

SQLite + SQLCipher + sqlite-vec, keyed by the OS keyring. Implements
backend-imp.md Section 3.

The public surface intentionally exposes the submodule names
:mod:`engine` and :mod:`session` so callers can write the
plan-prescribed::

    from backend.tom.db import engine, session, init_db

Use :func:`backend.tom.db.engine.make_engine` to construct a
SQLAlchemy engine, :data:`backend.tom.db.session.SessionLocal` to get a
sessionmaker, and :func:`init_db` to run migrations to head.
"""

from __future__ import annotations

from backend.tom.db import engine as engine
from backend.tom.db import session as session
from backend.tom.db.init_db import init_db
from backend.tom.db.paths import (
    core_memory_file,
    data_dir,
    db_file,
    ensure_dirs,
    keyring_id_file,
    logs_dir,
    skills_builtin_dir,
    skills_generated_dir,
    uploads_dir,
)

__all__: list[str] = [
    "core_memory_file",
    "data_dir",
    "db_file",
    "engine",
    "ensure_dirs",
    "init_db",
    "keyring_id_file",
    "logs_dir",
    "session",
    "skills_builtin_dir",
    "skills_generated_dir",
    "uploads_dir",
]
