"""
Instructions Manager — loads global and project-level agent instructions.

Two tiers:
1. Global instructions (~/.tomas/instructions/) — apply to EVERY session,
   independent of the current project. Good for personal preferences,
   coding standards, and default agent behaviour.

2. Project-level instructions — loaded from the project directory:
   - AGENT.md  (in project root, checked first)
   - agent.md  (in project root, fallback)
   - ~/.tomas/instructions/project/AGENT.md (per-project via .tomas dir)

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

def get_project_instructions(project_dir: Path | None = None) -> str:
    """Load project-level instructions from the project directory.

    Checks, in order of priority (first match wins):
    1. <project_dir>/AGENT.md
    2. <project_dir>/agent.md
    3. ~/.tomas/instructions/project/AGENT.md
       (if a matching file for the project exists there)

    Returns the content string, or empty string if nothing found.
    """
    if project_dir is None:
        try:
            from agent import PROJECT_DIR
            project_dir = PROJECT_DIR
        except (ImportError, Exception):
            project_dir = Path.cwd()

    candidates = [
        project_dir / "AGENT.md",
        project_dir / "agent.md",
    ]

    # Also check ~/.tomas/instructions/project/<project_name>.md
    project_name = project_dir.name
    per_project_file = PROJECT_INSTRUCTIONS_DIR / f"{project_name}.md"
    candidates.append(per_project_file)

    for path in candidates:
        if path.exists() and path.is_file():
            try:
                content = path.read_text(encoding="utf-8")
                if content.strip():
                    return (
                        f"# Project Instructions ({path.name})\n\n"
                        f"Source: {path}\n\n"
                        f"{content.strip()}\n"
                    )
            except OSError:
                continue

    return ""


# ═══════════════════════════════════════════════════════════════════════
#  Combined instructions builder
# ═══════════════════════════════════════════════════════════════════════

def build_instructions_section(project_dir: Path | None = None) -> str:
    """Build the complete instructions section for the system prompt.

    Order in the prompt:
    1. Global instructions (from ~/.tomas/instructions/)
    2. Project-level instructions (from AGENT.md / agent.md)

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

DEFAULT_INSTRUCTIONS = """# Default Agent Behaviour

- You are a helpful coding assistant.
- Always read files before editing them.
- Prefer making surgical edits over rewriting entire files.
- Keep responses concise and focused on the code.
- If a task is done, stop calling tools and summarise the result.

## Safety

- Never run destructive commands without confirmation.
- Never auto-commit or auto-push changes. The user will review and commit
  them manually.
- Don't modify files outside the project directory without asking.

## Communication

- Be direct and technical.
- Show code rather than explaining at length.
- Use tools proactively to explore and understand the codebase.
"""

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

    # Default global instructions
    default_file = instr_dir / "default.md"
    if not default_file.exists() or force:
        default_file.write_text(DEFAULT_INSTRUCTIONS.strip(), encoding="utf-8")

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

1. Place `AGENT.md` or `agent.md` in the project root directory.
2. OR place `<project-name>.md` in the `project/` subfolder here.

Project-level instructions are loaded on top of global instructions.

## Example files

- `default.md` — built-in defaults (safe to edit or delete)
- `AGENT.md` — local agent identity (safe to edit or delete)
- `project/` — per-project instruction files
"""
        readme_file.write_text(readme_content.strip(), encoding="utf-8")

    # Sample project instruction
    sample_project = project_dir / ".gitkeep"
    if not sample_project.exists():
        sample_project.write_text("", encoding="utf-8")
