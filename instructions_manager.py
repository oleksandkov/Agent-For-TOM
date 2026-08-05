"""
Instructions Manager — loads global and project-level agent instructions.

Two tiers:
1. Global instructions (~/.tomas/instructions/) — apply to EVERY session,
   independent of the current project. Good for personal preferences,
   coding standards, and default agent behaviour.

2. Project-level instructions — loaded from the project directory:
    - AGENTS.md  (in project root, checked first)
    - agent.md   (in project root, fallback)
    - ~/.tomas/instructions/project/AGENTS.md (per-project via .tomas dir)

The merged instructions are injected into the system prompt.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

# ── Paths ──────────────────────────────────────────────────────────────

TOMAS_DIR = Path.home() / ".tomas"
GLOBAL_INSTRUCTIONS_DIR = TOMAS_DIR / "instructions"
PROJECT_INSTRUCTIONS_DIR = TOMAS_DIR / "instructions" / "project"


# ═══════════════════════════════════════════════════════════════════════
#  Global instructions  (~/.tomas/instructions/)
# ═══════════════════════════════════════════════════════════════════════

def get_global_instructions_dir() -> Path:
    """Return the global instructions directory, creating it if needed."""
    GLOBAL_INSTRUCTIONS_DIR.mkdir(parents=True, exist_ok=True)
    return GLOBAL_INSTRUCTIONS_DIR


def get_global_instructions() -> str:
    """Load all global instruction files, merged into one string.

    Reads all .md files from ~/.tomas/instructions/ in alphabetical order.
    """
    instr_dir = get_global_instructions_dir()
    if not instr_dir.exists():
        return ""

    parts: list[str] = []
    for f in sorted(instr_dir.glob("*.md")):
        if f.name.startswith("."):
            continue
        try:
            content = f.read_text(encoding="utf-8")
            if content.strip():
                parts.append(f"## {f.stem.replace('-', ' ').title()}")
                parts.append("")
                parts.append(content.strip())
                parts.append("")
        except OSError:
            continue

    if not parts:
        return ""

    header = "# Global Agent Instructions\n\n"
    header += "These instructions apply to EVERY session, regardless of project.\n\n"
    return header + "\n".join(parts)


# ═══════════════════════════════════════════════════════════════════════
#  Project-level instructions
# ═══════════════════════════════════════════════════════════════════════

#: Project instruction files, in priority order. Every one that exists is
#: loaded — they are not alternatives.
#:
#: CLAUDE.md is on this list because both CLAUDE.md and AGENTS.md documented it
#: as injected into every system prompt, and it never was: this function
#: returned on the first match, AGENTS.md always matched first, and 7,199 bytes
#: of project conventions reached the model in no session ever. Conventions
#: written for the agent that the agent cannot read are worse than none —
#: they read as followed.
PROJECT_INSTRUCTION_FILES = ("AGENTS.md", "agent.md", "CLAUDE.md")


def get_project_instructions(project_dir: Path | None = None) -> str:
    """Load project-level instructions from the project directory.

    Loads every file in `PROJECT_INSTRUCTION_FILES` that exists, in order,
    plus `~/.tomas/instructions/project/<project_name>.md`. Each is labelled
    with its source so the model can tell which document a rule came from.

    Returns the merged content, or empty string if nothing was found.
    """
    if project_dir is None:
        try:
            from agent import PROJECT_DIR
            project_dir = PROJECT_DIR
        except (ImportError, Exception):
            project_dir = Path.cwd()

    candidates = [project_dir / name for name in PROJECT_INSTRUCTION_FILES]
    # Also check ~/.tomas/instructions/project/<project_name>.md
    candidates.append(PROJECT_INSTRUCTIONS_DIR / f"{project_dir.name}.md")

    seen: set[str] = set()
    parts: list[str] = []
    for path in candidates:
        try:
            resolved = str(path.resolve()).lower()
        except OSError:
            resolved = str(path).lower()
        # A case-insensitive filesystem makes AGENTS.md and agents.md the same
        # file; loading it twice would double its weight in the prompt.
        if resolved in seen or not (path.exists() and path.is_file()):
            continue
        seen.add(resolved)
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if content.strip():
            parts.append(
                f"# Project Instructions ({path.name})\n\n"
                f"Source: {path}\n\n"
                f"{content.strip()}\n"
            )

    return "\n\n---\n\n".join(parts)


# ═══════════════════════════════════════════════════════════════════════
#  Combined instructions builder
# ═══════════════════════════════════════════════════════════════════════

def build_instructions_section(project_dir: Path | None = None) -> str:
    """Build the complete instructions section for the system prompt.

    Order in the prompt:
    1. Global instructions (from ~/.tomas/instructions/)
    2. Project-level instructions (from AGENTS.md / agent.md)

    Returns the merged markdown string, or empty string if nothing found.
    """
    parts: list[str] = []

    global_instr = get_global_instructions()
    if global_instr:
        parts.append(global_instr)

    project_instr = get_project_instructions(project_dir)
    if project_instr:
        parts.append(project_instr)

    if not parts:
        return ""

    return "\n\n---\n\n".join(parts)


# ═══════════════════════════════════════════════════════════════════════
#  Default instructions (for install.ps1)
# ═══════════════════════════════════════════════════════════════════════

DEFAULT_AGENT_INSTRUCTIONS = """# Agent Identity

- Your name is TOMAS agent.
- Each report must be ended with My Lord.
"""

DEFAULT_PROJECT_INSTRUCTIONS = """# Per-Project Instructions

Place project-specific instructions here. This file is checked when
the agent starts in a project with this name.

To use:
1. Save this file as ~/.tomas/instructions/project/<project-name>.md
2. Fill in instructions specific to that project

Example for a Python project:
- Use `.venv\\Scripts\\python.exe` as the Python interpreter
- Run tests with `pytest`
- Follow PEP 8 style
"""


def create_default_instructions(force: bool = False):
    """Create default instruction files in the global instructions directory.

    Does NOT overwrite existing files unless force=True.
    """
    instr_dir = get_global_instructions_dir()
    project_dir = PROJECT_INSTRUCTIONS_DIR
    project_dir.mkdir(parents=True, exist_ok=True)

    # Default AGENT.md (local-level agent identity)
    agent_file = instr_dir / "AGENT.md"
    if not agent_file.exists() or force:
        agent_file.write_text(DEFAULT_AGENT_INSTRUCTIONS.strip(), encoding="utf-8")

    # README explaining the instructions system
    readme_file = instr_dir / "README.md"
    if not readme_file.exists() or force:
        readme_content = """# TOMAS Agent Instructions

This folder contains **global instructions** that apply to every TOMAS
session, regardless of the project you're working on.

## How it works

- Every `.md` file in this folder is loaded in alphabetical order and
  merged into the agent's system prompt.
- Use these files to set persistent preferences, coding standards, and
  default behaviour.

## Project-level instructions

You can also add instructions per project:

1. Place `AGENTS.md` or `agent.md` in the project root directory.
2. OR place `<project-name>.md` in the `project/` subfolder here.

Project-level instructions are loaded on top of global instructions.

## Example files

- `AGENT.md` — local agent identity (safe to edit or delete)
- `project/` — per-project instruction files
"""
        readme_file.write_text(readme_content.strip(), encoding="utf-8")

    # Sample project instruction
    sample_project = project_dir / ".gitkeep"
    if not sample_project.exists():
        sample_project.write_text("", encoding="utf-8")
