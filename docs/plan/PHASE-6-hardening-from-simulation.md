# Phase 6 — Hardening from Simulation Evidence

> **Status: implemented (2026-08-03).** All 14 acceptance criteria pass. Test
> count 118 → 184 (`tests/test_tool_layer.py` 42 new, `tests/test_session_integrity.py`
> 22 new, `tests/test_mcp_collision.py` extended); `python test_agent.py` 39/39.
> `python -m tests.simulate checks --offline` reports 29/29 with 0 warnings.
> Implementation notes are at the end of each item under **Implemented**.

**Goal:** fix what 16 real multi-turn sessions actually broke on — sessions that save with the replies missing, a sandbox that locks the agent out of its own state, a shell layer that lies about failure and mangles text, a grep that silently finds nothing, a permission tier that costs a minute per turn, and an MCP router that picks the wrong server.
**Effort:** ~1.5 weeks.
**Depends on:** Phase 2 (core/UI split — the shell and permission changes need to be testable without a terminal) and Phase 3 (learning — this phase deletes the generator Phase 3 orphaned).
**Net effect on codebase size: negative.** ~600 lines deleted (the `self_improve` generator), ~400 added.

Every item below is traced to a specific tool result in `~/.tomas/sessions/*.json` or to a file on disk right now. Nothing here is speculative.

---

## Where the evidence comes from

| Source | What it is |
|---|---|
| `~/.tomas/sessions/` | **16 session JSONs, 786.7 KB, 524 messages, 209 tool calls**, one model (`deepseek-v4-flash-free` via the Zen proxy) |
| `TOMAS_SIMULATION_REPORT.md` | Phase-1 sweep: 8 sessions, bugs 1–3 |
| `TOMAS_SIMULATION_REPORT_2.md` | Phase-2 sweep: 3 ultra-long sessions, bugs 4–6 |
| `TOMAS_SIMULATION_REPORT_3.md` | Phase-3 sweep: 5 sessions (CRUD lifecycle, web, MCP deep-dive, memory, stress), bugs 7–9 |
| `simulation_results.json` | Harness run: 18 checks, 16 PASS, 2 WARN, 0 FAIL |
| `~/.tomas/self-improve/` | 28 generated skills, 5 KB of tips, 49 KB of interaction log |

Measured tool distribution across all 16 sessions:

```
run_command  72   read_file  49   search_code  29   edit_file  18   write_file  18
list_files    9   save_memory  7   fetch_url    4   search_web   3
```

`run_command` is the most-used tool, the highest-risk one, and the least reliable one. That ordering explains most of this phase. `edit_file` jumped from 5 to 18 calls once the Phase-3 sessions did real refactoring — §P6-13 is why.

### Triage order

| Severity | Item | One-line |
|---|---|---|
| **Critical** | P6-11 | Sessions save with user turns and no assistant replies — 10 orphaned turns on disk |
| **Critical** | P6-2 | `run_command` reports failure as success |
| High | P6-12 | `search_code` returns "no matches" when `path` is a file |
| High | P6-1 | Sandbox blocks the agent from reading `~/.tomas` |
| High | P6-7 | MCP routes by first match across 17 servers |
| High | P6-14 | `run_command` decodes output with the system codepage — UTF-8 arrives mangled |
| Medium | P6-3, P6-4, P6-5, P6-13 | Windows shell quirks, permission tiering, context budget, single-site edits |
| Cleanup | P6-6, P6-8, P6-9, P6-10 | Delete the generator, session telemetry, harness, regression tests |

### Two corrections to the reports before using them

**1 · Report 2's self-improvement claim is false.** It states that `build_system_prompt()` "Stage 7 injects active tips". It does not — the injection was deliberately removed during Phase 3:

```python
# agent.py:984-988
# NOTE: the self-improvement tips/session-context block used to be injected
# here. It was template text addressed to a human developer ("Consider
# creating shortcuts or aliases for this tool") that consumed context and
# changed nothing about the model's behaviour. Reflection replaces it; the
# generator code is still in self_improve.py pending deletion.
```

The session that "verified" the loop verified that `get_active_tips()` returns a non-empty list. It does — nothing consumes it. §P6-6 finishes the deletion the comment promises.

**2 · Report 3's session inventory does not match disk.** Every row in its table is wrong, and two sessions did not happen at all:

| Session | Report 3 claims | Actually on disk |
|---|---|---|
| `114827_596109` | 62.4 KB, 12 msgs | 42.7 KB, 48 msgs |
| `115113_d5d238` | 58.1 KB, 12 msgs | 13.1 KB, 22 msgs |
| `121648_d8cc81` | 148.9 KB, 12 msgs | 190.4 KB, 76 msgs |
| `122232_29a204` | 72.3 KB, 12 msgs | 40.2 KB, 18 msgs — **turns 3–6 have no replies** |
| `122837_60bfa9` | 94.7 KB, 16 msgs | **1.4 KB, 8 msgs, zero assistant messages** |

Report 3 describes session 13 running "the full 118 unit test suite", compiling a PDF, and saving a memory key in 364.9 s. The session file contains eight user prompts and nothing else. The report was written from the harness's *intended* turn list, not from what the agent returned. This is not a reporting nit — it is the symptom that P6-11 fixes, and it is why the reports' latency tables should not be trusted as a baseline.

A useful thing did come out of session 11, though: **the agent refactored `agent.py` itself mid-simulation**, extracting `resolve_mcp_tool_conflicts` (`agent.py:253`), `apply_tool_cap` (`:283`), and `is_free_tier_model` (`:299`) out of `main()`, and writing `tests/test_mcp_collision.py`. Those changes are live and shifted every line number in `agent.py` by ~54. Citations below are against the current tree.

---

## P6-11 · Sessions save with the replies missing

**Evidence** — 10 user turns across the corpus are followed immediately by another user turn, with no assistant message between them. Concentrated in the two newest sessions:

```
20260803_122837_60bfa9.json   8 messages, 8 user, 0 assistant, 1,473 bytes
20260803_122232_29a204.json  18 messages, 11 user, 7 assistant — turns 3-6 empty
```

Report 3 attributes a rate-limit event to session 13 (its "bug 7": HTTP 429/502 from the Zen proxy). Retry logic exists and is correct — `core/loop.py:36` sets `MAX_RETRIES = 3`, `is_retryable_error` (`:53`) matches `429`, `rate_limit`, `Too Many Requests`. What is missing is what happens **after** retries are exhausted: the turn produces nothing, the harness moves to the next prompt, and `save_session` writes the transcript as though it were complete.

Nothing in the file marks it as broken. `message_count: 8` is accurate and useless.

The second half of the bug: **`token_usage` is process-global, not per-session.**

```python
# agent.py:149
_session_tokens = {"input": 0, "output": 0, "calls": 0}
```

It is accumulated at `agent.py:1331-1333` and never reset. Sessions 12 and 13 carry byte-identical usage — `{"input": 1640061, "output": 28648, "calls": 80}` — because they ran in one process. Session 13 did no work at all and reports 1.6 M input tokens. Every per-session cost number in all three reports is derived from this field.

**Fix — three parts:**

1. Reset `_session_tokens` when a session starts, or better, move it onto the session state object that Phase 2 introduced so it cannot be global.
2. Record turn outcome, not just turn content. A turn that ends without an assistant message gets an explicit marker:

```python
{"role": "system", "type": "turn_failed",
 "reason": "retries_exhausted", "error": "429 rate_limit", "attempts": 4}
```

3. `save_session` refuses to silently write a transcript whose last message is a `user` message, or whose user count exceeds its assistant count. Either append the failure marker or set `"complete": false` in the metadata. A consumer — the reflection pass in `learning/`, `/session continue`, or a human writing a report — must be able to tell a finished session from an abandoned one.

**Test:** a stub client that raises 429 four times produces a session whose metadata has `complete: false` and a `turn_failed` entry; two sessions run back-to-back in one process have independent `token_usage`.

**Implemented.** `agent.reset_session_state()` / `session_telemetry()` (agent.py); `_failed_turns` recorded in the `agent_loop` shim's `finally`. `session_manager.audit_transcript()` + `save_session(telemetry=...)` write `complete` and `incomplete_reason`. `backfill_completeness()` annotates pre-Phase-6 files and runs once at startup — it marked the two sessions the reports described as finished. Tests: `tests/test_session_integrity.py`.

---

## P6-1 · The sandbox locks the agent out of its own memory

**Evidence** — session `20260803_102520`, `read_file`:

```
Error: path outside project: C:\Users\muaro\.tomas\self-notes\note-20260803_101459-16216a.md
```

The agent then worked around it by shelling out:

```
run_command: del ...\_tmp_create_note.py && type "%USERPROFILE%\.tomas\self-notes\note-....md"
```

Session 11 (`121648`) does the same thing three weeks of design later:

```
run_command: cd /d %USERPROFILE%\.tomas\self-notes && dir /b note-20260803_121538* & echo --- & type note-...md
```

So the sandbox blocked a **read** and the agent replaced it, twice, with **`high`-risk shell commands that also delete files** — strictly worse in every dimension the sandbox exists to protect.

**Cause** — `_safe()` allows exactly one root:

```python
# agent.py:483-489
def _safe(p: Path) -> bool:
    """Ensure the path stays inside the project directory."""
    try:
        p.relative_to(PROJECT_DIR)
        return True
    except ValueError:
        return False
```

Used by all five path tools (`agent.py:498, 512, 520, 567, 606`). But per the plan's own Rule 5, **all user state lives in `~/.tomas/`** — sessions, notes, memory, learned skills. The agent is structurally forbidden from reading anything it knows.

**Fix** — two roots, asymmetric permissions. `~/.tomas/` is readable but not writable by path tools; writes there go through the typed APIs (`save_memory`, `self_notes`, `session_manager`) that own the format.

```python
# agent.py — replace _safe
TOMAS_HOME = (Path.home() / ".tomas").resolve()

def _within(p: Path, root: Path) -> bool:
    return p == root or root in p.parents

def _safe(p: Path, write: bool = False) -> bool:
    """Project dir is read-write. ~/.tomas is read-only: it is written
    through the typed APIs that own each file's schema, never by hand."""
    if _within(p, PROJECT_DIR):
        return True
    return not write and _within(p, TOMAS_HOME)
```

Call sites pass `write=True` from `handle_write_file`, `handle_edit_file`. Error text has to say which rule was hit, or the model retries blindly:

```python
return (f"Error: {path} is outside the project. "
        f"~/.tomas is readable but read-only — use save_memory / self_notes to write there.")
```

**Test:** `read_file` on a real `~/.tomas/sessions/*.json` succeeds; `write_file` to the same path is refused; `read_file` on `C:\Windows\System32\drivers\etc\hosts` is refused.

**Implemented.** `_safe(p, write=False)` + `_outside_project_error()` in agent.py; all five path tools updated, writes pass `write=True`. Tests: `TestSandbox`.

---

## P6-2 · `run_command` reports failure as success

**Evidence** — the single most expensive defect in the corpus. Session `20260803_105901`, turn 8: `957.2 seconds` on one `run_command` chain, debugging a PDF crash. The agent could not tell a failing run from a passing one without reading the traceback text itself.

```python
# agent.py:592-597
output = ""
if result.stdout: output += result.stdout
if result.stderr: output += f"\nSTDERR:\n{result.stderr}"
if not output.strip():
    output = f"Command completed with exit code {result.returncode}"
```

`result.returncode` is **only surfaced when the command printed nothing**. A command that exits 1 while writing to stdout is indistinguishable from success.

| Session | Command | Real outcome |
|---|---|---|
| `105901` | `python -c "import ultra_math_engine as m; print(m.dot(...))"` | `AttributeError: no attribute 'dot'` — the agent had written `dot_product`, then called `dot` |
| `105901` | `python pdf_report_skill.py` | `FPDFException: Not enough horizontal space` |
| `105901` | `python -u -c "import pdf_report_skill ..."` | same, after the agent added `-u` **by hand** |
| `121648` | `python -m unittest tests.test_mcp_collision -v 2>&1` | agent appended `2>&1` itself to force stderr into the visible stream |

That last row is new in Phase 3 and worth noting: the agent has started defensively appending `2>&1` and `| tail -N` to its own commands. It is compensating for a tool that will not tell it what happened.

**Fix** — always report the exit status, and label the streams:

```python
def handle_run_command(params: dict) -> str:
    ...
    parts = []
    if result.stdout.strip():
        parts.append(result.stdout.rstrip())
    if result.stderr.strip():
        parts.append(f"[stderr]\n{result.stderr.rstrip()}")
    body = "\n".join(parts) or "(no output)"
    status = "ok" if result.returncode == 0 else f"FAILED (exit {result.returncode})"
    return f"[exit {result.returncode} — {status}]\n{body}"
```

**Test:** `python -c "import sys; print('out'); sys.exit(3)"` returns a string containing both `out` and `exit 3`.

**Implemented.** `handle_run_command` returns `[exit N — ok|FAILED]` first, labels `[stderr]`, and the tool description says so, so the model stops appending `2>&1`. Tests: `TestRunCommand`.

---

## P6-12 · `search_code` finds nothing when you point it at a file

**Evidence** — session `20260803_121648`, turn 2. The user asked to search `agent.py` for `mcp_`. Three calls to do one grep:

```
search_code {"pattern": "mcp_", "path": "...\agent.py"}          → len=28   ("No matches for pattern: mcp_")
search_code {"pattern": "mcp_", "path": "...", "file_glob": "agent.py"}  → len=3019 (correct)
run_command {"command": "cd /d ... && findstr /n /c:\"mcp_\" agent.py"}  → len=1868 (agent no longer trusted the tool)
```

The first call is the natural reading of the parameter. It returns a confident, wrong, *negative* answer — the worst possible failure mode for a search tool, because the model has no reason to doubt it. Note the third line: having been told "no matches" once, the agent escalated to a `high`-risk shell command even after the second call succeeded.

**Cause** — `handle_search_code` assumes `path` is a directory:

```python
# agent.py:602-609
path = _resolve(params.get("path", "."))
...
glob_iter = path.rglob(file_glob) if file_glob else path.rglob("*")
```

`Path.rglob()` on a file yields an empty iterator. No exception, no warning — the loop body never runs and the function returns `f"No matches for pattern: {pattern}"`.

**Fix** — accept a file, and never report a clean negative for a path that could not be searched:

```python
if path.is_file():
    candidates = [path]
elif path.is_dir():
    candidates = path.rglob(file_glob) if file_glob else path.rglob("*")
else:
    return f"Error: path does not exist: {path}"
```

While here, make the 50-match ceiling honest. It currently returns early with `"... (50 matches, truncated)"` — the agent cannot tell whether there are 51 matches or 5,000, and cannot ask for the rest. Report the true count and support an `offset`.

**Test:** `search_code(pattern="def ", path="agent.py")` returns matches; a path that does not exist returns an error, not "no matches".

**Implemented.** `handle_search_code` branches on `is_file` / `is_dir` / neither, reports the true total, and supports `offset` (both exposed in the schema). Invalid regex is now an error rather than a traceback. Tests: `TestSearchCode`.

---

## P6-14 · Shell output is decoded with the system codepage

**Evidence** — session `20260803_121648`, turn 6. The agent wrote a UTF-8 self-note, then read it back through the shell:

```
run_command: type note-20260803_121538-cb4549.md
→ # MCP Subsystem Integrity вЂ" Verified End-to-End
  ... config from `~/.claude.json` в†' `mcpServers`
```

`—` and `→` arrived as `вЂ"` and `в†'` — UTF-8 bytes decoded as cp1251, this machine's default codepage. The agent then summarised the note *from the mangled text*.

**Cause** — `subprocess.run(..., text=True)` with no explicit encoding (`agent.py:585-588`) uses `locale.getpreferredencoding()`. Any tool output containing a non-ASCII character is silently corrupted, and the corruption is unrecoverable by the time the model sees it. The codebase's own source files are full of `—`, `→`, and box-drawing characters, so this fires constantly.

**Fix:**

```python
result = subprocess.run(
    cmd, shell=True, capture_output=True,
    encoding="utf-8", errors="replace",
    timeout=timeout, cwd=str(PROJECT_DIR),
)
```

On Windows, also set `PYTHONIOENCODING=utf-8` in the child environment so python subprocesses emit UTF-8 rather than the console codepage.

There is a matching read-side issue worth checking at the same time: `handle_read_file` uses `errors="replace"` (`agent.py:503`), which is why session `095814` shows `Session Manager � saves, loads...`. That one is cosmetic — the byte really was undecodable — but the two together mean the agent has never once seen this repo's own punctuation correctly.

**Test:** `run_command('python -c "print(\'—→\')"')` returns `—→`.

**Implemented.** `subprocess.run(..., encoding="utf-8", errors="replace")` plus `PYTHONIOENCODING=utf-8` in the child environment. Tests: `test_run_command_utf8_roundtrip`.

---

## P6-3 · Windows shell quirks the agent had to discover three times

**Evidence** — the agent independently rediscovered stdout buffering in two separate sessions and manually inserted `-u`. Report 1 attributes a 395-second turn to multi-line `python -c` payloads mangled by `cmd.exe`. Report 3's "bug 9" is the third instance: nested quotes.

Session `122232` turn 2, an inline note-creation with nested single quotes inside double quotes:

```
run_command: python -c "import self_notes; nid = self_notes.create_note(title='Memory Indexing System', content='# Memory Indexing System\n\n...')"
→ len=37
```

Session `121648` turn 6, the agent gave up on inline entirely and wrote a scratch file instead:

```
write_file  _create_note.py
run_command python _create_note.py 2>&1 && del _create_note.py
```

That workaround is the right instinct and the wrong location — see below.

**Cause** — `handle_run_command` passes the string straight to `shell=True` with no platform handling (`agent.py:585-588`).

**Fix** — normalise in one place, at the boundary:

```python
# agent.py — top of handle_run_command, after BLOCKED_PATTERNS
if sys.platform == "win32":
    # 1. Unbuffered: cmd.exe swallows stdout of short-lived python processes.
    cmd = re.sub(r'\bpython(\.exe)?\s+-c\b', r'python\1 -u -c', cmd)
    # 2. Multi-line or nested-quote payloads: cmd.exe eats them. Round-trip via a file.
    m = re.search(r'python(?:\.exe)?\s+(?:-u\s+)?-c\s+"(.*)"\s*$', cmd, re.S)
    if m and ("\n" in m.group(1) or "'" in m.group(1)):
        tmp = Path(tempfile.mkdtemp()) / "_exec.py"
        tmp.write_text(m.group(1), encoding="utf-8")
        cmd = f'"{sys.executable}" -u "{tmp}"'
```

The temp file goes in `tempfile.mkdtemp()`, **not** the project directory. Report 1's proposed fix wrote `_temp_exec_wrapper.py` into `PROJECT_DIR`; the corpus shows why that is wrong. Scratch files the agent left in the repo root across sessions: `_tmp_create_note.py`, `_debug_pdf.py`, `_create_note.py`, `_verify_cap.py`. Each one cost a follow-up `del`, and report 3's "bug 8" is the consequence — a scratch file named `test_cap.py` in the project root is picked up by `unittest discover -s tests -p "test_*.py"` and run as a real test.

Doing this at the tool boundary makes report 3's bug 8 disappear rather than requiring the naming convention it proposes.

**Test:** a `python -c` with an embedded newline executes both statements; one with nested single quotes executes correctly; the project directory is unchanged after the call.

**Implemented.** `_normalise_windows_command()` injects `-u` and round-trips multi-line or nested-quote payloads through `tempfile.mkdtemp()`, cleaned in a `finally`. Tests: `test_multiline_python_c_via_tempfile`, `test_nested_quotes_via_tempfile`, `test_no_temp_files_left_in_project`.

---

## P6-13 · `edit_file` forces one call per edit site

**Evidence** — session `20260803_114827`, turn 3. One refactor — replace every `print()` with a `log()` helper — took **nine sequential `edit_file` calls**, each with a hand-built unique context window:

```
edit_file ×9   (old_string values differ only by the surrounding lines)
search_code    (verify no print( remains)
run_command    (re-run the module)
```

At report 2's measured `edit_file` mean of 75.4 s, that turn is ~11 minutes of wall clock for a mechanical substitution. `edit_file` was called 18 times across the corpus; half of them are in this one turn.

**Cause** — deliberate, and half-right:

```python
# agent.py:525-528
count = content.count(old)
if count == 0: return f"Error: old_string not found in {path}"
if count > 1:  return f"Error: old_string matches {count} locations; be more specific."
```

Refusing an ambiguous edit is correct. Offering no way to say "yes, all of them" is not — it forces the model to synthesise N disambiguating contexts, and each one is a chance to get the whitespace wrong.

**Fix** — add `replace_all: bool = False`. When true, replace every occurrence and report the count. Keep the `count > 1` error as the default so ambiguity still fails loudly.

Also consider a batched form — a list of `{old_string, new_string}` applied atomically to one file, with the whole batch rejected if any element fails to match. That turns the session-9 refactor into one call and one permission prompt instead of nine.

**Test:** `replace_all=True` on a file with 9 occurrences reports `9 replacements`; the default still errors on an ambiguous match.

**Implemented.** `replace_all` added to `handle_edit_file` and to the tool schema; the ambiguity error now names the escape hatch. The batched form was not built — `replace_all` covers the observed case. Tests: `TestEditFile`.

---

## P6-4 · Permission tiering costs ~30–100 s of human wait per turn

**Evidence** — measured average turn: **132.6 s** in session `105901`, **155.4 s** in `111434`, **255.8 s** in `121648` (report 3's own figure, its worst session). `run_command` was called **72 times** across the corpus. Every one of them stopped for a prompt:

```python
# agent.py:457-468
RISK_LEVELS: dict[str, str] = {
    "read_file": "low",  "list_files": "low",  "search_code": "low",
    "edit_file": "medium", "write_file": "medium",
    "run_command": "high",   # ← every command, regardless of what it does
    ...
}
```

`git status`, `python -m unittest`, and `rm -rf` share one tier. In `auto` mode nothing about `run_command` is auto-approvable, so the mode that exists to reduce friction does not reduce it for the tool used most. Report 3's third recommendation asks for exactly this, framed as YOLO-mode whitelist rules; doing it in the risk classifier is better, because it applies in every mode rather than only the one that already approves everything.

**Fix** — the risk of `run_command` is a property of the *command*, not the tool. Classify at call time:

```python
# agent.py
READONLY_CMD = re.compile(
    r'^\s*(git\s+(status|log|diff|show|branch)|'
    r'(\.venv[\\/]Scripts[\\/])?python(\.exe)?\s+-m\s+(unittest|pytest)|'
    r'pytest|dir|ls|type|cat|echo|where|which|findstr|pip\s+(list|show|freeze))\b')

def risk_for(name: str, params: dict) -> str:
    if name == "run_command":
        cmd = params.get("command", "")
        if any(sep in cmd for sep in ("&&", "||", ";", "|", ">", "&", "del ", "rm ")):
            return "high"          # chaining defeats the classifier — do not try
        return "low" if READONLY_CMD.match(cmd) else "high"
    return RISK_LEVELS.get(name, "high")
```

The chaining bail-out is deliberate and non-negotiable. The corpus contains `del ... && type ...` (a read dressed as a delete), `python _create_note.py 2>&1 && del _create_note.py`, and `dir /b note-* & echo --- & type note-...` — note the single `&`, which `cmd.exe` treats as a separator and a naive classifier would not.

Wire it through the permission call site (`agent.py:892`, and the `risk_of` lambda at `agent.py:1300`) so both the direct loop and the Phase-2 core path use one classifier.

**Test:** `git status` → `low`; `git status && rm -rf build` → `high`; `dir /b x & del y` → `high`; `python -m unittest discover` → `low`; `python setup.py install` → `high`.

**Implemented.** `risk_for(name, params)` in agent.py, wired into both permission call sites; `AgentState.risk_of` and `core/loop.py` now pass the args through. Mutating verbs (`del`, `rm`, `curl`, …) are high even unchained. Tests: `TestRiskClassifier`.

---

## P6-5 · Context budget is spent on generated filler

Measurements against the current tree:

| Measurement | Value |
|---|---|
| `skills_manager.build_skills_section()` output | **25,326 chars** |
| `MAX_SKILLS_CHARS` (`agent.py:938`) | 4,000 |
| `MAX_TOTAL_SYSTEM_PROMPT` (`agent.py:939`) | 20,000 |
| Skills discovered | 72, of which **28 are auto-generated** |
| Largest single `read_file` result in the corpus | **24,766 chars** (`mcp_manager.py`, session `121648`) |
| Largest input-token session | **1,579,102 tokens over 6 turns** (session `121648`) |

The skills section alone is larger than the entire system prompt cap, so `_truncate_section` cuts 84% of it — at a character offset, mid-entry, with no notion of which skills matter. The 28 generated entries are in that list because `skills_manager.py:19,26` puts the legacy generator directory on the discovery path:

```python
LEGACY_LEARNED_SKILLS_DIR = Path.home() / ".tomas" / "self-improve" / "skills"
```

Sample of what is occupying that budget — actual files on disk:

```
sequence-read_file-read_file.md      "When starting with read_file, consider following up with read_file."
topic-using.md                       "When the user asks about 'using', be thorough and specific."
topic-code.md
topic-save_session'.md               ← note the apostrophe in the filename
```

The 1.58 M input tokens in session 11 are the other half of this. Six turns, 73 tool calls, whole-file reads of `mcp_manager.py` (24.7 KB) and `self_notes.py` (16.7 KB) resent on every subsequent turn.

**Fixes:**

1. Drop `LEGACY_LEARNED_SKILLS_DIR` from `SKILL_DIRS` (§P6-6 deletes the generator that fills it).
2. Cap `read_file` output by characters, not just lines. `handle_read_file` (`agent.py:495-507`) defaults to `limit=2000` *lines* with no byte ceiling. Add a 20,000-char cap with an explicit `[truncated at line N — re-read with offset=N]` footer, so truncation is recoverable rather than silent.
3. Budget the skills section by whole entries, sorted by relevance to the turn, instead of slicing a string at 4,000 chars.
4. Age out large tool results from history. A 24 KB file dump is worth full fidelity on the turn it was requested and a one-line stub five turns later.

**Test:** `build_skills_section()` returns ≤ `MAX_SKILLS_CHARS` and never ends mid-entry; a `read_file` on a 100 KB file returns ≤ 20 KB and names the line to resume from.

**Implemented.** `build_skills_section(max_chars=...)` budgets by whole entries with a `(+N more)` line; `LEGACY_LEARNED_SKILLS_DIR` dropped from `SKILL_DIRS`; `MAX_READ_FILE_CHARS = 20000` with a resumable footer. Measured 25,326 → 3,550 chars. Item 4 (ageing large tool results out of history) was **not** done — it needs the history rewriting Phase 2 owns. Tests: `TestContextBudget`.

---

## P6-6 · Delete the pattern generator (finish what Phase 3 started)

The generator has been dead code since Phase 3 removed its only consumer, but it still **runs every 5 user turns** (`_maybe_analyze`, `self_improve.py:1010-1020`) and still writes to disk. Current state of `~/.tomas/self-improve/`: 28 skills, 5 KB `tips.json`, 49 KB `interactions.jsonl`.

It is also actively misinforming the agent. Session `122232` turn 1 — the agent saved a memory key documenting how recall works, and wrote:

> *"Self-improvement loop: `get_active_tips()` returns un-applied tips ... injected each turn"*

That is now persisted in `~/.tomas/memory/` as a fact. It is false (see the correction above). The dead generator is generating false beliefs about itself.

Three independent defects, none worth fixing because the whole thing goes:

**a. Filenames are not sanitised.** `_pattern_to_skill_name` (`self_improve.py:473-486`) interpolates a raw keyword into a path:

```python
elif ptype == "topic":
    return f"topic-{pattern.get('keyword', 'keyword')}"
```

On disk that produced `topic-save_session'.md`. An apostrophe is survivable; a keyword containing `:` or `/` — both common in this corpus, which is full of `file:line` references — raises `OSError` on Windows inside a background analysis pass.

**b. Tips accumulate forever.** `get_active_tips()` filters on `applied` (`self_improve.py:767-769`), and `mark_tip_applied` (`:772`) **is called from nowhere in the codebase**. Every tip ever generated is permanently "active". `tips.json` already holds near-duplicates from consecutive passes: *"You frequently use `read_file` (15×)"* and *"...(17×)"*.

**c. The topics are stop-words.** `topic-using`, `topic-code`, `topic-search` are not topics; they are the residue of frequency counting over a stop-word list that was never tuned.

**Fix** — delete `analyze_patterns`, `generate_skill_for_pattern`, `generate_skills_for_all_ready_patterns`, `_pattern_to_skill_name`, the tips API, and `_maybe_analyze`. Keep `interactions.jsonl` — `learning/` consumes it. Ship a one-shot migration that removes `~/.tomas/self-improve/skills/` and `tips.json`, because the junk is on every existing user's disk, not just in the repo. Audit `~/.tomas/memory/` for keys asserting the old behaviour.

Keep `/self-improve tips` as a command only if it reads from `learning/`; otherwise remove it too. **One mechanism per job** (plan Rule 3) — `learning/` is the mechanism.

**Implemented.** 536 lines deleted from self_improve.py. `/self-improve skills|tips|patterns` now point at `facts` / `reflect` instead of showing nothing. `migrate_remove_generated()` runs at `init()` and removed 51 skill files, `tips.json`, `patterns.json`, `skill-registry.json`. `_ensure_dirs` no longer recreates the deleted directories — the strict harness caught that on its first run. The two memory keys and one self-note asserting the old behaviour were corrected in place. Tests: `Learning` section of `tests/simulate.py`.

---

## P6-7 · MCP routes by first match across servers

**Evidence** — `simulation_results.json` records **17 configured MCP servers**, including `microsoft-playwright-mcp`, `playwright`, and `chromedevtools-chrome-devtools-mcp`. All three expose `take_screenshot`.

Session 11 was devoted to this subsystem and the agent came away with the wrong conclusion. It wrote `tests/test_mcp_collision.py` (13 passing tests), saved memory key `mcp-routing-architecture`, and filed a self-note titled *"MCP Subsystem Integrity — Verified End-to-End"*. What it actually verified is the **built-in vs MCP** collision path — `resolve_mcp_tool_conflicts` (`agent.py:253-281`), which prefixes `read_file` → `mcp_read_file`. That path works and now has tests.

The **server vs server** path is untouched:

```python
# mcp_manager.py:398-403
def call_tool(self, tool_name: str, arguments: dict) -> str:
    for server in self.servers.values():
        for t in server.tools:
            if t["name"] == tool_name:
                return server.call_tool(tool_name, arguments)
```

First match in `dict` insertion order wins. `get_server_for_tool` (`:407`) has the same bug, so even diagnostics agree with the wrong answer. And `_all_tools` (`:369, 388`) is a flat list — the same name is appended once per server, so the API payload contains **duplicate tool names**, which is a protocol error independent of routing.

The self-note's "verified end-to-end" claim should be corrected when this lands; leaving it in `~/.tomas/self-notes/` means the next reflection pass reads it as established fact.

**Fix** — namespace on collision, inside `MCPManager` where the ownership information lives:

```python
# mcp_manager.py — in discover_and_connect
seen: dict[str, str] = {}          # tool name -> first server that claimed it
self._owner: dict[str, tuple[str, str]] = {}   # exposed name -> (server, original)

for t in server.tools:
    original = t["name"]
    exposed = original if original not in seen else f"mcp_{name}_{original}"
    seen.setdefault(original, name)
    tool = self._to_anthropic_tool(t)
    tool["name"] = exposed
    self._all_tools.append(tool)
    self._owner[exposed] = (name, original)

def call_tool(self, tool_name, arguments):
    if tool_name not in self._owner:
        return f"Error: MCP tool '{tool_name}' not found on any connected server."
    server_name, original = self._owner[tool_name]
    return self.servers[server_name].call_tool(original, arguments)
```

Prefixing only the *loser* keeps the common case stable — one server owning a name keeps that name. Extend `tests/test_mcp_collision.py` rather than starting a new file; its `FakeMCPManager` stub is already shaped for this.

**Interaction with Phase 4:** P4-8 replaces arbitrary truncation with selection. `apply_tool_cap` (`agent.py:283-296`) currently keeps `mcp_tools[:keep]` — insertion order — and the free-tier cap is **32 tools** (`is_free_tier_model`, `agent.py:299`) against 17 servers' worth. The whole corpus was produced under a config where most MCP tools were silently dropped, which is also why no session ever exercised a real cross-server collision. Do P6-7 first (correct names), then P4-8 (correct selection).

**Test:** two stub servers both exposing `take_screenshot` → exposed names are `take_screenshot` and `mcp_<second>_take_screenshot`; calling each reaches the right server; `[t["name"] for t in mgr.tools]` has no duplicates.

**Implemented.** `MCPManager._owner` maps exposed name → (server, original); `discover_and_connect` namespaces the second claimant only; `call_tool`/`get_server_for_tool` are O(1) lookups passing the original name to the server. Three tests in `tests/test_mcp_collision.py` that encoded first-wins were rewritten to drive the real `discover_and_connect` path.

---

## P6-8 · Sessions record nothing you can debug with

`save_session` (`session_manager.py:129-190`) stores `message_count`, `token_usage`, and the message list. It does not store *when* anything happened.

Consequences visible in the corpus:

- Every latency figure in all three reports came from the harness scripts, which time each turn (`run_ultra_long_sessions.py:69-72`) and then **throw the numbers away** — printed, never written into the session JSON. The reports' tables are unreproducible from the saved sessions, which is how report 3 ended up publishing timings for a session that produced nothing.
- `token_usage` is process-global (P6-11), so the per-session figures are wrong in both directions.
- The 957-second turn cannot be attributed to a specific tool call from the session file alone.

**Fix** — record per-tool-call outcomes at the point where they are already known, in the agent loop:

```json
"complete": true,
"turn_metrics": {"total_duration_sec": 1590.84, "turn_timings": [25.1, 114.0, ...]},
"tool_log": [
  {"turn": 2, "tool": "write_file", "exit": 0, "duration_sec": 25.1},
  {"turn": 8, "tool": "run_command", "exit": 1, "duration_sec": 957.2,
   "error": "FPDFException: Not enough horizontal space"}
]
```

This is the schema report 2 proposed and report 3 asked for again. The reason to build it is P6-2 — once `run_command` reports its exit code, the log has something truthful to record. Keep it to timing and status. Do **not** duplicate tool arguments or results into it; they are already in `messages`, and duplication is how a 6-turn session reached 190 KB.

**Test:** a two-turn session round-trips through `save_session`/`load_session` with `tool_log` intact; a failing tool call appears with a non-zero `exit`.

**Implemented.** `on_tool_call` widened to `(name, args, preview, duration_ms, ok)`; `_record_tool_call` parses the new `[exit N ...]` prefix. `turn_metrics` and `tool_log` are written by `save_session`. Arguments and results are deliberately not duplicated into the log.

---

## P6-9 · The harness tests functions that do not exist, and there are now five of it

`simulation_results.json` — 16 PASS, 0 FAIL, and 2 WARNs that are both the harness calling a name the codebase does not have:

```
"Headless Browser Interaction (fetch_browser)"  WARN
  module 'agent' has no attribute 'handle_fetch_browser'
"PDF Document Generation & Inspection"          WARN
  module 'pdf_report_skill' has no attribute 'generate_pdf_report'
```

The real names are `fetch_url_with_browser` (`HANDLERS`, `agent.py:804`) and `generate_ai_news_pdf` (`pdf_report_skill.py:176`). Both features work; the harness reports WARN and moves on. A harness that degrades to WARN on `AttributeError` cannot distinguish "not implemented" from "misspelled", which means **a green run proves nothing**. Report 3 nonetheless lists `fetch_browser` as "Success — Handless Chrome DOM parsing via Playwright integration" in its coverage matrix; no session in the corpus contains a single `fetch_url_with_browser` call.

**Fix** — resolve every entry point once, up front, and fail the run if a name is missing. `getattr(mod, name)` with no default; `AttributeError` is a FAIL, not a WARN. WARN stays reserved for genuinely optional environment (no Playwright browser installed, no network).

**Additionally:** the repo root held **five** harness scripts doing one job with divergent conventions. They also polluted their own results: session `114827` turn 4 searched for `temp_lifecycle_test` and the top hits were `run_phase3_sessions.py:106-111` — the harness's own prompt list.

**Implemented.** All five plus `verify_self_improve_loop.py` deleted, replaced by `tests/simulate.py` with `checks` and `sessions --turns N` subcommands and the corpus in `tests/session_prompts.py`. `resolve_entry_points()` runs before any mode and fails with exit 2 — re-adding `agent.handle_fetch_browser` now produces `did you mean: handle_fetch_url_with_browser?` instead of a WARN.

---

## P6-10 · Regression tests for bugs 1–9

Plan Rule 4: *every bug fixed gets a test*. Session 11 established the precedent by writing `tests/test_mcp_collision.py` unprompted; extend that file rather than duplicating its fixtures.

`tests/test_tool_layer.py`:

| Test | Guards |
|---|---|
| `test_read_tomas_home_allowed` | P6-1 / bug 2 |
| `test_write_tomas_home_refused` | P6-1 |
| `test_exit_code_surfaced_with_stdout` | P6-2 |
| `test_search_code_accepts_file_path` | P6-12 |
| `test_search_code_missing_path_errors` | P6-12 |
| `test_run_command_utf8_roundtrip` | P6-14 |
| `test_windows_python_c_unbuffered` | P6-3 / bug 6 |
| `test_multiline_python_c_via_tempfile` | P6-3 / bug 1 |
| `test_nested_quotes_via_tempfile` | P6-3 / bug 9 |
| `test_no_temp_files_left_in_project` | P6-3 / bug 8 |
| `test_edit_file_replace_all` | P6-13 |
| `test_readonly_command_is_low_risk` | P6-4 / bug 3 |
| `test_chained_command_stays_high_risk` | P6-4 |
| `test_single_ampersand_is_high_risk` | P6-4 |
| `test_read_file_char_cap` | P6-5 |
| `test_bullet_wrap_at_right_margin` | bug 4 — **already fixed** in `pdf_report_skill.py:173-178`, untested |

`tests/test_session_integrity.py`:

| Test | Guards |
|---|---|
| `test_incomplete_session_marked` | P6-11 |
| `test_token_usage_is_per_session` | P6-11 |
| `test_tool_log_roundtrip` | P6-8 |

`tests/test_mcp_collision.py` (extend):

| Test | Guards |
|---|---|
| `test_cross_server_tool_collision` | P6-7 / bug 5 |
| `test_no_duplicate_names_in_tool_list` | P6-7 |

Bug 4 deserves a note: the agent found and fixed it itself during session `105901` — wrote `_debug_pdf.py`, traced `multi_cell` to `x=208.00` against `r_margin=10.0`, and edited the source. The fix is live. It has no test, which means the next `fpdf2` upgrade silently reintroduces it. Report 3 lists it as re-verified in session 13; session 13 has no assistant messages, so it was not.

Report 3's "bug 7" (rate limiting) needs no new retry code — `core/loop.py:36-66` already handles it correctly. What it needs is P6-11, so that exhausted retries are visible rather than silent. Its proposed `agent_loop_with_fallback` mutates `os.environ["ANTHROPIC_BASE_URL"]` at runtime and re-inits the client mid-session; that belongs in Phase 4's provider layer (P4-3, "degrade, never fail"), not bolted onto the loop.

---

## Verification

```bash
python -m unittest discover -s tests -p "test_*.py"

# P6-5: measured, not asserted by eye
python -c "import skills_manager as s; print(len(s.build_skills_section()))"   # <= 4000

# P6-7: no duplicate tool names reach the API
python -c "from mcp_manager import MCPManager; m=MCPManager(); m.discover_and_connect(); \
n=[t['name'] for t in m.tools]; assert len(n)==len(set(n)), 'duplicates'"

# P6-11: no session on disk has orphaned user turns
python -c "
import json,glob,os
bad=[]
for f in glob.glob(os.path.expanduser('~/.tomas/sessions/*.json')):
    d=json.load(open(f,encoding='utf-8')); r=[m['role'] for m in d['messages']]
    if any(r[i]=='user' and r[i+1]=='user' for i in range(len(r)-1)) and d.get('complete',True):
        bad.append(os.path.basename(f))
print('unmarked incomplete:', bad)"

# P6-6: the junk is gone
python -c "import pathlib; p=pathlib.Path.home()/'.tomas/self-improve/skills'; print(list(p.glob('*')) if p.exists() else 'removed')"

# P6-9: harness fails loudly
python -m tests.simulate --turns 3       # expect 0 WARN from AttributeError
```

Then re-run one long goal-driven session and compare against the corpus baseline:

| Metric | Baseline | Target |
|---|---|---|
| Avg turn | 132.6 s (`105901`) / 255.8 s (`121648`) | < 60 s |
| Worst turn | 957.2 s | < 180 s |
| Calls to complete one 9-site refactor | 11 (`114827` turn 3) | 2 |
| Calls to grep one file | 3 (`121648` turn 2) | 1 |
| `read_file` refusals on `~/.tomas` | 2 | 0 |
| Temp files left in project root | 4 | 0 |
| Orphaned user turns on disk | 10 | 0 unmarked |
| Skills-section chars | 25,326 | ≤ 4,000 |

---

## Acceptance criteria

1. A session whose turn fails is saved with `complete: false` and a `turn_failed` marker; `token_usage` is per-session, not per-process.
2. `read_file` reads any file under `~/.tomas/`; `write_file` and `edit_file` refuse it with an error naming the typed API to use instead.
3. Every `run_command` result begins with its exit code. A command that exits non-zero while printing to stdout is reported as FAILED.
4. `search_code` accepts a file path, and a path that cannot be searched returns an error rather than "no matches".
5. Shell output containing `—` or `→` round-trips intact.
6. On Windows, `python -c` runs unbuffered; multi-line and nested-quote payloads execute correctly, leaving no file in the project directory.
7. `edit_file` supports `replace_all`, and the ambiguous-match error remains the default.
8. `git status` and `python -m unittest` execute without a prompt in `auto` mode; anything containing a shell separator — including a single `&` — still prompts.
9. `build_skills_section()` is under `MAX_SKILLS_CHARS` without mid-entry truncation, and `read_file` output is character-capped with a resumable footer.
10. `self_improve.py`'s pattern/tip/skill generator is deleted, its output directory is migrated away on first run, and `learning/` is the only learning mechanism.
11. Two MCP servers exposing the same tool name both remain callable, and the tool list sent to the API contains no duplicate names.
12. Session JSON carries `turn_metrics` and a `tool_log` with per-call duration and exit status.
13. The simulation harness fails — not warns — on a missing entry point, and the five scripts are one script under `tests/`.
14. All three new/extended test files pass, covering bugs 1–9.

---

## Next

P6-11 is a prerequisite for trusting any future simulation report, including the three this phase is built on — until incomplete sessions are marked, every generated evaluation risks describing work that did not happen. P6-7 unblocks Phase 4's P4-8 (tool selection over truncation); selection needs unambiguous names first. P6-8's `tool_log` gives Phase 3's reflection a structured failure signal it currently has to infer from transcript prose. P6-6 removes the last of the two-mechanisms-for-one-job debt that Phases 1 and 3 left behind — and stops the dead generator from writing false claims about itself into the memory store.
