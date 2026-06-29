"""Tests for :mod:`backend.tom.db.paths`."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.tom.db import paths


def test_data_dir_honours_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOM_DATA_DIR", str(tmp_path))
    assert paths.data_dir() == tmp_path.resolve()


def test_db_file_lives_under_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOM_DATA_DIR", str(tmp_path))
    assert paths.db_file() == tmp_path.resolve() / "tom.db"
    assert paths.keyring_id_file() == tmp_path.resolve() / "keyring.id"


def test_ensure_dirs_creates_expected_layout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TOM_DATA_DIR", str(tmp_path))
    created = paths.ensure_dirs()
    for sub in ("", "uploads", "skills/builtin", "skills/generated", "logs"):
        assert (tmp_path / sub).is_dir() if sub else tmp_path.is_dir()
    assert tmp_path.joinpath("skills", "generated").is_dir()
    assert tmp_path.joinpath("logs").is_dir()
    assert created  # non-empty list


def test_ensure_dirs_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOM_DATA_DIR", str(tmp_path))
    paths.ensure_dirs()
    paths.ensure_dirs()
    assert paths.data_dir().is_dir()
    assert paths.skills_generated_dir().is_dir()


def test_data_dir_uses_default_when_no_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform_default: Path
) -> None:
    monkeypatch.delenv("TOM_DATA_DIR", raising=False)
    monkeypatch.setattr(paths, "_platform_default", lambda: platform_default)
    assert paths.data_dir() == platform_default.resolve()


@pytest.fixture
def platform_default(tmp_path: Path) -> Path:
    return tmp_path / "fake-platform-default"
