"""Learn who the user is, then put each thing in the channel it belongs in.

    python skills/onboard/profile.py observe            # evidence, no questions
    python skills/onboard/profile.py apply <profile.json> [--write]
    python skills/onboard/profile.py status             # what is known, and its cost

## Why routing is the hard part

Three places already hold knowledge about the user, and they cost wildly
different amounts:

| channel | where | per turn | limit |
|---|---|---|---|
| instructions | `~/.tomas/instructions/*.md` | ~nothing — it is in the *stable*, cached half of the prompt | 40,000 chars |
| directives   | a fact with `kind: directive` | **full price, every turn** | 10 items / 800 chars |
| explicit facts | a fact with `kind: explicit` | only when the message matches | scored by relevance |

Put an unconditional rule in a long instructions file and it drowns. Put a
topical preference in a directive and it bills every single turn, forever,
out of ten slots. So the interview is the easy half; deciding where each
answer goes is the half worth writing code for.

`observe` reads what already happened and prints a draft *with the evidence
attached*, because the fact store's contract is that beliefs carry their
grounds — an onboarding that injects unevidenced claims poisons it. `apply`
is a dry run unless told otherwise, and refuses to silently evict a
directive.
"""
import argparse
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TOMAS = Path.home() / ".tomas"
SESSIONS = TOMAS / "sessions"
INSTRUCTIONS = TOMAS / "instructions"

#: The file this skill owns. Numbered so the alphabetical merge order in
#: `instructions_manager.get_global_instructions` is predictable, and separate
#: from `AGENT.md` because that one is the user's — hand-written, and not ours
#: to rewrite. Re-running updates this file and touches nothing else.
PROFILE_FILE = "10-profile.md"
PROFILE_MARK = "<!-- written by skills/onboard -->"

#: How many sessions to read. Enough to see a pattern, few enough to stay fast.
SESSION_WINDOW = 40


# ── evidence ────────────────────────────────────────────────────────────────

def _sessions() -> list[dict]:
    if not SESSIONS.is_dir():
        return []
    out = []
    for path in sorted(SESSIONS.glob("*.json"), reverse=True)[:SESSION_WINDOW]:
        try:
            with path.open(encoding="utf-8") as f:
                out.append(json.load(f))
        except (OSError, ValueError):
            continue
    return out


def _user_messages(session: dict) -> list[str]:
    out = []
    for m in session.get("messages", []):
        if m.get("role") != "user":
            continue
        c = m.get("content")
        if isinstance(c, str) and c.strip():
            out.append(c)
    return out


#: Letters that separate the two Cyrillic languages this agent actually sees.
#: Counting Cyrillic alone cannot: the alphabets overlap almost entirely, and
#: guessing wrong picks the wrong default reply language, which is the single
#: most visible thing a profile gets right or wrong.
_UK_ONLY = set("їієґ")
_RU_ONLY = set("ыэъё")

#: Letters are not enough on their own. "зроби це для мене, будь ласка" is
#: unmistakably Ukrainian and contains not one of `їієґ` — a great many
#: ordinary sentences don't. These are the highest-frequency words that differ
#: between the two, paired so neither list can quietly outweigh the other.
_UK_WORDS = {"що", "це", "ще", "дуже", "зроби", "ласка", "потрібно", "також",
             "мене", "тобі", "будь", "робота", "файли", "зробити"}
_RU_WORDS = {"что", "это", "еще", "ещё", "очень", "сделай", "пожалуйста",
             "нужно", "также", "меня", "тебе", "работа", "файлы", "сделать"}

_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def _language_mix(texts: list[str]) -> dict:
    latin = cyr = uk = ru = 0
    for t in texts:
        lowered = t.lower()
        for ch in lowered:
            if ch in _UK_ONLY:
                uk += 1
            elif ch in _RU_ONLY:
                ru += 1
            if "а" <= ch <= "я" or ch in "іїєґёыэъ":
                cyr += 1
            elif "a" <= ch <= "z":
                latin += 1
        for word in _WORD_RE.findall(lowered):
            if word in _UK_WORDS:
                uk += 1
            elif word in _RU_WORDS:
                ru += 1
    total = cyr + latin
    return {
        "cyrillic_share": round(cyr / total, 2) if total else 0.0,
        "latin_share": round(latin / total, 2) if total else 0.0,
        "likely": ("uk" if uk > ru else "ru" if ru > uk else
                   "en" if latin > cyr else "unknown"),
        "uk_markers": uk, "ru_markers": ru,
    }


def _project_shape(project_dir: Path) -> dict:
    """What kind of work this directory is, by what is in it."""
    exts: Counter = Counter()
    if project_dir.is_dir():
        for p in project_dir.rglob("*"):
            if not p.is_file():
                continue
            parts = set(p.parts)
            if parts & {".git", "node_modules", ".venv", "__pycache__"}:
                continue
            if p.suffix:
                exts[p.suffix.lower()] += 1
            if len(exts) > 400:
                break
    return {
        "top_extensions": exts.most_common(8),
        "has_agents_md": (project_dir / "AGENTS.md").exists()
                         or (project_dir / "agent.md").exists(),
        "has_claude_md": (project_dir / "CLAUDE.md").exists(),
    }


def observe(project_dir: Path) -> dict:
    sessions = _sessions()
    texts, tools, models, first_asks = [], Counter(), Counter(), []
    for s in sessions:
        msgs = _user_messages(s)
        texts += msgs
        if msgs:
            first_asks.append(msgs[0][:160].replace("\n", " "))
        if s.get("model"):
            models[s["model"]] += 1
        for entry in s.get("tool_log", []):
            if entry.get("tool"):
                tools[entry["tool"]] += 1

    known = {"facts": 0, "directives": 0}
    try:
        from learning import store
        facts = store.load_facts("global")
        known["facts"] = len(facts)
        known["directives"] = sum(1 for f in facts
                                  if f.get("kind") == store.KIND_DIRECTIVE)
    except Exception:                                # noqa: BLE001
        pass

    existing = []
    if INSTRUCTIONS.is_dir():
        existing = [f.name for f in sorted(INSTRUCTIONS.glob("*.md"))]

    return {
        "sessions_read": len(sessions),
        "language": _language_mix(texts),
        "top_tools": tools.most_common(10),
        "models": models.most_common(5),
        "recent_requests": first_asks[:8],
        "project": {"name": project_dir.name, **_project_shape(project_dir)},
        "instruction_files": existing,
        "already_known": known,
    }


def print_observation(data: dict) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=1))
    print()
    if not data["sessions_read"]:
        # A cold install has nothing to infer from, and saying so is the
        # difference between "I asked because I had to" and a questionnaire
        # that looks like it ignored everything it could have known.
        print("NOTE: no past sessions — there is nothing to infer from, so "
              "every field has to be asked. Say so when you ask.")
        return
    lang = data["language"]
    print(f"Read {data['sessions_read']} session(s). Language looks like "
          f"{lang['likely']} ({lang['cyrillic_share']:.0%} Cyrillic). "
          f"Confirm rather than assume — and ask separately which language "
          f"the *artefacts* should be in, which is often not the same one.")


# ── routing ─────────────────────────────────────────────────────────────────

_CHANNELS = ("identity", "directives", "preferences", "project")


def _render_profile_md(identity: list[str], project_name: str) -> str:
    lines = [PROFILE_MARK,
             "# About this user",
             "",
             "Written by `/setup`. Edit freely — re-running updates this file "
             "and leaves your own instruction files alone.",
             ""]
    lines += [f"- {line.strip()}" for line in identity if line.strip()]
    return "\n".join(lines).rstrip() + "\n"


def _directive_budget(store) -> tuple[int, int]:
    facts = store.load_facts("global")
    live = [f for f in facts
            if f.get("kind") == store.KIND_DIRECTIVE
            and f.get("status") == store.STATUS_ACTIVE]
    return len(live), sum(len(f.get("fact", "")) for f in live)


def apply(profile: dict, write: bool, project_dir: Path) -> int:
    from learning import store

    identity = [str(x) for x in profile.get("identity") or []]
    directives = [str(x) for x in profile.get("directives") or []]
    preferences = profile.get("preferences") or []
    project_notes = [str(x) for x in profile.get("project") or []]

    count, chars = _directive_budget(store)
    planned_chars = chars + sum(len(d) for d in directives)
    over = (count + len(directives) > store.MAX_DIRECTIVES
            or planned_chars > store.MAX_DIRECTIVE_CHARS)

    print(f"{'WRITING' if write else 'DRY RUN — nothing written yet'}\n")
    if identity:
        target = INSTRUCTIONS / PROFILE_FILE
        print(f"  instructions -> {target}")
        print(f"    {len(identity)} line(s), "
              f"{len(_render_profile_md(identity, project_dir.name))} chars "
              f"— cached with the stable prompt, ~free per turn")
    for d in directives:
        print(f"  directive    -> {d[:70]!r} ({len(d)} chars, EVERY turn)")
    for p in preferences:
        text = p.get("text", p) if isinstance(p, dict) else p
        print(f"  preference   -> {str(text)[:70]!r} (recalled when relevant)")
    if project_notes:
        print(f"  project      -> {INSTRUCTIONS / 'project' / (project_dir.name + '.md')}"
              f" ({len(project_notes)} line(s))")

    print(f"\nDirective budget: {count}+{len(directives)}/"
          f"{store.MAX_DIRECTIVES} items, {planned_chars}/"
          f"{store.MAX_DIRECTIVE_CHARS} chars")
    if over:
        # Never silently evict. A rule the user set and cannot see being
        # dropped is the exact failure the store's own cap comment warns
        # about, and an onboarding run is the worst place to introduce it.
        print("\nREFUSED: this would exceed the directive budget. Directives "
              "are injected on every turn, so the cap is real. Drop one with "
              "/forget <id>, or move the weakest of these into `preferences` "
              "(recalled when relevant) or `identity` (cached, free).")
        return 1

    if not write:
        print("\nRe-run with --write to commit. Nothing has changed.")
        return 0

    INSTRUCTIONS.mkdir(parents=True, exist_ok=True)
    if identity:
        (INSTRUCTIONS / PROFILE_FILE).write_text(
            _render_profile_md(identity, project_dir.name), encoding="utf-8")
    if project_notes:
        pdir = INSTRUCTIONS / "project"
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / f"{project_dir.name}.md").write_text(
            "\n".join(f"- {n}" for n in project_notes) + "\n", encoding="utf-8")

    from learning.promotion import remember
    for d in directives:
        remember(store.KIND_DIRECTIVE, d, evidence="stated during /setup")
    for p in preferences:
        if isinstance(p, dict):
            remember(store.KIND_EXPLICIT, str(p.get("text", "")),
                     evidence=str(p.get("evidence", "stated during /setup")))
        else:
            remember(store.KIND_EXPLICIT, str(p),
                     evidence="stated during /setup")

    try:
        import onboarding
        onboarding.mark_completed()
    except Exception:                                # noqa: BLE001
        pass

    print("\nDone. `/setup` again any time — it updates, never duplicates.")
    print("Undo: /forget <id> for a rule, or delete "
          f"{INSTRUCTIONS / PROFILE_FILE}.")
    return 0


def status(project_dir: Path) -> int:
    from learning import store
    count, chars = _directive_budget(store)
    facts = store.load_facts("global")
    print(f"Instruction files: "
          f"{', '.join(f.name for f in sorted(INSTRUCTIONS.glob('*.md'))) or '(none)'}")
    print(f"Profile written by /setup: "
          f"{'yes' if (INSTRUCTIONS / PROFILE_FILE).exists() else 'no'}")
    print(f"Facts: {len(facts)} · directives {count}/{store.MAX_DIRECTIVES}, "
          f"{chars}/{store.MAX_DIRECTIVE_CHARS} chars")
    try:
        import onboarding
        s = onboarding.state()
        if s["completed"]:
            offers = "done — nothing will offer it again"
        elif s["sessions_seen"] > onboarding.PROPOSE_UNTIL_SESSION:
            offers = "window closed — /setup only"
        else:
            left = onboarding.PROPOSE_UNTIL_SESSION - s["sessions_seen"]
            offers = f"{max(0, left)} more session(s) may offer it"
        print(f"Sessions seen: {s['sessions_seen']} · {offers}")
    except Exception:                                # noqa: BLE001
        pass
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="profile.py")
    sub = parser.add_subparsers(dest="command", required=True)
    o = sub.add_parser("observe", help="gather evidence; asks nothing")
    o.add_argument("--project", default=os.getcwd())
    a = sub.add_parser("apply", help="route a profile into the three channels")
    a.add_argument("profile")
    a.add_argument("--write", action="store_true")
    a.add_argument("--project", default=os.getcwd())
    s = sub.add_parser("status", help="what is known, and what it costs")
    s.add_argument("--project", default=os.getcwd())

    args = parser.parse_args(argv)
    project_dir = Path(args.project).resolve()
    if args.command == "observe":
        print_observation(observe(project_dir))
        return 0
    if args.command == "status":
        return status(project_dir)
    with open(args.profile, encoding="utf-8") as f:
        return apply(json.load(f), args.write, project_dir)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
