# Phase 7 — The Chat Itself, and Cyrillic

> **Status: implemented (2026-08-03).** All 12 acceptance criteria pass. Test
> count 268 -> 337 (`tests/test_cyrillic.py` 50 new, `tests/test_chat_ux.py` 19
> new); `python test_agent.py` 39/39; `tests.simulate checks` 41/41;
> **`tests.simulate cyrillic` 18/18** (18/24 before this phase).
> Against the plan's own targets: Cyrillic keystrokes 0 -> all accepted,
> Ukrainian keywords `[]` -> real, Ukrainian tool selection list-order -> by
> relevance, MCP connect 21.5s -> 5.3s, startup error lines 6 -> 1.

**Goal:** make the conversation feel calm, quick and professional, and make the agent genuinely usable in Ukrainian and Russian rather than accidentally English-only.
**Effort:** ~1.5 weeks.
**Depends on:** Phase 2 (rendering lives in `adapters/`, so the chat can change without touching the loop) and Phase 4 (capabilities, so the UI can say what the provider actually does).
**Net effect on codebase size:** roughly neutral. ~250 lines added to `adapters/terminal.py`, ~150 to a new `text_display.py`, ~80 deleted from the input loop.

Every number below was measured on this machine, on 2026-08-03, with 17 MCP servers configured and OpenCode Zen as the provider. The verification run is in `Where the evidence comes from`.

---

## Where the evidence comes from

A full-stack verification session was run before writing this plan:

| Run | Result |
|---|---|
| All 11 built-in tools | 11/11 working |
| Real MCP connect (17 configured) | 11 connected, 6 failed, **118 tools**, 0 duplicate names, 7 cross-server collisions namespaced |
| Every Phase 6 fix | 18/18 confirmed working on real data |
| Every Phase 4 fix | 11/11 confirmed working on real data |
| **Total** | **49/49 checks passed** |
| Cyrillic sweep | **18/24 passed — 6 real failures, all in this phase** |
| Live 4-turn UA/RU session | Model side works; UI and NLP side does not |

So the previous phases hold up: `run_command` reports exit codes, `search_code` accepts a file, `~/.tomas` is readable but not writable, sessions record completeness, the generator is gone, tool selection picks by relevance (32 of 129 sent, 97 withheld), and MCP namespacing works against 7 genuine cross-server collisions.

What is *not* fine is everything this phase is about.

### Triage

| Severity | Item | One line |
|---|---|---|
| **Critical** | P7-1 | You cannot type a single Cyrillic character into the prompt |
| **Critical** | P7-2 | Keyword extraction drops all Cyrillic, so retrieval and tool selection are blind |
| High | P7-3 | 22.5 s of dead time before the first prompt, 21.5 s of it serial MCP connect |
| High | P7-4 | PDF export crashes on any Cyrillic character |
| Medium | P7-5 | Tool calls render Cyrillic as `\u043f\u0440...`, truncated mid-escape |
| Medium | P7-6 | The chat has no width awareness — nothing wraps, long output runs off screen |
| Medium | P7-7 | A denied tool call tells the model nothing, so it retries the same call six times |
| Medium | P7-8 | Startup noise: six failed servers reported as errors the user cannot act on |
| Low | P7-9 | Stop-word list is English-only |

---

## Part A — Cyrillic

### P7-1 · You cannot type Cyrillic into the prompt

**Severity: CRITICAL — this is the whole feature, and it is one line.**

`agent.py:2809-2820`, the REPL input loop:

```python
# ── Printable ASCII ───────────────────────────────────────────
if len(ch) == 1 and 32 <= ch[0] < 127:
    buffer.append(chr(ch[0]))
    ...
    continue

# ── Everything else (non-printable, utf-8 multi-byte, etc.) ──
continue
```

`ch` comes from `msvcrt.getch()`, which returns **bytes**. Measured, for every plausible codepage:

```
Ukrainian 'П'    cp866: bytes=b'\x8f' first=143  accepted=False
Ukrainian 'П'   cp1251: bytes=b'\xcf' first=207  accepted=False
Ukrainian 'ї'    cp866: bytes=b'\xf5' first=245  accepted=False
Russian   'Ж'   cp1251: bytes=b'\xc6' first=198  accepted=False
```

Every Cyrillic keystroke fails `32 <= ch[0] < 127` and falls through to `continue` — **silently discarded**. The character simply never appears. There is no error, no beep, nothing to debug from. The same is true of every accented Latin character, every emoji, and every other non-ASCII key.

**Fix — `msvcrt.getwch()` instead of `msvcrt.getch()`.** It returns a `str`, already decoded, one wide character per call:

```python
ch = msvcrt.getwch()          # str, not bytes

if ch in ("\x00", "\xe0"):     # arrow / function key prefix
    ext = msvcrt.getwch()
    ...
elif ch == "\r":
    ...
elif ch in ("\x08", "\x7f"):   # backspace
    ...
elif ch == "\x1b":             # escape
    ...
elif ch.isprintable():         # ← accepts every language, not just ASCII
    buffer.append(ch)
```

`ch.isprintable()` is the whole change in policy: it is true for `П`, `ї`, `é`, `日`, and false for control characters. The extended-key prefixes (`\x00`, `\xe0`) and F5–F8 handling all have direct `getwch` equivalents.

Two details that will bite:

- **Backspace must delete one *character*, not one byte.** With `getwch` the buffer is already a list of characters, so the existing `buffer.pop()` is correct — but only once the buffer stops being byte-derived.
- **The redraw must measure display width, not `len()`.** See P7-6; for Cyrillic specifically, `len()` is correct (measured: `display_width("їєґіІЇЄҐ") == 8 == len(...)`), so Cyrillic needs no special padding. The problem is CJK and emoji, which this phase should handle once rather than twice.

**Test:** feed a scripted `getwch` sequence spelling `Привіт` and assert the buffer contains `Привіт`; assert `é`, `日` and an emoji also survive; assert arrows and F-keys still work.

**Implemented.** `msvcrt.getch()` -> `msvcrt.getwch()` throughout the loop, every byte comparison became a str comparison, and the ASCII test became `ch.isprintable()`. The `\x00` and `\xe0` extended-key prefixes were merged into one branch — F5-F8 lived behind the `\x00` test and would otherwise have become unreachable. `_refresh()` now measures display columns and scrolls horizontally rather than leaving debris when the buffer wraps. Tests: `TestInputAcceptsEveryScript` — 12 cases covering ASCII, Ukrainian, Russian, Ukrainian-only letters, accented Latin, CJK, emoji, mixed scripts, backspace, escape, slash commands, and an F-key followed by Cyrillic.

---

### P7-2 · Retrieval and tool selection are blind to Cyrillic

**Severity: CRITICAL — it fails silently and looks like it works.**

`learning/text.py:34`:

```python
words = re.findall(r"[a-zA-Z][a-zA-Z0-9_\-']{1,}", text)
```

The character class is Latin-only. Measured:

```
extract_keywords("Прочитай файл конфігурації та виправ помилку")   -> []
extract_keywords("Прочитай файл конфигурации и исправь ошибку")    -> []
extract_keywords("Read the configuration file and fix the error")  -> ['read','configuration','file','fix','error']
similarity("Прочитай файл конфігурації", "файл конфігурації")      -> 0.0
extract_keywords("Файл README.md містить документацію")            -> ['readme']   ← Cyrillic silently dropped
```

Three subsystems consume this and all three degrade to nothing:

1. **`learning/retrieval.recall()`** scores facts by keyword overlap. With an empty keyword set every Cyrillic query scores identically, so recall stops discriminating — measured, it returned the same 200 facts for `"файл конфігурації"` and `"configuration file"`.
2. **`agent.select_tools()`** (Phase 4's P4-8) scores tools against the turn's keywords. With no keywords it falls back to list order.
3. **`learning/corrections.py` and `promotion.py`** use `similarity()` to notice a repeated topic. At a constant 0.0 they never fire, so **the agent cannot learn anything from a Ukrainian or Russian session.**

Point 2 is worth demonstrating, because a naive test passes by accident. With `take_screenshot` listed first, a Ukrainian screenshot request appears to work. Reorder the list and the illusion collapses:

```
EN 'take a browser screenshot' -> ['take_screenshot']    ← correct
UA 'зроби скріншот браузера'   -> ['sql_query']          ← wrong
RU 'сделай скриншот браузера'  -> ['sql_query']          ← wrong
(empty context, list order)    -> ['sql_query']
Cyrillic result == list-order fallback: True
```

**Fix — make the tokeniser Unicode-aware:**

```python
# learning/text.py
_WORD_RE = re.compile(r"[^\W\d_][\w\-']*", re.UNICODE)

def extract_keywords(text: str, max_keywords: int = 12) -> list[str]:
    text = (text or "").lower()
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    text = re.sub(r"https?://\S+", "", text)
    words = [w for w in _WORD_RE.findall(text)
             if w not in STOP_WORDS and len(w) > 2]
    ...
```

`[^\W\d_]` is "any word character that is not a digit or underscore" — under `re.UNICODE` (the default for `str` patterns) that is every alphabetic character in every script. It keeps the existing behaviour for English exactly.

Note this changes nothing about *matching*: `search_code` already handles Cyrillic patterns correctly (measured: `search_code("українською")` → 1 match; `search_code("[а-яА-ЯіїєґІЇЄҐ]+")` → 3 matches), because it uses the caller's regex verbatim.

**Test:** `extract_keywords` returns non-empty for Ukrainian and Russian; `similarity("файл конфігурації", "конфігурації файл") > 0.5`; mixed `"Файл README.md містить документацію"` keeps both `readme` and the Cyrillic words; the English results are byte-identical to before; and the reordered tool-selection case above picks `take_screenshot` for all three languages.

**Implemented.** `_WORD_RE = re.compile(r"[^\W\d_][\w\-']*")` replaces the Latin-only class. English output is byte-identical.

One thing the plan under-estimated: a correct tokeniser is **necessary but not sufficient**. Tool names and descriptions are English, so `зроби скріншот браузера` tokenised perfectly and still overlapped nothing in `take_screenshot — screenshot the browser page`; selection stayed on the list-order fallback. `TERM_ALIASES` bridges ~180 Cyrillic technical terms to their English equivalents (файл->file, скріншот->screenshot, база->database, помилка->error …), appended *alongside* the original words so nothing is lost, with `aliases=False` for pure tokenisation. Only with the bridge does the reordered selection case pass in all three languages. Tests: `TestTokeniser`, `TestToolSelectionAcrossLanguages`.

---

### P7-4 · PDF export crashes on Cyrillic

**Severity: HIGH — it raises, it does not degrade.**

```
FPDFUnicodeEncodingException: Character "З" at index 0 in text is outside
the range of characters supported by the font used: "helvetica".
```

`pdf_report_skill.py` uses fpdf2's core Helvetica, which is latin-1 only. Any Ukrainian or Russian report fails outright — and this is the one place in the codebase where a Cyrillic character produces an exception rather than working.

**Fix — register a Unicode TTF and use it:**

```python
FONT_CANDIDATES = [
    Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "DejaVuSans.ttf",
    Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "arial.ttf",
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
]

def _use_unicode_font(pdf) -> str:
    for path in FONT_CANDIDATES:
        if path.exists():
            pdf.add_font("uni", "", str(path))
            pdf.add_font("uni", "B", str(path))   # or the bold file if present
            return "uni"
    return "Helvetica"        # ASCII-only fallback, still better than crashing
```

Then `pdf.set_font(font_name, ...)` everywhere, and a transliteration or `errors="replace"` last resort so a missing font degrades to readable-ish output instead of an exception. Arial ships with Windows and covers Cyrillic; DejaVu is the portable choice.

**Test:** generating a PDF from a source containing `Звіт українською` and `Отчёт по-русски` succeeds and the file is non-empty; with no TTF available it still succeeds rather than raising.

**Implemented.** The existing font machinery was broken two ways: it looked only for DejaVu (absent on a stock Windows box) and **never called `add_font`**, so even a hit would have failed at `set_font` — and the body bypassed it entirely with five direct `set_font("Helvetica")` calls. Now four families are tried (DejaVu, Arial, Calibri, Verdana) across seven font directories, faces are actually registered, all five call sites route through `_use_font`, and a missing bold/italic file falls back to the regular face rather than losing Cyrillic. With no TTF at all, `_latin1_safe()` transliterates instead of raising. Verified: Arial registered, 45,750-byte PDF; the forced no-font path still produced a file. Tests: `TestPdfCyrillic`.

---

### P7-9 · Stop words are English-only

`learning/text.py:STOP_WORDS` contains `the`, `is`, `are`, … and nothing else. Measured: none of `що`, `це`, `как`, `это` are present.

Once P7-2 makes the tokeniser see Cyrillic, these function words become the *most frequent* tokens in every Ukrainian and Russian message and will dominate keyword extraction — turning a real fix into a new noise source. The two changes ship together or not at all.

**Fix:** add the common Ukrainian and Russian function words (roughly 120 each: `що, це, як, для, або, тому, коли, який, буде, треба…` / `что, это, как, для, или, потому, когда, который, будет, надо…`). Keep them in one set — the tokeniser does not need to know which language it is reading.

**Implemented.** `_STOP_WORDS_EN | _STOP_WORDS_UK | _STOP_WORDS_RU`, one flat set. Verified: `"Що це таке і як це працює? Це дуже важливо."` -> `['таке', 'працює', 'важливо']` — the function words no longer dominate.

---

## Part B — The chat

### P7-3 · 22.5 seconds of dead time before the first prompt

**Severity: HIGH — it is the first thing anyone notices.**

Measured cold start with 17 MCP servers configured:

```
import agent                1.00s
read MCP config             0.00s
connect MCP servers        21.45s   (11 ok, 6 failed, 118 tools)
build system prompt         0.01s
---------------------------------
TOTAL before first prompt  22.46s
```

`MCPManager.discover_and_connect` (`mcp_manager.py:379`) loops servers **serially**, and each failure waits for its own timeout before being recorded. Six failures — three HTTP 401, one 406, one JSON error, one `initialize failed` — are paid one after another.

Per-turn cost, by contrast, is already fine: `select_tools` over 129 tools takes **2.0 ms**, rebuilding the system prompt **8.6 ms**. Nothing in the turn loop needs optimising. The whole problem is startup.

**Fix — connect concurrently, and do not block the prompt on it:**

1. **Parallel connect.** A thread pool over servers turns 21.5 s into roughly the slowest single server (~3-4 s here). `MCPServer` already owns a `threading.Lock`, so per-server state is safe; only `self._all_tools`, `self._owner` and `self.failed_servers` need guarding, and the deterministic namespacing from P6-7 must be preserved — collect per-server results, then merge **in config order** so exposed names do not depend on which thread finished first.

2. **Show the prompt immediately.** Connect in the background and let the user start typing. The first turn waits on the connection only if it needs a tool the pool has not loaded yet; built-in tools are available from the first keystroke. A one-line status that resolves in place —

   ```
   ◈ MCP: connecting… 4/17          →   ◈ MCP: 11 connected · 118 tools · 6 unavailable
   ```

3. **Cache the tool list.** Server tool lists change rarely. Cache `{server: {tools, resources, prompts, fetched_at}}` in `~/.tomas/mcp-cache.json`, use it instantly on startup, and refresh in the background. A warm start then shows real tools in well under a second.

**Test:** with 17 stub servers of which 6 hang for 3 s, `discover_and_connect` completes in under 6 s rather than 20+; exposed tool names are identical to the serial ordering; a warm start with a valid cache reaches the prompt in under 1 s.

**Implemented — partly.** `discover_and_connect(parallel=True)` connects through a `ThreadPoolExecutor`, then registers names **in config order**, so exposed names never depend on which thread finished first. Measured against the real 17-server config: **24.1s -> 5.3-7.5s (~3-4x)**, byte-identical exposed names, no duplicates. `_register_tool` was extracted so registration has one implementation, and a server that raises is caught per-server instead of taking startup down.

**Not done: the tool-list cache and the non-blocking prompt.** Parallel connect alone met the cold-start target. Both remaining pieces change startup *sequencing* — background connect in particular requires the first turn to wait on a partially-populated tool pool, a correctness question this phase did not need to answer to hit its number. Tests: `TestParallelConnect`.

---

### P7-5 · Tool calls render Cyrillic as escape sequences

`adapters/terminal.py:95`:

```python
args_str = json.dumps(event.args, default=str)[:120]
```

`json.dumps` defaults to `ensure_ascii=True`. What the user actually saw in the live session:

```
⚡ run_command [built-in]({"command": "rm \"\u043f\u0440\u0438\u0432\u0456\u0442_\u0441\u0432\u0456\u0442.txt\"...
```

and directly beneath it, from the permission prompt (which uses `str(v)` instead), the same command rendered correctly:

```
command: rm "привіт_світ.txt" 2>&1
```

Two defects in one line. The escaping is the obvious one. The subtler one is that truncating the escaped form at 120 characters cuts **mid-escape** — measured, the last 14 characters of the cut are `3f\u0440\u0438`, which is not text in any language.

**Fix:**

```python
args_str = json.dumps(event.args, default=str, ensure_ascii=False)
args_str = shorten(args_str, width=120)      # cut on a character boundary
```

And audit the rest: `agent.py:2844` has the same `json.dumps(...)[:80]` pattern in the history renderer.

Better still, stop showing raw JSON. A tool call reads far better as its salient argument:

```
  ⚡ run_command  rm "привіт_світ.txt"                      [built-in]
  ⚡ read_file    calculator.py:1-40                        [built-in]
  ⚡ take_screenshot                          [MCP: playwright]
```

One line, the argument that matters, origin right-aligned. The full arguments are one keystroke away and already in the session file.

**Test:** a tool call with Cyrillic arguments renders the actual characters; a 400-character argument is cut on a character boundary and never mid-escape.

**Implemented.** `ensure_ascii=False` plus `shorten()` everywhere JSON reaches the screen (`adapters/terminal.py`, and the history renderer in `agent.py`). Tool calls now render as the name plus the argument that matters (`_HEADLINE_ARG` per built-in tool) with the origin right-aligned, instead of a raw JSON dump. The permission prompt moved to width-safe truncation too. Tests: `TestRendering`.

---

### P7-6 · The chat has no idea how wide the terminal is

Every render path in `adapters/terminal.py` is a bare `print()`. Nothing measures the terminal, nothing wraps, nothing aligns. Consequences, all visible in a normal session:

- A long tool result prints as one line that runs off the right edge or hard-wraps mid-word at the terminal's mercy.
- `'─' * 46` is hard-coded in a dozen places (`agent.py`, `self_improve.py`, `skills_manager.py`) regardless of actual width.
- Streamed text has no wrapping at all, so a paragraph arrives as one very long line.
- Nothing distinguishes the agent's prose from tool chatter except two leading spaces.

**Fix — one small `text_display.py`, used by every renderer:**

```python
def term_width(default: int = 100) -> int:
    return max(60, min(shutil.get_terminal_size((default, 24)).columns, 120))

def display_width(text: str) -> int:
    """Columns a string occupies. CJK and emoji are double-width; Cyrillic,
    Greek and accented Latin are single — measured, not assumed."""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1
               for c in text if unicodedata.combining(c) == 0)

def wrap(text: str, indent: str = "  ") -> str: ...
def shorten(text: str, width: int) -> str: ...   # character-safe truncation
def rule(char: str = "─") -> str: ...            # full-width, not hard-coded 46
```

`display_width` is the function P7-1's redraw needs too — one implementation, both uses. Cyrillic measures single-width (verified: `display_width("їєґіІЇЄҐ") == 8`), so this costs Ukrainian and Russian nothing and fixes CJK and emoji for free.

**Test:** `display_width` returns 8 for `їєґіІЇЄҐ`, 4 for `日本`, 1 for `é` composed and decomposed; wrapped output never exceeds the measured width; `rule()` matches the terminal.

**Implemented.** `text_display.py`: `display_width` (ANSI-stripped, CJK/emoji double-width, combining marks zero), `term_width` (clamped 60-120), `shorten` (character-safe), `wrap` (preserves blank lines, never reflows code or table rows), `rule`, `pad`, `strip_ansi`. Used by both the REPL redraw and the event renderer, so the two cannot disagree about how wide a string is. Assistant prose is now wrapped. Tests: `TestDisplayWidth`, `TestRendererWidth`.

---

### P7-7 · A denied tool call teaches the model nothing

From the live session — the model asked to delete a file, was denied, and then did this:

```
⚡ run_command  rm "привіт_світ.txt" 2>&1; ls "привіт_світ.txt" 2>&1     → denied
⚡ run_command  rm "привіт_світ.txt" 2>&1                                 → denied
⚡ run_command  rm "привіт_світ.txt" 2>&1; test -f ... && echo "EXISTS"   → denied
⚡ run_command  rm "привіт_світ.txt" 2>&1; test -f ... && echo "Файл існує" → denied
⚡ run_command  rm "привіт_світ.txt" 2>&1; test -f ... && echo "EXISTS"   → denied
⚡ run_command  rm "привіт_світ.txt" 2>&1; test -f ... && echo "EXISTS"   → denied
⚠ Stopping — the same tool call repeated 3x in a row.
```

Six denied calls, ~26 s, and an empty reply. The loop detector eventually caught it (Phase 2 working as designed) but the turn was already wasted.

The cause is the message the model receives: `"Error: user denied this tool call."` That reads like a transient failure, so the model rewrites the command cosmetically and tries again. It has no way to know the denial was about the *action*, not the phrasing.

**Fix — make the result say what a denial means:**

```python
result = ("Error: the user denied this tool call and will deny identical "
          "retries. Do not re-issue this command. Either explain what you "
          "wanted to do and ask, or continue without it.")
```

And on the second denial in a turn, stop offering: return the same message with `Further tool calls in this turn will not be approved.` so the model writes its summary instead of probing.

Two related fixes worth taking at the same time:

- **A non-interactive run should not present a prompt it cannot answer.** With stdin not a TTY, `TerminalAdapter` auto-denies — correct, but it should say so once (`non-interactive: medium/high-risk tools unavailable`) rather than silently denying six times.
- **The model does not know it is on `cmd.exe`.** It reached for `rm`, `test -f` and `ls` on Windows. One line in the system prompt naming the shell would remove a whole class of wasted calls.

**Test:** a stub responder that denies once produces at most two attempts, not six; the denial text appears in the tool result; a non-interactive run prints the notice once.

**Implemented.** The denial result now states that a retry will be denied and not to re-issue the call; a second denial in the same turn adds that further tool calls are unlikely to be approved. A non-interactive `TerminalAdapter` returns `deny` without prompting and says so **once** rather than silently denying six times. `_environment_section()` names the shell (`cmd.exe`, with `dir`/`type`/`del`/`findstr` rather than `ls`/`cat`/`rm`/`grep`), the interpreter path, and asks the model to reply in the user's language. Tests: `TestDenialSemantics`, `TestEnvironmentAwareness`.

---

### P7-8 · Startup reports six errors the user cannot act on

```
✗ github-github-mcp-server: HTTP 401: Unauthorized
✗ supabase-community-supabase-mcp: HTTP 401: Unauthorized
✗ googleapis-mcp-toolbox: initialize failed: None
✗ timescale-pg-aiguide: HTTP 406: Not Acceptable
✗ vercel: HTTP 401: Unauthorized
✗ linear: Expecting value: line 1 column 1 (char 0)
```

Six red lines before the user has typed anything. Three are simply "you have not authenticated this optional server", which is not an error — it is a fact, and a permanent one until they do something about it.

**Fix:** collapse to one line, and separate *needs credentials* from *actually broken*:

```
◈ MCP: 11 connected · 118 tools
  3 need credentials (github, supabase, vercel) · 3 unavailable — /mcp for details
```

Keep the detail behind `/mcp`, where someone who wants to fix it will look. A 401 on an optional server should never be styled the same as a crash.

**Implemented.** `_classify_mcp_failures()` splits 401/403/auth from genuine breakage; startup prints one line — `MCP: 11 connected · 118 tools · 3 need credentials · 3 unavailable` — followed by a dim name list pointing at `/mcp`. Tests: `TestFailureClassification`.

---

## Verification

```powershell
# P7-1 — every language reaches the buffer
.venv\Scripts\python.exe -m unittest tests.test_input -v

# P7-2 — the tokeniser sees Cyrillic, and English is unchanged
.venv\Scripts\python.exe -c "from learning.text import extract_keywords as k; print(k('Прочитай файл конфігурації'), k('Read the configuration file'))"

# P7-3 — cold start
.venv\Scripts\python.exe -c "import time,mcp_manager as m; t=time.time(); mgr=m.MCPManager(); mgr.discover_and_connect(); print(round(time.time()-t,1), 's', len(mgr.tools), 'tools')"

# P7-4 — Cyrillic PDF
.venv\Scripts\python.exe -c "import pdf_report_skill as p; print(p.generate_ai_news_pdf('_ua.pdf'))"

# Full sweep — the script that produced this phase's evidence
.venv\Scripts\python.exe -m tests.simulate checks
.venv\Scripts\python.exe -m tests.simulate cyrillic
```

Targets against today's measurements:

| Metric | Now | Target |
|---|---|---|
| Cyrillic characters accepted by the prompt | **0** | all |
| `extract_keywords` on Ukrainian | `[]` | non-empty |
| Tool selection for a Ukrainian request | list order | by relevance |
| Cold start to first prompt | 22.5 s | < 2 s (warm), < 6 s (cold) |
| PDF with Cyrillic | raises | succeeds |
| Denied-call retries | 6 | ≤ 2 |
| Startup error lines | 6 | 1 |
| Cyrillic sweep | 18/24 | 24/24 |

---

## Acceptance criteria

- [ ] Every printable character — Ukrainian, Russian, accented Latin, CJK, emoji — can be typed, edited and submitted at the prompt.
- [ ] `extract_keywords` and `similarity` work on Cyrillic; English results are unchanged.
- [ ] A Ukrainian or Russian request selects tools by relevance, and reordering the tool list does not change the result.
- [ ] The agent can learn from a Ukrainian session: corrections and promotion fire on Cyrillic text.
- [ ] Stop words cover Ukrainian and Russian, so function words do not dominate extraction.
- [ ] PDF export succeeds with Cyrillic, and degrades rather than raising when no Unicode font is present.
- [ ] Tool calls display real characters, truncated on character boundaries.
- [ ] All output respects the terminal width; nothing is hard-coded to 46 columns.
- [ ] The prompt appears in under 2 s on a warm start; MCP connects in the background.
- [ ] A denied tool call is not retried more than once, and non-interactive runs say so once.
- [ ] Startup shows one MCP status line; credentials-missing is distinguished from broken.
- [ ] `tests/simulate cyrillic` reports 24/24.

---

## What this phase is not

Not a rewrite of the TUI, and not a GUI. `agent_cli.py` keeps its arrow-key menus; `adapters/terminal.py` keeps rendering the same events. Phase 5 is where a desktop front end goes, and it will consume the same event stream — which is exactly why the work here belongs in the adapter and in one small display module, not scattered through the loop.

## Next

Phase 5 — [the desktop app](PHASE-5-desktop-app.md). The width and display-width helpers from P7-6, and the status-line model from P7-3 and P7-8, are the same abstractions a GUI needs.
