"""TOM backend test fixtures.

Two autouse / opt-in fixtures make every test hermetic:

- :func:`virtual_keyring` — an in-memory keyring backend so the OS
  keyring is never touched during tests.
- :func:`tmp_data_dir` — every test gets a unique ``TOM_DATA_DIR``
  pointing at a ``tmp_path`` so data files do not leak between runs.
- :func:`_reset_default_engine` — guarantees the cached engine is
  rebuilt whenever ``TOM_DATA_DIR`` changes during a test.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import keyring
import keyring.backend
import pytest


class _InMemoryKeyring(keyring.backend.KeyringBackend):
    """In-process replacement for the OS keyring."""

    priority = 99

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.store.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.store[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self.store.pop((service, username), None)


@pytest.fixture
def virtual_keyring() -> _InMemoryKeyring:
    """Install an in-memory keyring backend for the duration of the test."""
    backend = _InMemoryKeyring()
    prev = keyring.get_keyring()
    keyring.set_keyring(backend)
    try:
        yield backend
    finally:
        keyring.set_keyring(prev)


@pytest.fixture
def tmp_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``TOM_DATA_DIR`` at a tmp directory for this test."""
    monkeypatch.setenv("TOM_DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def _reset_default_engine(tmp_data_dir: Path) -> Iterator[None]:
    """Reset the cached engine so each test picks up its tmp TOM_DATA_DIR."""
    from backend.tom.db.engine import reset_default_engine

    reset_default_engine()
    yield
    reset_default_engine()
