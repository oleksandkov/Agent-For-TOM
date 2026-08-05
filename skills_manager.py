"""
Skills Manager — discovers installed skills from global skill directories,
reads their content, and makes them available to the agent as instructions.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Optional

# ── skill directories (searched in order) ────────────────────────────

# Skills the agent wrote for itself. Reflection (Phase 3) writes here.
LEARNED_SKILLS_DIR = Path.home() / ".tomas" / "learned" / "global" / "skills"

# The self-improve/ template generator wrote here. It is no longer on the
# discovery path: it produced entries like "sequence-read_file-read_file"
# ("when starting with read_file, consider following up with read_file") that
# crowded real skills out of the prompt budget. Kept as a constant so the
# migration in self_improve.py can find and remove the directory.
LEGACY_LEARNED_SKILLS_DIR = Path.home() / ".tomas" / "self-improve" / "skills"

# Skills that ship with TOMAS. Lives beside this module, so it resolves the
# same way in a checkout and in ~/.tomas/src after an install — the installers
# copy every non-excluded directory, so a bundled skill is present from the
# first run with no setup step.
BUNDLED_SKILLS_DIR = Path(__file__).resolve().parent / "skills"

# Order is precedence: the first directory to claim a name wins, so a user's
# own copy of a bundled skill overrides the shipped one.
SKILL_DIRS = [
    Path.home() / ".claude" / "skills",
    Path.home() / ".agents" / "skills",
    Path.home() / "AppData" / "Roaming" / "Code" / "User" / "prompts",
    LEARNED_SKILLS_DIR,
    BUNDLED_SKILLS_DIR,
]


def find_skill_dirs() -> list[Path]:
    """Return skill directories that actually exist on this system."""
    return [d for d in SKILL_DIRS if d.is_dir()]


# ── the one skill format ─────────────────────────────────────────────
#
# Bundled, user-installed and learned skills all use this frontmatter, so
# installing a skill, generating one, and *improving* an existing one are the
# same code path:
#
#     ---
#     name: ps-file-ops
#     description: How this user prefers file operations on Windows
#     triggers: ["file", "directory", "powershell"]
#     source: learned | user | bundled
#     version: 2
#     ---
#
# `triggers` feeds retrieval; `source` keeps provenance; `version` is what an
# improvement bumps.

VALID_SOURCES = ("bundled", "user", "learned")


def _parse_list_value(value: str) -> list[str]:
    """Parse a YAML-ish inline list: ["a", "b"] or a, b."""
    value = (value or "").strip()
    if not value:
        return []
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1]
    else:
        inner = value
    return [item.strip().strip("\"'") for item in inner.split(",") if item.strip()]


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


def validate_frontmatter(frontmatter: dict, fallback_name: str) -> tuple[dict, list[str]]:
    """Normalise a skill's frontmatter. Returns (normalised, problems).

    A hand-written skill with a malformed block must not crash discovery, and
    must not be silently dropped either: problems are returned so the caller
    can warn.
    """
    problems: list[str] = []
    normalised = {
        "name": (frontmatter.get("name") or fallback_name).strip(),
        "description": (frontmatter.get("description") or "").strip(),
        "triggers": _parse_list_value(frontmatter.get("triggers", "")),
        "source": (frontmatter.get("source") or "").strip().lower(),
        "version": 1,
    }
    if not normalised["description"]:
        problems.append("missing description")
    if normalised["source"] and normalised["source"] not in VALID_SOURCES:
        problems.append(f"unknown source {normalised['source']!r}")
        normalised["source"] = ""
    raw_version = frontmatter.get("version", "1")
    try:
        normalised["version"] = max(1, int(str(raw_version).strip() or 1))
    except (TypeError, ValueError):
        problems.append(f"non-numeric version {raw_version!r}")
    return normalised, problems


def render_frontmatter(meta: dict) -> str:
    """Serialise skill metadata back into the one format."""
    triggers = meta.get("triggers") or []
    lines = ["---",
             f"name: {meta.get('name', '')}",
             f"description: {meta.get('description', '')}"]
    if triggers:
        rendered = ", ".join(f'"{t}"' for t in triggers)
        lines.append(f"triggers: [{rendered}]")
    lines.append(f"source: {meta.get('source') or 'user'}")
    lines.append(f"version: {int(meta.get('version', 1))}")
    lines.append("---")
    return "\n".join(lines) + "\n"


class Skill(dict):
    """A discovered skill.

    `content` is read from disk on first access, not at discovery time.
    Loading every body up front is what `load_skills_content` used to do —
    it put the full text of every installed skill into the prompt budget.
    """

    def __getitem__(self, key):
        if key == "content" and "content" not in self:
            super().__setitem__("content", self._read_body())
        return super().__getitem__(key)

    def get(self, key, default=None):
        if key == "content" and "content" not in self:
            try:
                return self["content"]
            except Exception:
                return default
        return super().get(key, default)

    def _read_body(self) -> str:
        try:
            raw = Path(self["file"]).read_text(encoding="utf-8", errors="replace")
        except Exception:
            return ""
        body, _ = strip_yaml_frontmatter(raw)
        return body


def _read_frontmatter_only(path: Path) -> tuple[dict, bool]:
    """Read just the frontmatter block, not the whole body."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            head = f.read(4096)
    except Exception:
        return {}, False
    if not head.lstrip().startswith("---"):
        return {}, True          # no frontmatter is legal, just undescribed
    _, frontmatter = strip_yaml_frontmatter(head)
    return frontmatter, True


def discover_skills(warn: bool = False) -> list[Skill]:
    """
    Scan all skill directories and return skill records:
      { name, file, description, triggers, source, version, learned }

    Bodies are not read here — `skill["content"]` loads on demand.
    A malformed frontmatter block is reported, never fatal.
    """
    skills: list[Skill] = []
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

            frontmatter, readable = _read_frontmatter_only(skill_file)
            if not readable:
                if warn:
                    print(f"  skill skipped (unreadable): {skill_file}")
                continue

            # The display name is the declared one, or the file stem — never
            # the raw filename, or a rewrite would put "foo.md" in the
            # frontmatter's name field.
            fallback = entry.name if entry.is_dir() else entry.stem
            meta, problems = validate_frontmatter(frontmatter, fallback)
            if problems and warn:
                print(f"  skill {fallback}: {'; '.join(problems)}")

            learned = skills_dir == LEARNED_SKILLS_DIR
            bundled = skills_dir == BUNDLED_SKILLS_DIR
            default_source = "learned" if learned else "bundled" if bundled else "user"
            skills.append(Skill({
                "name": meta["name"] or fallback,
                "file": skill_file,
                "description": meta["description"] or meta["name"] or name,
                "triggers": meta["triggers"],
                "source": meta["source"] or default_source,
                "version": meta["version"],
                "learned": learned,
                "problems": problems,
            }))

    return skills


def match_skills(message: str, skills: Optional[list] = None) -> list:
    """Skills whose triggers appear in what the user just wrote.

    `triggers` has always been documented as feeding retrieval, but nothing
    read it: a skill's body only ever loaded when someone typed
    `/skill <name>`, so a skill describing how to do a job never reached the
    model *while it was doing that job*. Matching is substring-based on the
    lowercased message because triggers are phrases, not words — "як у
    прикладі" and "same style" have to match as written.
    """
    text = (message or "").lower()
    if not text.strip():
        return []
    matched = []
    for skill in (discover_skills() if skills is None else skills):
        for trigger in skill.get("triggers") or []:
            if trigger and trigger.lower() in text:
                matched.append(skill)
                break
    return matched


def build_triggered_skills(message: str, max_chars: int) -> str:
    """Bodies of the skills this message triggers, for the system prompt.

    Bodies, not descriptions: a procedure the model is meant to follow is
    worthless summarised to one line. Budgeted by whole skills — half a
    procedure is worse than none, because the model cannot tell it is reading
    half.
    """
    matched = match_skills(message)
    if not matched or max_chars <= 0:
        return ""
    out: list[str] = []
    used = 0
    for skill in matched:
        body = (skill.get("content") or "").strip()
        if not body:
            continue
        block = f"## Skill: {skill['name']}\n\n{body}"
        if used + len(block) > max_chars:
            # Dropping the skill whole and saying nothing is how a skill
            # silently stops applying when someone edits it past the budget —
            # the prompt looks fine and the procedure is simply gone. Keep the
            # head of it and say where it was cut, so the loss is visible.
            room = max_chars - used - 120
            if room > 400:
                out.append(block[:room].rstrip() +
                           f"\n\n[... {skill['name']} truncated at {room} of "
                           f"{len(block)} chars — read the rest with "
                           f"/skill {skill['name']} ...]")
            break
        out.append(block)
        used += len(block)
    if not out:
        return ""
    return ("# Applicable skill instructions\n\n"
            "Triggered by this message. Follow them.\n\n" + "\n\n".join(out))


def find_skill(name: str) -> Optional[Skill]:
    """Look up one skill by directory/file name or declared name."""
    wanted = (name or "").strip().lower()
    for skill in discover_skills():
        candidates = {str(skill["name"]).lower(),
                      Path(skill["file"]).stem.lower()}
        if wanted in candidates:
            return skill
    return None


def write_skill(path: Path, meta: dict, body: str) -> Path:
    """Write a skill in the one format. Used by install, generate and improve."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_frontmatter(meta) + "\n" + body.strip() + "\n",
                    encoding="utf-8")
    return path


def improve_skill(name: str, addition: str,
                  description: Optional[str] = None,
                  triggers: Optional[list[str]] = None) -> Optional[Path]:
    """Append to an existing skill and bump its version, keeping provenance.

    This is the third use of the same code path: the user installs a skill,
    the agent generates one, and the agent improves one it already has. All
    three write the same format, so improvement costs almost nothing.
    """
    skill = find_skill(name)
    if skill is None:
        return None
    path = Path(skill["file"])
    body = skill["content"]
    meta = {
        "name": skill["name"],
        "description": description or skill["description"],
        "triggers": triggers if triggers is not None else skill["triggers"],
        "source": skill["source"],
        "version": int(skill["version"]) + 1,
    }
    stamp = time.strftime("%Y-%m-%d")
    merged = f"{body.rstrip()}\n\n## Learned {stamp} (v{meta['version']})\n\n{addition.strip()}\n"
    return write_skill(path, meta, merged)


# A catalogue entry only has to be enough to *pick* a skill. The full text is
# injected separately, in full, when a message actually triggers one — so
# shipping every word of every description bought nothing and cost the same
# tokens on every turn forever. One skill's description ran to a thousand
# characters of trigger phrases ("open a website", "fill out a form", …), which
# is guidance for the skill, not for choosing it.
#
# 60 is chosen against the section budget rather than by taste: with 45 skills
# installed the untruncated list needed 6,879 characters, so 19 of them were
# dropped entirely and the model could not know they existed. At 60 the whole
# catalogue fits inside MAX_SKILLS_CHARS with room to spare. A terse entry is
# still findable; a missing one is not, and the names carry most of the
# meaning anyway.
SKILL_SUMMARY_CHARS = 60


def _summarise(description: str) -> str:
    """First sentence, capped — enough to choose by, not the whole procedure."""
    text = " ".join((description or "").split())
    if len(text) <= SKILL_SUMMARY_CHARS:
        return text
    # Prefer a sentence boundary inside the budget; a clean stop reads better
    # than a hard cut and is usually the summary sentence anyway.
    window = text[:SKILL_SUMMARY_CHARS]
    stop = max(window.rfind(". "), window.rfind("! "), window.rfind("? "))
    if stop >= SKILL_SUMMARY_CHARS // 2:
        return window[:stop + 1]
    return window.rsplit(" ", 1)[0].rstrip(",;:") + "…"


def build_skills_section(max_chars: Optional[int] = None) -> str:
    """
    Build a markdown section listing all installed skills and their
    descriptions, for injection into the system prompt.

    When `max_chars` is given the list is budgeted by whole entries: a skill
    is either listed or it is not. Slicing the joined string at a character
    offset instead (the old behaviour) cut mid-entry and left the model
    reading half a skill name.
    """
    skills = discover_skills()
    if not skills:
        return ""

    header = [
        "# Installed Agent Skills",
        "",
        "The following skills are available on this system. "
        "Use them to guide your behavior when relevant.",
        "",
    ]

    def render(s: dict) -> str:
        origin = " *(learned from your past sessions)*" if s.get("learned") else ""
        return f"- **{s['name']}**: {_summarise(s['description'])}{origin}"

    entries = [render(s) for s in skills]
    if max_chars is None:
        return "\n".join(header + entries + [""])

    budget = max_chars - len("\n".join(header + [""])) - 1
    kept: list[str] = []
    used = 0
    for i, entry in enumerate(entries):
        remaining = len(entries) - i
        # Reserve room for the "N more not shown" line while any remain.
        note = f"- *(+{remaining} more skills not shown)*"
        reserve = len(note) + 1 if remaining > 1 else 0
        if used + len(entry) + 1 + reserve > budget:
            kept.append(f"- *(+{remaining} more skills not shown)*")
            break
        kept.append(entry)
        used += len(entry) + 1
    return "\n".join(header + kept + [""])


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
