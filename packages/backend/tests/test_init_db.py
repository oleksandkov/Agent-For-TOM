"""End-to-end tests for :func:`backend.tom.db.init_db.init_db`."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import inspect, text

from backend.tom.db import db_file, init_db
from backend.tom.db.engine import make_engine


def test_init_db_creates_db_file(virtual_keyring: object, tmp_path: Path) -> None:
    assert not db_file().exists()
    init_db()
    assert db_file().exists()


def test_init_db_creates_expected_tables(virtual_keyring: object, tmp_path: Path) -> None:
    init_db()
    engine = make_engine()
    insp = inspect(engine)
    expected = {
        "sessions",
        "messages",
        "memory_records",
        "skills",
        "patterns",
        "provider_configs",
        "audit_log",
        "memory_records_vec",
        "alembic_version",
    }
    assert expected.issubset(set(insp.get_table_names()))


def test_init_db_is_idempotent(virtual_keyring: object, tmp_path: Path) -> None:
    init_db()
    init_db()
    engine = make_engine()
    insp = inspect(engine)
    assert "alembic_version" in insp.get_table_names()


def test_init_db_returns_db_file_path(virtual_keyring: object, tmp_path: Path) -> None:
    path = init_db()
    assert path == db_file()
    assert path.exists()


def test_init_db_creates_vec0_table(virtual_keyring: object, tmp_path: Path) -> None:
    init_db()
    engine = make_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'memory%'")
        ).all()
    names = {row[0] for row in rows}
    assert "memory_records_vec" in names


def test_init_db_emits_info_log(virtual_keyring: object, tmp_path: Path) -> None:
    """Smoke check: init_db logs a single INFO line summarising the run.

    Alembic's env.py reconfigures the root logging, which evicts
    pytest's caplog handler. Attach a handler directly to our module
    logger to assert deterministically.
    """
    import logging

    captured: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record.getMessage())

    target = logging.getLogger("backend.tom.db.init_db")
    handler = _Capture()
    target.addHandler(handler)
    target.setLevel(logging.INFO)
    try:
        init_db()
    finally:
        target.removeHandler(handler)
    assert any("TOM db initialised" in msg for msg in captured)


def test_db_init_cli_runs_init(tmp_path: Path, virtual_keyring: object, capsys: object) -> None:
    from backend.tom.__main__ import main

    rc = main(["db", "init"])
    assert rc == 0
    out = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "TOM db initialised" in out
    assert db_file().exists()


def test_db_init_cli_show_path(tmp_path: Path, virtual_keyring: object, capsys: object) -> None:
    from backend.tom.__main__ import main

    rc = main(["db", "init", "--show-path"])
    assert rc == 0
    out = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "data_dir=" in out
