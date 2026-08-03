"""
Skills Manager — discovers installed skills from global skill directories,
reads their content, and makes them available to the agent as instructions.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

# ── skill directories (searched in order) ────────────────────────────

# Skills the agent wrote for itself. The learned/ path is where reflection
# writes (Phase 3); the self-improve/ path is the older template generator,
# kept until that code is deleted.
LEARNED_SKILLS_DIR = Path.home() / ".tomas" / "learned" / "global" / "skills"
LEGACY_LEARNED_SKILLS_DIR = Path.home() / ".tomas" / "self-improve" / "skills"

SKILL_DIRS = [
    Path.home() / ".claude" / "skills",
    Path.home() / ".agents" / "skills",
    Path.home() / "AppData" / "Roaming" / "Code" / "User" / "prompts",
    LEARNED_SKILLS_DIR,
    LEGACY_LEARNED_SKILLS_DIR,
]


def find_skill_dirs() -> list[Path]:
    """Return skill directories that actually exist on this system."""
    return [d for d in SKILL_DIRS if d.is_dir()]


def strip_yaml_frontmatter(text: str) -> tuple[str, dict]:
    """
    Remove YAML frontmatter (--- ... ---) from a markdown file.
    Returns (body, frontmatter_dict).
    """
    pattern = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
    m = pattern.match(text)
    if not m:
        return text, {}
    # simple YAML key: value parser (no nested)
    frontmatter: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            frontmatter[k.strip()] = v.strip().strip("\"'")
    return text[m.end() :], frontmatter


def discover_skills() -> list[dict]:
    """
    Scan all skill directories and return a list of skill info dicts:
      { "name": str, "file": Path, "description": str, "content": str }
    """
    skills: list[dict] = []
    seen: set[str] = set()

    for skills_dir in find_skill_dirs():
        if not skills_dir.is_dir():
            continue
        for entry in sorted(skills_dir.iterdir()):
            name = entry.name
            if name.startswith(".") or name.startswith("_"):
                continue
            # deduplicate by name (first dir wins)
            if name in seen:
                continue
            seen.add(name)

            if entry.is_dir():
                # Read SKILL.md inside the directory (standard skill format)
                skill_file = entry / "SKILL.md"
                if not skill_file.exists():
                    # try any .md file
                    md_files = list(entry.glob("*.md"))
                    if md_files:
                        skill_file = md_files[0]
                    else:
                        continue
            elif entry.suffix == ".md":
                skill_file = entry
            else:
                continue

            try:
                raw = skill_file.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            body, frontmatter = strip_yaml_frontmatter(raw)
            desc = frontmatter.get("description", "") or frontmatter.get(
                "name", name
            )
            skills.append(
                {
                    "name": name,
                    "file": skill_file,
                    "description": desc,
                    "content": body,
                    "learned": skills_dir in (LEARNED_SKILLS_DIR,
                                              LEGACY_LEARNED_SKILLS_DIR),
                }
            )

    return skills


def build_skills_section() -> str:
    """
    Build a markdown section listing all installed skills and their
    descriptions, for injection into the system prompt.
    """
    skills = discover_skills()
    if not skills:
        return ""

    lines = [
        "# Installed Agent Skills",
        "",
        "The following skills are available on this system. "
        "Use them to guide your behavior when relevant.",
        "",
    ]
    for s in skills:
        origin = " *(learned from your past sessions)*" if s.get("learned") else ""
        lines.append(f"- **{s['name']}**: {s['description']}{origin}")
    lines.append("")
    return "\n".join(lines)


def cmd_skill_list() -> str:
    """Return a human-readable list of installed skills."""
    skills = discover_skills()
    if not skills:
        return "No skills installed."

    dirs = find_skill_dirs()
    lines = [
        "Installed skills:",
        "",
        "Skill directories:",
    ]
    for d in dirs:
        lines.append("  {}".format(d))
    lines.append("")
    lines.append("Skills ({})".format(len(skills)))
    lines.append("")

    for s in skills:
        lines.append("  {} - {}".format(s["name"], s["description"]))
    return "\n".join(lines)


def cmd_skill_run(name: str) -> Optional[str]:
    """
    Find a skill by name (case-insensitive partial match) and return its
    content (instruction text). Returns None if not found.
    """
    skills = discover_skills()
    name_lower = name.lower().replace("-", " ").strip()

    # Try exact match first
    for s in skills:
        if s["name"].lower() == name_lower:
            return s["content"]

    # Try partial / fuzzy match
    for s in skills:
        sn = s["name"].lower()
        if name_lower in sn or sn in name_lower:
            return s["content"]

    # Try matching without dir prefix / extension
    for s in skills:
        stem = Path(s["file"]).stem.lower().replace("-", " ")
        if stem == name_lower or name_lower in stem:
            return s["content"]

    return None
