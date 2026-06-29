"""Initialize the encrypted TOM database.

Runs all Alembic migrations to head and ensures the data dir layout
exists. Idempotent: calling twice is a no-op (Alembic skips already
applied revisions).

Used by:
- :func:`backend.tom.__main__.main` for the ``db init`` subcommand
- FastAPI startup hook (Section 6 wiring)
- The first run of the desktop app (colleague side, deferred)
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig

from backend.tom.db.engine import get_engine, reset_default_engine
from backend.tom.db.paths import data_dir, db_file, ensure_dirs

logger = logging.getLogger(__name__)

_BACKEND_ROOT = Path(__file__).resolve().parents[4]
ALEMBIC_INI_PATH = str(_BACKEND_ROOT / "alembic.ini")
SCRIPT_LOCATION = str(_BACKEND_ROOT / "src" / "backend" / "tom" / "db" / "migrations")


def init_db() -> Path:
    """Run migrations to head. Returns the resolved DB file path.

    Sets up the data dir, then upgrades the schema. Logs timing so
    operators can spot a slow first run.
    """
    start = time.monotonic()
    ensure_dirs()
    db_path = db_file()
    config = _build_alembic_config()
    reset_default_engine()
    alembic_command.upgrade(config, "head")
    # Touch the engine to trigger key-load if it wasn't loaded by env.py.
    get_engine()
    elapsed = time.monotonic() - start
    logger.info(
        "TOM db initialised at %s in %.2fs (data_dir=%s)",
        db_path,
        elapsed,
        data_dir(),
    )
    return db_path


def _build_alembic_config() -> AlembicConfig:
    config = AlembicConfig(ALEMBIC_INI_PATH)
    config.set_main_option("script_location", SCRIPT_LOCATION)
    return config


__all__: list[str] = ["init_db"]
