"""Tests for :mod:`backend.tom.instructions.loader`."""

from __future__ import annotations

from backend.tom.instructions.loader import load_base_prompt, render_prompt


def test_load_base_prompt_has_behaviour_and_policy_sections() -> None:
    text = load_base_prompt()
    assert "TOM" in text
    assert "## Behaviour" in text
    assert "## Memory policy" in text
    assert "## Tool policy" in text
    assert "## Refusal rules" in text


def test_render_with_no_override_marks_missing() -> None:
    text = render_prompt(user_override=None)
    assert "## User-supplied override" in text
    assert "(no user override set)" in text


def test_render_with_override_substitutes() -> None:
    text = render_prompt(user_override="Always reply in Ukrainian.")
    assert "Always reply in Ukrainian." in text
    assert "(no user override set)" not in text


def test_render_with_whitespace_override_falls_back() -> None:
    text = render_prompt(user_override="   \n   ")
    assert "(no user override set)" in text
