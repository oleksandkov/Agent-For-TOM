"""Tests for :mod:`backend.tom.db.keyring`."""

from __future__ import annotations

from pathlib import Path

from backend.tom.db import keyring
from backend.tom.db.paths import data_dir, keyring_id_file

SERVICE = keyring.SERVICE_NAME
USERNAME = keyring.KEY_USERNAME


def test_get_or_create_key_first_call_persists(virtual_keyring: object, tmp_path: Path) -> None:
    key_hex = keyring.get_or_create_key()
    assert len(key_hex) == 64
    assert all(c in "0123456789abcdef" for c in key_hex)


def test_get_or_create_key_is_stable(virtual_keyring: object) -> None:
    first = keyring.get_or_create_key()
    second = keyring.get_or_create_key()
    assert first == second


def test_keyring_id_written_once(virtual_keyring: object, tmp_path: Path) -> None:
    keyring.get_or_create_key()
    first_id = keyring.read_keyring_id()
    assert first_id is not None
    keyring.get_or_create_key()
    assert keyring.read_keyring_id() == first_id


def test_key_hex_never_written_to_disk(virtual_keyring: object, tmp_path: Path) -> None:
    key_hex = keyring.get_or_create_key()
    current = keyring.read_keyring_id() or "x"
    keyring.write_keyring_id(current)
    for path in data_dir().rglob("*"):
        if path.is_file():
            content = path.read_bytes()
            assert key_hex.encode() not in content, f"leaked hex in {path}"


def test_ensure_keyring_id_is_idempotent(virtual_keyring: object) -> None:
    a = keyring.ensure_keyring_id()
    b = keyring.ensure_keyring_id()
    assert a == b
    assert a


def test_forget_key_clears_slot(virtual_keyring: object) -> None:
    keyring.get_or_create_key()
    first = keyring.get_or_create_key()
    keyring.forget_key()
    new = keyring.get_or_create_key()
    assert new != first
    assert len(new) == 64


def test_keyring_id_file_path_uses_data_dir(tmp_path: Path) -> None:
    assert keyring_id_file() == tmp_path.resolve() / "keyring.id"
