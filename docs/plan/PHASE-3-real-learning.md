# Phase 3 — Real Learning

**Goal:** replace keyword counting with genuine learning — the agent notices what the user actually wants, remembers it with evidence, and applies it invisibly.
**Effort:** 2-3 weeks.
**Depends on:** Phase 1 (the loop must be closed) and Phase 2 (this must be testable).
**Net effect on codebase size: negative.** Roughly 700 lines deleted, ~380 added.

This is the phase that builds the actual product.

---

## The problem

### It learns by counting words

`analyze_patterns` (`self_improve.py:211`) extracts keywords minus a stop-word list, computes similarity by token overlap (`_similarity_score`, `:146`), and when a counter passes 3 (`PATTERN_TO_SKILL_THRESHOLD`) it fills in a Markdown template. The entire "skill" it produces:

```python
# self_improve.py:450-468
content = f"""---
name: {tool}-usage
description: Guidelines for using the {tool} tool effectively
---

# Using `{tool}` Effectively

*This skill was auto-generated because `{tool}` has been used {count} times.*

## Best Practices

- Always verify the path before calling `{tool}`.
- Use `{tool}` with clear, specific parameters.
- Check the result of `{tool}` before proceeding to the next step.
"""
```

That text is identical for every user, every project, every tool. It contains **zero information** derived from the interaction it supposedly learned from. The tips are the same shape (`:600-704`):

> *"You frequently use `read_file` (12×). Consider creating shortcuts or aliases for this tool."*

That is advice addressed to a human developer. It is injected into the system prompt on every turn (`agent.py:905-910`), where it consumes context and changes nothing about the model's behaviour.

**A keyword counter cannot produce an insight.** It has no access to what was said, what went wrong, or what the user actually wanted. Meanwhile a language model — which you already have in the loop — can read the transcript and say exactly that.

### It ignores the only high-quality signal it has

When the user says *"no, not like that"*, *"I meant PowerShell"*, re-asks the same question, denies a tool call, or immediately edits a file the agent just wrote — that is a **labelled training example**: the agent did X, the user wanted Y. It is free, unambiguous, and it is the single most valuable data in the session.

Nothing in the codebase detects any of it.

### Four stores, two of them write-only

| Store | Written by | Read by |
|---|---|---|
| `~/.tomas/memory/` | `save_memory` tool | index dumped into every prompt |
| `~/.tomas/self-improve/` | pattern analysis | tips only (the noise ones) |
| `~/.tomas/self-notes/` | `/note`, `auto_generate_note` | **nothing** (fixed in Phase 1) |
| `~/.tomas/sessions/` | session manager | `/session continue` |

Three of these are trying to be the same thing.

---

## The design

Four pieces. Each is small; together they replace ~700 lines of heuristics.

### 1 · Reflection — the model is the learner

At session end (or every N turns, off the hot path), send the transcript to a **cheap** model with a strict schema:

```python
# learning/reflect.py

REFLECTION_PROMPT = """You are reviewing a completed session between a user and
an AI agent, to learn how to serve THIS user better next time.

Report only what the transcript actually supports. Do not invent preferences
from a single ambiguous exchange. An empty list is the correct answer when
nothing was learned — most sessions teach nothing, and that is fine.

Return JSON only:
{
  "user_preferences": [
    {"fact": "<durable, specific, about the user or their environment>",
     "confidence": 0.0-1.0,
     "evidence": "<what in the transcript supports this>"}
  ],
  "corrections": [
    {"what_i_did": "...", "what_was_wanted": "...", "lesson": "<one actionable sentence>"}
  ],
  "skill_candidates": [
    {"name": "kebab-case-name",
     "trigger": "<when this should apply>",
     "body": "<concrete guidance, specific to this user's actual workflow>"}
  ],
  "project_notes": [
    {"fact": "<true of this codebase, not of the user>", "evidence": "..."}
  ]
}"""


def reflect_on_session(messages: list, model: str | None = None) -> dict:
    """One cheap API call per session. Returns {} on any failure —
    learning must never break or delay the user's work."""
    if len(messages) < 4:
        return {}
    transcript = render_transcript(messages, max_chars=20_000)
    try:
        resp = call_model(
            model=model or cheapest_available_model(),
            system=REFLECTION_PROMPT,
            messages=[{"role": "user", "content": transcript}],
            max_tokens=1500,
        )
        return json.loads(extract_json(resp))
    except Exception:
        return {}
```

Cost: one small-model call per session. On the free Zen tier, zero.

**Design notes that matter:**

- **Give it permission to learn nothing.** Without that instruction, an LLM asked to extract preferences will always find some — and you get a store full of hallucinated facts. This single sentence is what keeps the system honest.
- **Use the cheap model, not the session model.** Reflection is a summarisation task. Add a `REFLECTION_MODEL` setting defaulting to the smallest model the active provider offers.
- **Separate user facts from project facts.** They have different lifetimes and scopes (see §5).

### 2 · Correction detection — mine the free signal

Two detectors: cheap heuristics that flag *candidates*, and the reflection pass that interprets them.

```python
# learning/corrections.py

CORRECTION_MARKERS = [
    "no,", "not like that", "i meant", "i said", "wrong", "that's not",
    "don't ", "stop ", "actually,", "instead", "no need to", "why did you",
]


def detect_correction_signals(messages: list) -> list[dict]:
    """Flag turns where the user appears to be correcting the agent.
    Heuristics only — the reflection pass decides what the lesson is."""
    signals = []
    for i, msg in enumerate(messages):
        if msg["role"] != "user" or not isinstance(msg.get("content"), str):
            continue
        text = msg["content"].lower()

        if any(m in text for m in CORRECTION_MARKERS):
            signals.append({"kind": "explicit_correction", "index": i,
                            "text": msg["content"][:300]})

        prev_user = previous_user_message(messages, i)
        if prev_user and similarity(text, prev_user.lower()) > 0.7:
            signals.append({"kind": "repeated_request", "index": i,
                            "text": msg["content"][:300]})
    return signals
```

Three more signals available for free, all worth wiring up:

| Signal | Source | Meaning |
|---|---|---|
| Permission denied | `PermissionNeeded` → `deny` (Phase 2) | the agent proposed something unwanted |
| Tool error loop | same tool failing 2+ times consecutively | the agent is stuck, note the approach |
| Agent-written file edited immediately by the user | file mtime after `write_file` | the output was wrong in a specific way |

Feed the signals into the reflection call — *"the user appears to have corrected the agent at these points, focus your analysis there"*. That focuses a small model on exactly the material worth learning from, which is also what makes a small model sufficient.

### 3 · Promotion — evidence before belief

**Never let one session write a permanent rule.** LLM reflection will occasionally hallucinate a preference from an ambiguous exchange; without a gate those become permanent false beliefs about the user, silently applied forever.

```
observed (seen once)  →  candidate (2 sessions)  →  active (3+, confirmed)
                                 │
                                 └─ not re-confirmed in 30 days → decays out
```

```python
# learning/promotion.py

PROMOTE_AT = 3          # sessions of supporting evidence
DECAY_DAYS = 30


def record_observation(kind: str, fact: str, evidence: str, scope: str) -> None:
    """Merge a new observation into the store, or reinforce an existing one."""
    store = load_store(scope)
    existing = find_similar(store, fact, threshold=0.75)
    if existing:
        existing["evidence_count"] += 1
        existing["last_seen"] = time.time()
        existing["evidence"].append(evidence)
        if existing["evidence_count"] >= PROMOTE_AT:
            if existing["status"] != "active":
                existing["status"] = "active"
                emit(LearnedSomething(kind=kind, summary=fact))
    else:
        store.append({
            "kind": kind, "fact": fact, "status": "observed",
            "evidence_count": 1, "evidence": [evidence],
            "first_seen": time.time(), "last_seen": time.time(),
        })
    save_store(scope, store)


def decay(scope: str) -> None:
    """Age out beliefs that stopped being reinforced."""
    cutoff = time.time() - DECAY_DAYS * 86400
    store = [f for f in load_store(scope)
             if f["last_seen"] > cutoff or f["evidence_count"] >= PROMOTE_AT * 2]
    save_store(scope, store)
```

Only `status == "active"` items ever enter a prompt. Explicit user instructions ("always use PowerShell") may enter as `active` immediately — the user said it outright, no inference involved.

### 4 · Retrieval — the piece that makes it scale

Today, memory is *dumped*: all of `MEMORY.md` into every prompt, truncated when it overflows (`agent.py:877-881`). That means prompt cost grows with everything ever learned, until things silently vanish.

**Retrieve instead.** One function, used by memory, notes, skills, and (Phase 4) tool selection:

```python
# learning/retrieval.py

def recall(query: str, k: int = 5, scopes: tuple = ("global", "project")) -> str:
    """Return the k most relevant learned items for this message, as plain text.

    v1 scores with keyword overlap — good enough, and it reuses the existing
    _extract_keywords/_similarity_score. The signature is embedding-ready:
    swap the scorer, change nothing else.
    """
    candidates = []
    for scope in scopes:
        candidates += load_active_facts(scope)
        candidates += load_learned_skills(scope)
        candidates += load_memories(scope)

    q = set(extract_keywords(query))
    scored = []
    for c in candidates:
        overlap = len(q & set(c.get("keywords", []))) / (len(q) or 1)
        recency = recency_boost(c.get("last_seen", 0))
        scored.append((overlap * 0.8 + recency * 0.2, c))

    top = [c for score, c in sorted(scored, key=lambda x: -x[0])[:k] if score > 0.1]
    return render_for_prompt(top)
```

Then in `build_system_prompt`, replace the three separate dump sections (memory index, notes, tips) with **one** retrieval section:

```python
    learned = recall(current_user_message, k=5)
    if learned:
        prompt += f"\n\n# What I've learned about this user and project\n{learned}"
```

This is what keeps the prompt **flat in size** as knowledge grows — the property that makes a memory system viable over years instead of weeks.

> `build_system_prompt()` currently takes no arguments (`agent.py:861`) and is called once per turn at `agent.py:2388`. It needs the current user message to retrieve against — change the signature to `build_system_prompt(user_message: str = "")` and pass it at the call site. Retrieval with an empty query falls back to most-recent, so nothing breaks.

### 5 · Storage consolidation

Four stores become one hierarchy, with one write API and one read API:

```
~/.tomas/
  learned/
    global/
      facts.jsonl            # durable facts about the USER (all projects)
      skills/*.md            # learned skills, real frontmatter, discoverable
    projects/<sha1-of-path>/
      facts.jsonl            # facts about THIS codebase
      meta.json              # {"path": "C:\\...", "last_seen": ...}
  sessions/                  # raw transcripts — unchanged, already works
  providers.json             # moved in Phase 1
```

```python
def remember(kind: str, fact: str, evidence: str = "", scope: str = "global") -> None: ...
def recall(query: str, k: int = 5, scopes: tuple = ("global", "project")) -> str: ...
```

Every subsystem goes through those two functions. The `save_memory` tool becomes a thin wrapper over `remember(kind="explicit", scope=...)`, which is a nice side effect: an explicit user "remember this" and an inferred preference land in the same store with the same retrieval, differing only in `status` and provenance.

**Scoping rule:** facts about *the user* ("prefers short answers", "writes Ukrainian", "uses PowerShell") are global. Facts about *a codebase* ("tests live in tests/", "uses fpdf2") are project-scoped, keyed by a hash of the project path. This fixes the leak where patterns from one project pollute another's prompt.

**Migration:** on first run, import existing `~/.tomas/memory/*.md` as `status: active, kind: explicit` global facts, and existing self-notes as `observed`. Do not import the generated template skills or tips — they contain no information.

---

## What to delete

| Target | Lines | Why |
|---|---|---|
| `generate_tips` + tip templates (`self_improve.py:600-704`) | ~105 | generic advice to a human; replaced by reflection |
| `generate_skill_for_pattern` templates (`:434-552`) | ~120 | identical output for every user |
| Pattern taxonomy in `analyze_patterns` (`:211-390`) | ~180 | keyword counting; the model does this better |
| `_infer_purpose` / `_infer_complexity` / `_infer_tools_needed` / `_infer_stage` (`:827-908`) | ~80 | keyword heuristics; reflection subsumes them |
| `self_notes.py` note-type taxonomy overlap | ~100 | folded into the unified store |
| **Total removed** | **~585** | |
| Added: reflect (~90) + corrections (~70) + promotion (~90) + retrieval (~80) + storage (~50) | **~380** | |

Keep: the JSONL logging primitives, `_extract_keywords`, `_similarity_score` (retrieval reuses them), the `/si` status UI (repointed at the new store), and session transcripts.

---

## Invisible, but inspectable

The requirement is that the user does not see the machinery. That must not mean the user *cannot* see it. An agent that silently accumulates wrong beliefs with no way to inspect or correct them is worse than one that does not learn.

- **Default: silent.** No output when something is learned. Optionally a single dim line, easily disabled.
- **`/si` (and its desktop equivalent) shows everything:** each active fact, its evidence count, when it was last confirmed, and the transcript excerpt that produced it.
- **`/forget <id>`** removes a fact and tombstones it so reflection cannot immediately re-learn it.
- **Incognito mode** (`/private` or `--no-learn`) — nothing is logged or reflected on for that session.
- **Redaction before write.** Strip high-entropy strings (API keys, tokens) from anything persisted. `log_user_message` currently stores raw user content forever, with no retention limit; add a retention window and a redaction pass.

Inspectability is also a genuine selling point over the closed competition — "you can see exactly what your agent has learned about you, and delete any of it" is a feature the big agents do not offer.

---

## Implementation order

1. **Storage layer first** (`learning/store.py`): `remember`/`recall`, the directory layout, migration from the old stores. Everything else depends on it.
2. **Retrieval** — wire `recall()` into `build_system_prompt`, replacing the memory dump. Ship this alone and measure: prompt size should drop and stop growing.
3. **Reflection** — session-end hook, cheap model, strict schema. Log its output without using it for a few days; read what it produces before you trust it.
4. **Promotion** — the evidence gate. Only after you have seen real reflection output.
5. **Correction detection** — the highest-value signal, but it needs the pipeline above to land anywhere.
6. **Delete the heuristics.** Do this last, once the replacement is demonstrably producing better material.
7. **Skill generation from `skill_candidates`** — write real frontmatter into `learned/global/skills/`, which Phase 1 already made discoverable. "The agent improved its own skill" and "the user installed a skill" become the same code path.

---

## Tests

```python
def test_reflection_returns_empty_for_trivial_session(self):
    """The model must be willing to learn nothing — this is what stops
    the store filling with hallucinated preferences."""
    self.assertEqual(reflect_on_session([user("hi"), assistant("hello")]), {})

def test_fact_requires_evidence_before_going_active(self):
    for i in range(PROMOTE_AT - 1):
        record_observation("preference", "prefers PowerShell", f"session {i}", "global")
        self.assertNotIn("prefers PowerShell", recall("how do I list files"))
    record_observation("preference", "prefers PowerShell", "session 3", "global")
    self.assertIn("PowerShell", recall("how do I list files"))

def test_prompt_size_is_flat_in_stored_knowledge(self):
    """The property that makes this viable long-term."""
    base = len(build_system_prompt("list files"))
    for i in range(500):
        record_observation("preference", f"fact number {i}", "synthetic", "global")
    self.assertLess(len(build_system_prompt("list files")), base * 1.5)

def test_project_facts_do_not_leak_across_projects(self):
    record_observation("project", "tests live in tests/", "...", scope="project")
    with project_dir("C:/other/project"):
        self.assertNotIn("tests live in", recall("where are the tests"))

def test_correction_is_detected(self):
    msgs = [user("use bash"), assistant("..."), user("no, I meant PowerShell")]
    kinds = [s["kind"] for s in detect_correction_signals(msgs)]
    self.assertIn("explicit_correction", kinds)

def test_secrets_are_not_persisted(self):
    remember("explicit", "my key is sk-ant-api03-REDACTEDLOOKINGSTRING", scope="global")
    self.assertNotIn("sk-ant-api03", Path(store_path("global")).read_text())
```

## Acceptance criteria

- [ ] A preference stated and reinforced across 3 sessions becomes active and shows up in later prompts.
- [ ] A preference mentioned once does **not**.
- [ ] With 500 stored facts, the system prompt is no more than ~1.5× its empty-store size.
- [ ] Project facts do not appear in another project's prompt.
- [ ] An explicit correction produces a stored lesson within one session.
- [ ] Reflection returns `{}` for a trivial session.
- [ ] `/si` lists every active fact with evidence count and source excerpt; `/forget` removes one permanently.
- [ ] Net line count of `self_improve.py` + new `learning/` package is **lower** than `self_improve.py` alone today.
- [ ] Nothing in the learning path can raise into the user's turn — wrap every entry point.

## Next

Phase 4 — [providers and extensions](PHASE-4-providers-and-extensions.md).
