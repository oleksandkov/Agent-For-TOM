"""app/backend/db/connection.py

SQLite connection wrapper with WAL mode, foreign keys ON, and a
single-connection + threading.Lock for the desktop app.

Usage:
    from app.backend.db.connection import Database

    db = Database()                # opens app/db/agent.db, runs migrations
    with db.transaction() as conn:
        conn.execute(...)
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.backend.pipeline.utils import DB_PATH, ensure_dir


# We retry "database is locked" this many times with exponential backoff.
_BUSY_RETRIES = 5
_BUSY_BASE_DELAY_SEC = 0.05


class Database:
    """Thin, single-connection SQLite wrapper suitable for a desktop app.

    Design notes
    ------------
    * **WAL** journal mode lets readers run concurrently with a single
      writer — perfect for our UI thread reading `sessions` while the
      pipeline thread writes to `pipeline_runs`.
    * **check_same_thread=False** + a single ``threading.Lock`` is the
      pragmatic choice for a desktop app. A real connection pool would
      add complexity without measurable benefit at this scale.
    * Transactions use ``BEGIN IMMEDIATE`` (write lock) to avoid
      SQLITE_BUSY deadlocks when reading-and-writing the same row.
    """

    def __init__(self, db_path: Path | str | None = None) -> None:
        self._path = Path(db_path) if db_path else DB_PATH
        ensure_dir(self._path.parent)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            str(self._path),
            check_same_thread=False,
            isolation_level=None,  # we control BEGIN / COMMIT explicitly
            timeout=5.0,
        )
        self._conn.row_factory = sqlite3.Row
        # Pragmas. We set them on every new connection.
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")
        # Apply any pending migrations (idempotent).
        from .migrations import apply_pending_migrations
        apply_pending_migrations(self)
        # Seed initial data (templates, global instruction, empty user_style).
        from .seed import seed_initial_data
        seed_initial_data(self)

    @property
    def path(self) -> Path:
        return self._path

    @property
    def conn(self) -> sqlite3.Connection:
        """Raw connection. Use sparingly — prefer ``transaction()``."""
        return self._conn

    @property
    def lock(self) -> threading.Lock:
        """Internal lock. Exposed for the migration runner; do not use
        elsewhere — prefer ``transaction()``.
        """
        return self._lock

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Context manager that wraps a block in a serialised transaction.

        Acquires the lock, opens a ``BEGIN IMMEDIATE`` transaction,
        commits on success, rolls back on any exception. Re-raises the
        exception to the caller.
        """
        with self._lock:
            self._begin_with_retry()
            try:
                yield self._conn
                self._conn.execute("COMMIT")
            except Exception:
                try:
                    self._conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise

    def _begin_with_retry(self) -> None:
        """``BEGIN IMMEDIATE`` with exponential backoff for SQLITE_BUSY."""
        delay = _BUSY_BASE_DELAY_SEC
        last_exc: sqlite3.Error | None = None
        for attempt in range(_BUSY_RETRIES):
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                return
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                    raise
                last_exc = exc
                import time
                time.sleep(delay)
                delay *= 2
        raise sqlite3.OperationalError(
            f"database is locked after {_BUSY_RETRIES} retries"
        ) from last_exc

    def execute_script(self, sql: str) -> None:
        """Run a multi-statement script with autocommit semantics.

        Used by migrations. Each migration is a single transaction
        externally — we do not wrap individual CREATE TABLE statements.
        """
        with self._lock:
            self._conn.executescript(sql)

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.commit()
            except sqlite3.Error:
                pass
            self._conn.close()
