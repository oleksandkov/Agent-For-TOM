"""Alembic environment for TOM.

The URL defined in ``alembic.ini`` is intentionally empty — TOM opens the
encrypted SQLCipher connection via :func:`backend.tom.db.engine.make_engine`,
which keys off the OS keyring and the data dir. Tests can isolate state by
setting ``TOM_DATA_DIR`` *before* invoking Alembic.
"""

from __future__ import annotations

import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool

from backend.tom.db.engine import make_engine
from backend.tom.db.models import Base

config = context.config

# Skip Alembic's fileConfig when running under pytest so it does not
# disable loggers (fileConfig sets disable_existing_loggers=True, which
# silently drops our module-level INFO lines during init_db).
if config.config_file_name is not None and "pytest" not in sys.modules:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL without a live connection (limited use; SQLCipher needs online)."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against the live SQLCipher engine."""
    engine = make_engine(poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
