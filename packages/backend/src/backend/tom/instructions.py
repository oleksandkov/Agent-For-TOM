"""TOM agent instructions and system prompt loader.

The actual prompt content and user-override merging land in Section 9.
This module exists now so `from backend.tom import instructions` is stable
and so the instructions layer can be wired into the chat orchestrator in
Section 6 without touching the public import path.
"""

from __future__ import annotations

from pathlib import Path

PROMPT_DIR: Path = Path(__file__).resolve().parent / "instructions_assets"

__all__: list[str] = ["PROMPT_DIR"]
