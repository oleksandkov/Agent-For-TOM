# Phase 1 — Close the Learning Loop

**Goal:** everything the self-improvement system writes is actually read back and affects behaviour.
**Effort:** ~1 week.
**Depends on:** Phase 0 (a broken agent loop cannot be observed learning).

The self-improvement machinery already records interactions, detects patterns, generates skills and writes notes. **Almost none of it returns to the model.** This phase does not redesign anything — it connects the pipes that already exist and fixes the storage bugs. The redesign of *what* gets learned is Phase 3.

---

## P1-1 · Auto-generated skills can never be loaded

**Severity: HIGH — this is half of the headline feature, and it is a no-op**

### The problem

`self_improve._register_skill` (`self_improve.py:553`) writes generated skills to `SELF_IMPROVE_DIR / "skills"` = `~/.tomas/self-improve/skills/` and records them in `skill-registry.json`.

`skills_manager.SKILL_DIRS` (`skills_manager.py:15-20`) is:

```python
SKILL_DIRS = [
    Path.home() / ".claude" / "skills",
    Path.home() / ".agents" / "skills",
    Path.home() / "AppData" / "Roaming" / "Code" / "User" / "prompts",
]
```

`~/.tomas/self-improve/skills` is not in the list. `skill-registry.json` is read by exactly one function, `get_auto_generated_skills()` (`:576`), which is used only to print a count on the `/si` status screen.

The docstring at `self_improve.py:556-558` states *"The skills_manager already scans global dirs, so we also add a reference in a local registry file that build_skills_section can read."* Both halves are false: the directory is not scanned, and `build_skills_section` does not read the registry.

**Net effect: the agent generates skills for itself that it is structurally incapable of using.**

### The fix

**Step 1 — add the directory.** `skills_manager.py:15`:

```python
LEARNED_SKILLS_DIR = Path.home() / ".tomas" / "self-improve" / "skills"

SKILL_DIRS = [
    Path.home() / ".claude" / "skills",
    Path.home() / ".agents" / "skills",
    Path.home() / "AppData" / "Roaming" / "Code" / "User" / "prompts",
    LEARNED_SKILLS_DIR,          # skills the agent wrote for itself
]
```

**Step 2 — make sure `discover_skills()` can parse them.** Generated skills are flat `<name>.md` files with YAML frontmatter; check whether `discover_skills` (`:45`) expects `<dir>/<name>/SKILL.md` instead. If it does, either write generated skills in that layout or teach the discovery function both. **Verify by reading `discover_skills` before changing anything** — this is the step most likely to silently do nothing.

**Step 3 — mark them in the prompt.** In `build_skills_section` (`:101`), tag learned skills so the model knows their provenance and can weigh them appropriately:

```python
    for s in skills:
        origin = " *(learned from your past sessions)*" if s.get("learned") else ""
        lines.append(f"- **{s['name']}**: {s['description']}{origin}")
```

**Step 4 — fix the docstring** at `self_improve.py:556-558` so it describes what the code does.

**Step 5 — delete `load_skills_content()`** (`skills_manager.py:123`). It loads the *full text* of every skill into the prompt, is called from nowhere, and would blow the context budget if it ever were called. Dead code that is also a trap.

### Test

```python
def test_generated_skill_reaches_the_prompt(self):
    """Regression: P1-1. A skill written by the agent must be discoverable."""
    skill = LEARNED_SKILLS_DIR / "test-generated-skill.md"
    skill.parent.mkdir(parents=True, exist_ok=True)
    skill.write_text("---\nname: test-generated-skill\n"
                     "description: proves the loop is closed\n---\n\nbody\n")
    try:
        self.assertIn("test-generated-skill", skills_manager.build_skills_section())
    finally:
        skill.unlink()
```

---

## P1-2 · Self-notes are never injected, and are the wrong shape

**Severity: HIGH**

### The problem

`self_notes.get_notes_for_context()` (`self_notes.py:369`) exists for exactly one purpose — its docstring says *"Return all notes as a formatted string for injection into the system prompt."* It is referenced **nowhere** in the codebase. `build_system_prompt()` (`agent.py:861-924`) never calls it. Every note the agent writes for itself is write-only.

Worse, the function as written could not be used even if it were called:

```python
    lines = [f"  {BOLD}Agent Self-Notes{RESET}"]      # ← ANSI escapes
    lines.append(f"  {DIM}{'─' * 46}{RESET}")         # ← terminal decoration
    for n in notes:
        if n.get("auto_generated"):
            continue                                   # ← filter runs after the header
        lines.append(f"  {DIM}[{ntype}]{RESET} {title}{tag_str}")   # ← titles only
```

Three problems: it emits **ANSI escape codes** into what would be a system prompt; it emits only **titles**, never the note body, so the model gets a table of contents with no content; and if every note is auto-generated it still returns a non-empty header, so callers cannot use truthiness to detect "nothing to say".

### The fix

Split display from prompt-building — a rule worth applying everywhere (see Phase 2).

```python
def render_notes_for_display() -> str:
    """Terminal rendering — keeps the ANSI version for /notes."""
    ...  # the current body of get_notes_for_context, unchanged


def get_notes_for_context(query: str = "", k: int = 5) -> str:
    """Plain-text note content for the system prompt.

    With a query, returns only the most relevant notes; without one, the
    k most recent. Never returns decoration, never returns a bare header.
    """
    notes = [n for n in list_notes(limit=100) if not n.get("auto_generated")]
    if not notes:
        return ""
    if query:
        notes = _rank_by_relevance(notes, query)[:k]
    else:
        notes = notes[:k]

    lines = ["# Notes I've written to myself", ""]
    for n in notes:
        body = (get_note(n["id"]) or {}).get("content", "").strip()
        lines.append(f"- [{n.get('type', 'insight')}] {n.get('title', '?')}"
                     + (f": {body[:300]}" if body else ""))
    return "\n".join(lines)
```

Then wire it into `build_system_prompt()`, next to the memory index (`agent.py:877-881`):

```python
    # notes the agent has written for itself
    try:
        notes_section = self_notes.get_notes_for_context()
        if notes_section:
            notes_section = _truncate_section(notes_section, MAX_NOTES_CHARS, "notes")
            prompt += f"\n\n{notes_section}"
    except Exception:
        pass
```

Add `MAX_NOTES_CHARS` alongside the other section budgets, and keep it small (~1500). **Cap it now**, before Phase 3 starts producing notes at volume.

> `k=5` and recency ordering are the v1 stand-in. Phase 3 replaces `_rank_by_relevance` with the shared retrieval function. Keep the signature `(query, k)` so that swap is a one-line change.

---

## P1-3 · `providers.json` is destroyed by every update

**Severity: HIGH — silent user data loss**

### The problem

```python
# agent_cli.py:48
PROJECT_DIR = Path(__file__).parent.resolve()
# agent_cli.py:301
PROVIDERS_CONFIG_PATH = PROJECT_DIR / "providers.json"
```

Provider config is stored **in the source directory**. The updater replaces `$SrcDir` wholesale (`install.ps1:231-232`) while correctly preserving `.env` (`:514`), `AGENT.md` (`:457`), sessions, memory and instructions — all of which live outside `$SrcDir`.

**Every provider the user has configured is wiped on every upgrade.** It is also per-checkout, which is why this repo's `providers.json` sits at `{"active": null, "providers": {}}`.

### The fix

**Step 1 — move it, with migration:**

```python
TOMAS_DIR = Path.home() / ".tomas"
PROVIDERS_CONFIG_PATH = TOMAS_DIR / "providers.json"
_LEGACY_PROVIDERS_PATH = Path(__file__).parent.resolve() / "providers.json"


def _migrate_providers_config() -> None:
    """One-time move of provider config out of the source tree, which the
    updater replaces wholesale (install.ps1:231)."""
    if PROVIDERS_CONFIG_PATH.exists() or not _LEGACY_PROVIDERS_PATH.exists():
        return
    try:
        data = json.loads(_LEGACY_PROVIDERS_PATH.read_text(encoding="utf-8"))
        if not data.get("providers"):
            return                      # nothing worth keeping
        TOMAS_DIR.mkdir(parents=True, exist_ok=True)
        PROVIDERS_CONFIG_PATH.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        _LEGACY_PROVIDERS_PATH.rename(
            _LEGACY_PROVIDERS_PATH.with_suffix(".json.migrated"))
    except Exception:
        pass
```

Call it once at CLI startup, before any config read.

**Step 2 — add `providers.json` to `.gitignore`** if the stale committed copy is removed, so a checkout never shadows user config again.

**Step 3 — audit for the same mistake elsewhere.** Grep for `PROJECT_DIR /` in both entry points and confirm nothing else writes user state into the source tree. Rule for the whole project: **user state lives in `~/.tomas/`, source lives in the source directory, and the two never mix.**

---

## P1-4 · The entire interaction log is re-read and re-analysed on every message

**Severity: HIGH (scaling) — degrades until the agent is unusable**

### The problem

```python
# self_improve.py:937
def record_user_message(content, msg_type="text") -> None:
    log_user_message(content, msg_type)
    interactions = get_all_interactions()      # reads the ENTIRE jsonl file
    _maybe_analyze(interactions)               # then analyses ALL of it
```

`_read_jsonl` (`:102`) loads every line ever written. `analyze_patterns` (`:211`) runs keyword extraction and pairwise similarity over the whole set. There is no rotation, no cap, no window. And it all happens **synchronously, inside the user's turn**, before the model is even called.

At 50 interactions this is invisible. At 50,000 — a few months of daily use — every message pays a full re-read and re-analysis of the entire history, on the critical path. Pairwise similarity is worse than linear.

### The fix

**Step 1 — bound what is loaded.**

```python
ANALYSIS_WINDOW = 500        # interactions considered by pattern analysis
ROTATE_AT_BYTES = 5 * 1024 * 1024


def get_recent_interactions(n: int = ANALYSIS_WINDOW) -> list[dict]:
    """Tail the log without loading the whole file."""
    if not INTERACTIONS_FILE.exists():
        return []
    with INTERACTIONS_FILE.open("r", encoding="utf-8") as f:
        tail = collections.deque(f, maxlen=n)
    out = []
    for line in tail:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out
```

Note `get_recent_interactions` already exists at `:196` — check whether it also calls `_read_jsonl` internally, and fix it if so.

**Step 2 — rotate the log.**

```python
def _rotate_if_large() -> None:
    if INTERACTIONS_FILE.exists() and INTERACTIONS_FILE.stat().st_size > ROTATE_AT_BYTES:
        INTERACTIONS_FILE.rename(
            INTERACTIONS_FILE.with_suffix(f".{int(time.time())}.jsonl"))
```

Call from `_append_jsonl`. Keep the last 2-3 archives, delete older ones — Phase 3 mines *sessions*, not raw interaction logs, so deep history has little value.

**Step 3 — get analysis off the hot path.** Minimum viable: move `_maybe_analyze` out of `record_user_message` and call it *after* the reply has been delivered, at the end of the turn in `main()`. Better: run it on session end only. Best (Phase 2): emit a `TurnFinished` event and let a background worker handle it.

```python
def record_user_message(content: str, msg_type: str = "text") -> None:
    """Log only. Analysis is triggered after the turn completes."""
    log_user_message(content, msg_type)
```

**Step 4 — cap analysis cost.** If `analyze_patterns` does pairwise `_similarity_score` over the window, that is 250k comparisons at a 500-item window. Bucket by keyword first and only compare within buckets, or cap comparisons outright.

---

## P1-5 · The analysis trigger fires unpredictably

**Severity: LOW — but it makes the feature look non-deterministic**

`_maybe_analyze` (`self_improve.py:949`) gates on:

```python
    if len(interactions) % ANALYZE_INTERVAL != 0:      # ANALYZE_INTERVAL = 5
        return
```

`interactions.jsonl` contains **both** user messages and tool calls (`log_user_message` and `log_tool_call` both append to it). A turn that makes three tool calls jumps the counter from 8 to 12, stepping straight over the multiple of 5 — so analysis silently never runs for that stretch.

**Fix:** count user turns explicitly:

```python
    user_turns = sum(1 for i in interactions if i.get("kind") == "user_message")
    if user_turns % ANALYZE_INTERVAL != 0:
        return
```

Adjust the field name to match what `log_user_message` actually writes. Once Phase 3 moves to session-end reflection this heuristic disappears entirely — fix it cheaply now, delete it later.

---

## P1-6 · Memory truncation is silent

**Severity: MED**

`load_memory_index()` (`agent.py:930`) reads all of `MEMORY.md` into every system prompt. `build_system_prompt` then truncates it to `MAX_MEMORY_CHARS` (`:880`). When the index outgrows the budget, memories **disappear from the agent's awareness with no signal to anyone** — the user believes something was remembered, and it silently is not.

**Fix (v1 — honest failure):**

```python
    memory = load_memory_index()
    if memory:
        if len(memory) > MAX_MEMORY_CHARS:
            n_lines = len(memory.splitlines())
            print(f'  {YELLOW}⚠{RESET} {DIM}memory index exceeds the prompt budget '
                  f'({n_lines} entries) — older entries are not being loaded{RESET}')
        memory = _truncate_section(memory, MAX_MEMORY_CHARS, "memory")
        prompt += f"\n\n# Memory index\n{memory}"
```

The real fix is retrieval (Phase 3): score memories against the current message and inject the top few, so prompt size stays flat as memory grows. Phase 1 just stops it losing data quietly.

While here, note the related gap: the individual memory files that `MEMORY.md` links to are **never read automatically** — the model would have to `read_file` them, and nothing in `BASE_PROMPT` (`agent.py:827-836`) tells it that it can. Add one line to the base prompt:

```
- Memory files listed in the memory index can be read with read_file when you need their detail.
```

---

## P1-7 · Deferred to Phase 3 (recorded here so it isn't lost)

- **Global vs project scoping.** `~/.tomas/memory`, `~/.tomas/self-improve` and the session analysis are shared across all projects, so patterns from one leak into another's prompt. Needs the storage redesign.
- **Template tips are noise.** `generate_tips` (`self_improve.py:600-704`) produces human-directed advice ("Consider creating shortcuts or aliases for this tool") that is injected into every prompt at `agent.py:905-910`. It gets deleted in Phase 3. If you want a cheap win now, cap it to 2 tips instead of 5.
- **Privacy.** `log_user_message` stores raw user content in plaintext forever, with no retention limit or opt-out.

---

## Verification

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
```

Then, by hand:

1. Force-generate a skill (`/si` → generate), confirm the file appears in `~/.tomas/self-improve/skills/`, then print `build_system_prompt()` and confirm the skill name is in it. **This is the single most important check in Phase 1.**
2. Write a note via `/note`, then confirm its text appears in the next `build_system_prompt()` — and that no ANSI escape (`\x1b`) appears anywhere in the prompt string.
3. Configure a provider, simulate an update (rename `providers.json` in the source dir), confirm config survives in `~/.tomas/`.
4. Append 10,000 synthetic interactions; confirm turn latency is unchanged and `analyze_patterns` still returns in well under a second.

## Acceptance criteria

- [ ] A skill the agent generated for itself appears in the system prompt on the next turn.
- [ ] Notes written by the agent appear in the system prompt, as plain text, content included.
- [ ] `assert "\x1b" not in build_system_prompt()` passes.
- [ ] Provider config survives a simulated update.
- [ ] With 10k interactions logged, per-turn latency is unchanged from an empty log.
- [ ] Memory index overflow prints a warning instead of silently dropping entries.

## Next

Phase 2 — [the core/UI split](PHASE-2-core-ui-split.md). Do it before Phase 3, so the learning system is testable from day one.
