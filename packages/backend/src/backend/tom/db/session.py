"""SQLAlchemy session factory.

The sessionmaker is intentionally lazy: the engine is fetched on first
use, and tests that override ``TOM_DATA_DIR`` and reset the default
engine get a fresh binding automatically. Each call to
:func:`SessionLocal` returns a new :class:`Session` instance that the
caller is responsible for closing (use ``with`` for that).
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session, sessionmaker

from backend.tom.db.engine import get_engine


def SessionLocal(*, bind: Engine | Connection | None = None) -> Session:  # noqa: N802
    """Open a new :class:`Session`. The caller closes it (use ``with``).

    Named ``SessionLocal`` to match the SQLAlchemy idiom called for by
    backend-imp.md Section 3; ruff's N802 is suppressed.
    """
    factory: sessionmaker[Session] = sessionmaker(
        bind=bind if bind is not None else get_engine(),
        autoflush=False,
        expire_on_commit=False,
    )
    return factory()


def get_session() -> Iterator[Session]:
    """Yield a session and ensure it is closed afterwards.

    Useful in ``with get_session() as session:`` blocks driven by scripts
    and FastAPI dependencies.
    """
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


__all__: list[str] = ["SessionLocal", "get_session"]
