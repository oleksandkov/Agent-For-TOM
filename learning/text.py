"""
Text primitives for the learning system.

Deliberately owned by this package rather than imported from self_improve.py:
the keyword taxonomy in that module is being deleted, and retrieval must not
go down with it. Same behaviour, no dependency.
"""

from __future__ import annotations

import re

STOP_WORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "dare", "ought",
    "used", "to", "of", "in", "for", "on", "with", "at", "by", "from",
    "as", "into", "through", "during", "before", "after", "above", "below",
    "between", "out", "off", "over", "under", "again", "further", "then",
    "once", "here", "there", "when", "where", "why", "how", "all", "each",
    "every", "both", "few", "more", "most", "other", "some", "such", "no",
    "nor", "not", "only", "own", "same", "so", "than", "too", "very",
    "just", "because", "but", "and", "or", "if", "while", "that", "this",
    "these", "those", "it", "its", "what", "which", "who", "whom",
    "about", "up", "down", "i", "you", "my", "your", "me",
}


def extract_keywords(text: str, max_keywords: int = 12) -> list[str]:
    """Frequency-ranked keywords, stop words and code blocks removed."""
    text = (text or "").lower()
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    text = re.sub(r"https?://\S+", "", text)
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9_\-']{1,}", text)
    words = [w for w in words if w not in STOP_WORDS and len(w) > 2]
    freq: dict[str, int] = {}
    for w in words:
        freq[w] = freq.get(w, 0) + 1
    return [w for w, _ in sorted(freq.items(), key=lambda x: -x[1])[:max_keywords]]


def similarity(a: str, b: str) -> float:
    """Jaccard overlap of the two texts' keyword sets, 0.0-1.0."""
    ka = set(extract_keywords(a, max_keywords=15))
    kb = set(extract_keywords(b, max_keywords=15))
    if not ka or not kb:
        return 0.0
    return len(ka & kb) / len(ka | kb)
