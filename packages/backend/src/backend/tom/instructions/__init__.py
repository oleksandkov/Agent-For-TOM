"""Agent instructions and system prompt loader.

Section 6 reads the prompt each turn via
:func:`backend.tom.instructions.loader.render_prompt`.
Section 10 will formalise the user override endpoint, audit logging,
and per-user overrides stored in ``data_dir/core_memory.json``.
"""

from __future__ import annotations

from backend.tom.instructions.loader import (
    load_base_prompt,
    render_prompt,
)

__all__: list[str] = ["load_base_prompt", "render_prompt"]
