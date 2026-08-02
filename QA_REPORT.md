# TOMAS — QA & Code Review Report

**Date:** 2026-08-02
**Branch:** `prototype2-refactoring` (commit `0a50e1c`)
**Method:** 5 parallel test agents + direct instrumented runs of the real agent loop
**Model under test:** OpenCode Zen free tier via local `zen_proxy` (`127.0.0.1:6446`), default `deepseek-v4-flash-free` from `.env`, plus all 6 free Zen models for latency comparison

---

## Verdict

TOMAS is **architecturally complete but non-functional end-to-end in its shipped configuration.**
The tool implementations, MCP layer, session/memory/skills subsystems and installer are all in good shape — but three defects in the agent loop and proxy mean that, out of the box, **the first message a user sends fails**, and every tool-using turn after that fails too.

All three are small, localized fixes. Estimated effort to get from "broken" to "working": **under 40 lines of code.**

| Area | State |
|---|---|
| Agent loop (conversation + tool round-trip) | ❌ Broken — 2 blocking bugs |
| Streaming | ❌ Broken — 100% failure through the proxy |
| Terminal I/O on non-UTF-8 console | ❌ Crashes |
| Built-in tools (file, search, web) | ✅ Correct and fast |
| MCP integration | ✅ Works (9/15 servers connect) |
| Sessions / memory / skills | ✅ Pass |
| Installer / updater / uninstaller | ⚠️ Works, several robustness gaps |
| Test coverage | ⚠️ 8 tests for 9.5k lines |

---

## 1. Blocking bugs

### B1 — The agent never records its own turns (`agent.py:1219`)

`agent_loop` executes tools and appends the results, but **never appends the assistant message that requested them.** There is no `messages.append({"role": "assistant", ...})` anywhere in `agent.py` — all 4 appends in the file are `role: "user"`.

Two separate failures fall out of this:

**(a) Every tool-using turn dies.** The transcript becomes `user → user[tool_result]`. `zen_proxy.py:293-302` translates `tool_result` blocks into OpenAI `role: "tool"` messages, which must immediately follow an assistant message carrying `tool_calls`. Since that message is missing, the upstream rejects the request:

```
502 upstream_error → invalid_request_error: "Messages with role 'tool'..."
```

`agent.py` then treats the 502 as transient and retries 3× (5s/10s/20s), burning ~35s before returning *"I'm sorry, but the AI service is unavailable right now."*

Reproduced **100% of the time** across every scenario run: file create/read/edit, code search, web search, URL fetch, MCP `sequentialthinking`, MCP `memory`. In each case the tool itself executed correctly and returned correct data — the agent simply could never see it and never produced a final answer.

Isolated proof it is the missing message and not the model: a hand-built round-trip that *does* include the assistant turn succeeds on all 6 free models, at 120 tools and full system prompt:

```
deepseek-v4-flash-free   OK  3.3s   mimo-v2.5-free   OK  5.3s
ling-3.0-flash-free      OK  4.7s   north-mini-code  OK  3.7s
nemotron-3-ultra-free    OK 11.9s   laguna-s-2.1     OK  7.2s
```
(Some upstreams tolerate the orphan `tool` message and answer anyway — that is why a few scenarios *appeared* to work. Even then the model never sees its own tool call.)

**(b) There is no conversation.** The assistant's final text reply is never appended either, so the model sees only a list of user messages. Verified directly with tools disabled:

```
turn1 reply: '472'
turn2 reply: 'NO MEMORY'          # asked what number it had just picked
roles in history: ['user', 'user']
```

This also means saved sessions (`session_manager.save_session(messages, ...)`) contain **user turns only** — the session browser's `▌ assistant` branch (`agent_cli.py:1023`) can essentially never render.

**Fix** — in `agent_loop`, immediately before line 1219:
```python
messages.append({"role": "assistant", "content": response.content})
messages.append({"role": "user", "content": tool_results})
```
and in `main()` (~line 2386), append the returned reply as `{"role": "assistant", "content": reply}` after `agent_loop` returns.

---

### B2 — Streaming is 100% broken through `zen_proxy`, and the fallback never fires

`zen_proxy.py:462` forwards the client's `stream: true` straight upstream, but `_upstream_request` (`:214`) always does a plain `.read()` and `_handle_anthropic` always does `json.loads(zen_raw)` (`:502`). Upstream returns SSE (`data: {...}`), which is not valid JSON → parse error → `502 "Invalid upstream response"`. There is also no `text/event-stream` writer in the file at all (only `_send_json`, `:395`), so even a parsed stream could not be relayed in the shape the Anthropic SDK expects.

Confirmed for every model, with and without tools:
```
deepseek-v4-flash-free  tools=False  STREAM FAIL 7.6s  502 Invalid upstream response
deepseek-v4-flash-free  tools=True   STREAM FAIL 7.0s  502 Invalid upstream response
mimo-v2.5-free          tools=False  STREAM FAIL 7.0s  502 Invalid upstream response
mimo-v2.5-free          tools=True   STREAM FAIL 9.9s  502 Invalid upstream response
```

The non-streaming path works perfectly — but it is unreachable. `agent.py:1096-1100` only sets `_streaming_disabled` on `AttributeError`/`TypeError`; an `InternalServerError` is **re-raised into the retry loop** (`:1110`), which retries the *streaming* call 3× and then gives up. Net effect on a fresh install: the user's very first message takes ~65-75s and returns *"the AI service is unavailable right now."*

**Fix (two parts):**
1. `zen_proxy.py` — force `oai_body["stream"] = False` upstream, then, if the client asked for streaming, synthesize the Anthropic SSE sequence (`message_start`, `content_block_start`, one `content_block_delta`, `content_block_stop`, `message_delta`, `message_stop`) from the complete JSON and write it with `Content-Type: text/event-stream`.
2. `agent.py:1099` — on `anthropic.InternalServerError` from the streamed attempt, set `_streaming_disabled = True` and fall through to the non-streaming call instead of re-raising. This also protects any future non-streaming provider.

---

### B3 — `UnicodeEncodeError` crash on a non-UTF-8 Windows console

`agent.py` prints `▌ ✧ ⚙ ◎ ▣ ⇧ ⚡ ↳` etc. and never reconfigures stdout or the console codepage (no `reconfigure` / `chcp` / `PYTHONIOENCODING` / `colorama` anywhere in the file). On this machine the default Python console encoding is **cp1251**, and the agent crashes on its own output label:

```
File "agent.py", line 1151, in agent_loop
    print(f'  {MAGENTA}{BOLD}\u258c TOMAS{RESET}')
UnicodeEncodeError: 'charmap' codec can't encode character '\u258c'
```

Hit live during testing (the test driver had forced UTF-8, which masked it at first). Affects any non-English Windows locale or `chcp 1252/437`. `/help`, `/model`, `/status`, `/mode` all carry the same glyphs.

**Fix** — at the top of `agent.py` and `agent_cli.py`, do what `test_agent.py:22-26` already does:
```python
if sys.platform == "win32":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
```

---

## 2. Response times

Measured through the full agent path (real system prompt, real tool schemas, streaming disabled).

| Model | No-tool turn | One-tool turn | Notes |
|---|---|---|---|
| `deepseek-v4-flash-free` *(default in .env)* | 2.6s | **74.6s FAIL** | fastest simple replies, fails on tools |
| `mimo-v2.5-free` | 4.6s | 16.5–20.5s | |
| `ling-3.0-flash-free` | 7.5s | 8.0s | most consistent |
| `north-mini-code-free` | 9.9s | **74.4s FAIL** | |
| `nemotron-3-ultra-free` | 5.7s | 19.4s | |
| `laguna-s-2.1-free` | 7.1s | 9.5s | |

Startup, measured separately: `import agent` **1.38s**, `import agent_cli` **1.20s**, `build_system_prompt()` **15ms** (10,795 chars).

Tool latency is not the bottleneck — `fetch_url` 183ms, `search_web` 1.78s direct, MCP `sequentialthinking` 4ms. **All of the 60–120s per-turn wall times observed in the failing scenarios were retry backoff, not work.** Fix B1/B2 and turns land in the 3–20s range.

**Token cost:** 110 MCP tools + 10 built-ins = 120 schemas ≈ **38.5k input tokens on every single turn** before any conversation content, with no prompt caching on the tool block (`agent.py:1102`).

---

## 3. What works

- **Built-in tools are correct.** `write_file` wrote exact content; `search_code` located `build_system_prompt` at `agent.py:861`; `read_file` returned a clean `Error: file not found: ...` rather than a traceback; `fetch_url` returned correct HTML for example.com in 183ms.
- **Web search is real, not hallucinated.** Falls back from Playwright to DDG and returned accurate live data (Python 3.14.6, released 2026-06-10) — spot-checked against an independent search.
- **MCP works.** 9/15 servers connect (playwright, sequential-thinking, chrome-devtools, context7, mobile-mcp, pageindex, next-devtools, memory, scrapling), 110 tools discovered. Failures are all external credential/endpoint issues, not TOMAS bugs: github/supabase/vercel `401`, timescale `406`, googleapis `initialize failed: None`, linear JSON parse error. `create_entities` against the memory server executed correctly in 4ms.
- **Sessions, memory, skills all pass** — save/list/load/delete round-trips clean into `~/.tomas/sessions/`, `save_memory`/`load_memory_index` round-trip clean, 44 skills discovered without errors.
- **Unit suite passes:** `Ran 8 tests in 1.3s — OK`.
- **Installers parse clean:** `install.ps1` and `TOMAS.ps1` via the PowerShell AST parser, `install.sh` via `bash -n`.

---

## 4. Install / update / uninstall

Audited by reading (nothing was actually installed, updated or uninstalled on this machine).

**Good:** installs to `~/.tomas/{bin,src,.venv,...}`, adds only the **User** PATH (`install.ps1:557-566`, no admin needed). Update = re-run installer via `TOMAS-upgrade.cmd` (`:394-417`) and it correctly preserves user data — `.env` only written `if (-not (Test-Path $EnvFile))` (`:514`), `AGENT.md` likewise (`:457`), only `$SrcDir` is replaced (`:231`) while `sessions/`, `memory/`, `self-notes/`, `instructions/` live outside it. Uninstall removes the PATH entry (`:586-590`) and the install dir (`:592-603`). Re-install is idempotent (PATH duplicate-checked at `:560`).

**Gaps:**

| Sev | Finding |
|---|---|
| MED | `install.ps1:331-344` — pip dependency failure only **warns and continues**; Playwright Chromium failure is swallowed by an empty `catch {}`. The script still prints "Installation Complete!" over a broken install. |
| MED | No top-level `$ErrorActionPreference = "Stop"` — `Copy-Item`/`Move-Item`/`Remove-Item` failures can pass silently. |
| MED | `install.ps1:594-603` — the detached uninstall cleanup script retries `rmdir` with **no retry limit**; if a file is locked (e.g. TOMAS still running) it loops forever, while `"[OK] Deleted $tomasDir"` is printed *before* deletion is confirmed. |
| MED | `TOMAS.ps1:8-10,31` — prefers the **system** `~/.tomas/.venv` but always runs the **local** `agent_cli.py`, mixing dev source with a possibly stale venv. |
| LOW | `install.ps1:372,384,608` — template substitution uses regex `-replace` instead of literal `.Replace()`; an install path containing `$` would be mangled. |
| LOW | `install.sh` has no upgrade alias — asymmetric with Windows' `TOMAS-upgrade.cmd`. |

---

## 5. Code quality

**Security & permissions**
- **MED** `agent.py:819` — answering `"always"` to a permission prompt does `RISK_LEVELS[name] = "low"`, a **permanent global downgrade** of that tool's risk tier. One "always" on `run_command` auto-approves every later `run_command` — any command, not just the one shown. Fix: keep a separate "always approved" set instead of mutating `RISK_LEVELS`.
- **LOW** `agent.py:390` — `BLOCKED_PATTERNS` (`rm -rf /`, `mkfs`, `/dev/sd*`, fork bomb) are entirely Unix-specific on a Windows-only agent. Nothing stops `rd /s /q C:\` or `del /f /s /q`. It is also a blacklist, trivially bypassed. `run_command` is correctly tiered `high`, so the prompt still gates it — the list itself is decorative here.
- **LOW** `_safe()`/`_resolve()` (`agent.py:396-412`) were checked for traversal and are **sound** — `.resolve()` then `Path.relative_to`, not a string prefix check.
- **LOW** No evidence of API keys leaking into session files or logs.

**Structure**
- `agent.py` and `agent_cli.py` are ~105KB each, but this is **not** mass duplication — `agent_cli.py` imports the real logic from `agent.py` and adds a genuinely separate 64-function TUI. Only concrete overlap: the 9 ANSI constants copy-pasted at `agent.py:226-234` / `agent_cli.py:143-151`.
- **MED** `agent.py:2310-2316` — on free tier the cap is 32 tools, so **88 of 110 MCP tools are dropped**. The operator gets a console warning; nothing tells the *model* which tools vanished.
- **LOW** `agent.py:2289-2299` — MCP↔built-in name conflicts are renamed with an `mcp_` prefix (0 conflicts in practice), but same-named tools from two *different* MCP servers are not deduped; `MCPManager.call_tool` just takes the first match.
- **MED** `agent.py:1372-1381` — the "did you mean" bigram heuristic is so broad that almost any typo dumps the entire command list instead of a clean error.

**Tests** — `tests/test_agent_units.py`, 112 lines, 8 tests, all passing. Covers `read_file`, `write`/`edit_file`, `run_command` blocklist, `search_web` fallback, session lifecycle, instructions loading, provider detection. **Not covered:** the agent loop and its retry/streaming fallback (B1 and B2 would both have been caught by one integration test), `check_permission`/risk tiers, path-traversal edges, context compaction, MCP merge/truncation, slash-command dispatch, and `zen_proxy.py` entirely — 0 tests for the 645-line component responsible for the most production failures found here.

---

## 6. Recommended order of work

1. **B1** — append the assistant turn (`agent.py:1219` + `main()`). Unblocks all tool use *and* conversation memory. ~3 lines.
2. **B3** — force UTF-8 stdout on Windows. Stops the crash on non-English locales. ~4 lines.
3. **B2a** — set `_streaming_disabled` on `InternalServerError` (`agent.py:1099`). Makes the working fallback reachable regardless of provider. ~2 lines.
4. **B2b** — implement SSE synthesis in `zen_proxy.py` to actually restore streaming. ~30 lines.
5. Add an integration test that runs one full tool round-trip against a stub client — the single highest-value test this repo is missing.
6. Then the MEDs: permission `"always"` scoping, installer error handling, `TOMAS.ps1` venv/source mismatch.

---

## Appendix — test artifacts

All scenario runs used an instrumented non-interactive driver around the real `agent.agent_loop` with the real system prompt and real tool schemas (`YOLO_MODE`, tool calls traced for name/params/latency/result). Test files created during scenarios (`tmp_test/`, `tmp_web/`) and the MCP `TomasTestEntity` memory entity were removed; the repository working tree is unchanged apart from this report.
