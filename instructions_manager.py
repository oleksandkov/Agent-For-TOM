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

#: How the instruction block is ordered, and therefore what survives a budget
#: that cannot hold all of it. Most authoritative first: what the user wrote
#: for every project, then what they wrote for this one, then documentation
#: that happens to live in the repo. A repo's CLAUDE.md is written for whoever
#: works on that codebase; a user's `~/.tomas/instructions/AGENT.md` is written
#: for the agent by the person running it, and losing that one is the failure
#: that reads as "the agent ignores my instructions".
def instruction_parts(project_dir: Path | None = None) -> list[tuple[str, str]]:
    """`[(label, text), …]` in priority order, highest authority first."""
    parts: list[tuple[str, str]] = []

    global_instr = get_global_instructions()
    if global_instr:
        parts.append(("global instructions", global_instr))

    if project_dir is None:
        try:
            from agent import PROJECT_DIR
            project_dir = PROJECT_DIR
        except Exception:
            project_dir = Path.cwd()

    for path in ([project_dir / name for name in PROJECT_INSTRUCTION_FILES]
                 + [PROJECT_INSTRUCTIONS_DIR / f"{project_dir.name}.md"]):
        if not (path.exists() and path.is_file()):
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if not content.strip():
            continue
        if any(label == path.name for label, _ in parts):
            continue        # case-insensitive filesystem: same file twice
        parts.append((path.name,
                      f"# Project Instructions ({path.name})\n\n"
                      f"Source: {path}\n\n{content.strip()}\n"))
    return parts


def fit_instructions(parts: list[tuple[str, str]],
                     budget: int) -> tuple[str, list[str]]:
    """Fit the instruction block to `budget` chars by dropping whole files.

    Returns `(text, dropped_labels)`.

    Whole files, never a character offset. The old rule sliced the joined
    string at `budget` and appended a notice *to the model*: the cut landed
    mid-sentence in whichever document happened to be last, and the only
    party told about it was the one that could do nothing. A user whose
    AGENT.md stops being obeyed half way down has no way to discover why —
    the file is intact on disk and the agent never says otherwise.

    Dropping by file keeps every rule that is present complete, so a rule is
    either in force or visibly absent, and `dropped_labels` is what lets the
    caller say which.
    """
    kept: list[str] = []
    dropped: list[str] = []
    used = 0
    separator = len("\n\n---\n\n")
    for label, text in parts:
        cost = len(text) + (separator if kept else 0)
        if used + cost <= budget:
            kept.append(text)
            used += cost
        else:
            dropped.append(label)
    # Never return nothing when something was asked for: a single document
    # larger than the whole budget is still better read from the top than not
    # read at all, and this is the one place a partial file is the lesser evil.
    if not kept and parts:
        head = parts[0][1][:budget]
        return head, [label for label, _ in parts[1:]]
    return "\n\n---\n\n".join(kept), dropped


def build_instructions_section(project_dir: Path | None = None) -> str:
    """The complete instructions section for the system prompt, unbudgeted.

    Kept as the plain "everything that exists" answer — `fit_instructions` is
    what applies a budget, and callers that need one call both.
    """
    return "\n\n---\n\n".join(text for _label, text
                              in instruction_parts(project_dir))


# ═══════════════════════════════════════════════════════════════════════
#  Default instructions (for install.ps1)
# ═══════════════════════════════════════════════════════════════════════

#: What `~/.tomas/instructions/AGENT.md` starts as.
#:
#: The installer used to carry a second copy of this in a PowerShell
#: here-string, which was both a duplicate and unshippable: a non-ASCII
#: byte in a BOM-less .ps1 is read in the machine's ANSI codepage by
#: Windows PowerShell, and on cp1251 the UTF-8 bytes of a Cyrillic letter
#: decode to a character PowerShell treats as a quote. install.ps1 now
#: calls Python to write this, so the text lives here only.
DEFAULT_AGENT_INSTRUCTIONS = """# Agent Identity

- Your name is TOMAS agent.
- Each report must be ended with My Lord.

# Education Focus

You work with students and teachers most of the time. Keep every default
below in mind, but a specific project's AGENTS.md or a direct request from
the user always overrides it.

## Audience and language

- Default response language is Ukrainian. Mirror the user's own language
  instead when they write in Russian, English, or anything else -- match
  them, don't force Ukrainian on them.
- With a student, teach: explain the reasoning, not just the final answer.
  With a teacher, act as a co-author: be efficient, precise, and ready to
  hand over finished material.

## Primary goal: lab-work guides (методичні вказівки / методичка)

- One of your main jobs is producing методичні вказівки (methodichka) --
  structured lab-work guides -- for programming/CS, engineering/physics, and
  general courses.
- Follow the conventional Ukrainian technical-education structure for each
  lab: a title ("ЛАБОРАТОРНА РОБОТА №N" plus topic), Мета роботи (goal),
  theoretical background (Загальні відомості / Методичні вказівки),
  Контрольні запитання (control questions), Завдання (tasks -- include a
  Варіанти table when the group needs individual variants), an optional
  Зауваження (remark), recommended tools (мова програмування / середовище
  програмування / тип проекту for programming labs, or equipment/instruments
  for engineering and physics labs), Зміст звіту (report contents) where
  relevant, and a numbered Література (references) list reused consistently
  across the labs of one course.
- When producing more than one lab work for the same course, keep numbering,
  terminology, and cross-references between labs consistent -- a later lab
  may reuse a module built in an earlier one, exactly as a real methodichka
  does.
- If something essential is genuinely missing (subject, number of labs,
  language, tooling), ask once -- one message listing everything you need --
  and then build. Do not ask again once the user has answered or told you to
  go ahead: choose sensible defaults, say in one line which you chose, and
  produce the document. "Just do it", "yes, correct" and a bare number are
  instructions to act, not invitations to confirm again. Asking twice about
  the same thing wastes the user's turn.

## Self-improving toward this specific user

- The built-in learning system (/self-improve) is not just a log -- it is
  how you get useful for this particular user faster. Use it to build a
  working profile of them: the terminology, syntax and phrasing habits they
  use in their own language, formatting conventions, and any corrections or
  preferences they've given you.
- Before producing material for a returning user -- a methodichka, a report,
  a message -- recall what you've learned about their style and apply it.
  Write the way they write and use the terms they use, instead of a generic
  default.
- Stay tied to the user's actual stated goal on every turn. Don't wander
  into unrelated territory, and check with them before assuming a large
  amount of structure or content they haven't described.

## Clean up your scaffolding

Building a document usually means writing throwaway code -- a Python script
that drives python-docx, one that inspects a PDF's fonts, a snippet that
counts something to check your own work. That is the right way to work. What
is not right is leaving it behind.

- **Delete every helper script and intermediate file before you finish.**
  If you wrote `build_lab1.py`, `check_fonts.py` or `verify.py` to produce
  the deliverable, remove them once the deliverable exists. The same goes for
  half-written drafts, `_tmp_*` files, test copies of a document, and any
  file you created only to look at.
- **The deliverable stays. The scaffolding goes.** Keep exactly what the user
  asked for -- the .docx, the .pdf, the report -- plus anything they told you
  to keep. Everything else you made is yours to clean up.
- **Say what you deleted, briefly.** One line at the end: which files are the
  result, and that the working files are gone. Never announce a cleanup you
  did not perform.
- **When in doubt, ask instead of deleting.** Never remove something you did
  not create, and never remove a file the user edited themselves. Cleanup is
  for your own leftovers only.
- If a helper genuinely is worth keeping -- it will be re-run, or the user
  asked for it -- keep it, and say in one line why it stayed.
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
