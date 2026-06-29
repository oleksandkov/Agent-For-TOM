"""Tests for :mod:`backend.tom.chat.tokens`."""

from __future__ import annotations

from backend.tom.chat.tokens import estimate_messages, estimate_tokens


def test_estimate_zero() -> None:
    assert estimate_tokens("") == 0


def test_estimate_simple() -> None:
    assert estimate_tokens("abcd") == 1


def test_estimate_long_text() -> None:
    text = "x" * 401
    # 401 / 4 = 100 (integer division)
    assert estimate_tokens(text) == 100


def test_estimate_messages_sums_content() -> None:
    pairs = [
        ("user", "abcd"),
        ("assistant", "x" * 12),
    ]
    assert estimate_messages(pairs) == 1 + 3  # 4/4 + 12/4


def test_estimate_messages_empty() -> None:
    assert estimate_messages([]) == 0
