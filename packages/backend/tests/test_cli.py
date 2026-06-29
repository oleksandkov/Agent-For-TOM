"""TOM CLI smoke tests."""

from __future__ import annotations

from backend.tom.__main__ import main


def test_cli_version_prints_product_and_version(capsys: object) -> None:
    rc = main(["version"])
    assert rc == 0
    out = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "TOM" in out
    assert "0.1.0" in out


def test_cli_serve_rejects_non_loopback_host() -> None:
    rc = main(["serve", "--host", "0.0.0.0", "--port", "7878"])
    assert rc == 2


def test_cli_unknown_command_returns_error() -> None:
    rc = main(["nope"])
    assert rc == 1
