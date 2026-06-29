"""Rough token estimator.

v0.1 uses ``len(text) // 4`` — close enough for ``total_tokens``
tracking. Replace with a real tokenizer in §10/§11 once the chosen
embedding model is finalised.
"""

from __future__ import annotations

from collections.abc import Iterable

CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """Estimate token count for ``text``."""
    if not text:
        return 0
    return max(1, len(text) // CHARS_PER_TOKEN)


def estimate_messages(messages: Iterable[tuple[str, str]]) -> int:
    """Sum estimates over an iterable of ``(role, content)``."""
    return sum(estimate_tokens(content) for _role, content in messages)


__all__: list[str] = ["CHARS_PER_TOKEN", "estimate_messages", "estimate_tokens"]
