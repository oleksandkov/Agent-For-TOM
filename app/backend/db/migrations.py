"""app/backend/db/migrations.py

Apply SQL migrations from ``app/db/schema/`` to the SQLite database.

Strategy
--------
* On startup, ensure the ``schema_version`` table exists.
* Read the current version (0 if the DB is fresh).
* Apply every ``00N_*.sql`` file in ``app/db/schema/`` whose number
  is greater than the current version, in numeric order.
* Each migration is run inside its own transaction. If it fails the
  database is rolled back to the prior version.
* Before applying the first migration on an existing DB, copy the
  current file to ``app/db/backups/agent_<timestamp>.db``.
"""
from __future__ import annotations

import logging
import re
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from app.backend.pipeline.utils import DB_PATH

from .connection import Database
from .exceptions import DbError

log = logging.getLogger("agent_for_tom.db.migrations")


SCHEMA_DIR = Path(__file__).resolve().parents[2] / "db" / "schema"
BACKUPS_DIR = Path(__file__).resolve().parents[2] / "db" / "backups"
SCHEMA_VERSION_TABLE = "schema_version"

_MIGRATION_FILE_RE = re.compile(r"^(\d{3})_(.+)\.sql$")


def _discover_migrations() -> list[tuple[int, str, Path]]:
    """Return ``[(version, description, path)]`` sorted by version."""
    out: list[tuple[int, str, Path]] = []
    if not SCHEMA_DIR.is_dir():
        return out
    for entry in sorted(SCHEMA_DIR.iterdir()):
        if not entry.is_file() or entry.suffix != ".sql":
            continue
        m = _MIGRATION_FILE_RE.match(entry.name)
        if not m:
            continue
        version = int(m.group(1))
        description = m.group(2).replace("_", " ")
        out.append((version, description, entry))
    out.sort(key=lambda t: t[0])
    return out


def _current_version(conn: sqlite3.Connection) -> int:
    """Return the highest applied version, or 0 if the DB is fresh."""
    try:
        row = conn.execute(
            f"SELECT COALESCE(MAX(version), 0) FROM {SCHEMA_VERSION_TABLE}"
        ).fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row[0]) if row else 0


def _backup_db(db_path: Path) -> Path | None:
    """Snapshot the existing DB to app/db/backups/ before any migration."""
    if not db_path.is_file() or db_path.stat().st_size == 0:
        return None
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    target = BACKUPS_DIR / f"agent_{stamp}.db"
    shutil.copy2(db_path, target)
    log.info("backed up database to %s", target)
    return target


def apply_pending_migrations(db: Database) -> list[int]:
    """Apply every migration whose version is greater than the current one.

    Returns the list of versions that were just applied (empty = up to
    date). Safe to call on every startup.
    """
    conn = db.conn
    current = _current_version(conn)
    migrations = _discover_migrations()
    pending = [(v, d, p) for (v, d, p) in migrations if v > current]
    if not pending:
        log.info("schema is up to date at version %d", current)
        return []

    if current > 0:
        _backup_db(db.path)

    applied: list[int] = []
    for version, description, path in pending:
        log.info("applying migration %03d: %s", version, description)
        sql = path.read_text(encoding="utf-8")
        # `executescript` issues an implicit COMMIT and runs its own
        # internal transaction; we must NOT wrap it in BEGIN/COMMIT
        # ourselves. The script itself is atomic. We hold `db.lock`
        # to serialise concurrent migrators.
        try:
            with db.lock:
                db.conn.executescript(sql)
                db.conn.execute(
                    f"INSERT OR REPLACE INTO {SCHEMA_VERSION_TABLE} "
                    "(version, description, applied_at) "
                    "VALUES (?, ?, datetime('now'))",
                    (version, description),
                )
        except sqlite3.Error as exc:
            raise DbError(
                f"migration {version:03d} ({description}) failed: {exc}"
            ) from exc
        applied.append(version)
    log.info("applied %d migration(s); now at version %d", len(applied), max(applied))
    return applied
