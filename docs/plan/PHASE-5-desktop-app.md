# Phase 5 — Desktop App

**Goal:** a desktop application with no terminal, built as an adapter over the existing core — not a rewrite.
**Effort:** 3-4 weeks (assuming Phase 2 is done; considerably more if it is not).
**Depends on:** Phase 2 (hard requirement). Phase 3 provides the app's most distinctive screen.

If Phase 2 is complete, this phase adds no agent logic at all. Every line here is UI, transport, or packaging.

---

## The architecture decision

**Run the core as a local daemon and talk to it over IPC. Do not embed Python in the UI process.**

```
┌──────────────────────────┐        ┌──────────────────────────────┐
│  Desktop UI              │  WS/   │  tomas-daemon (Python)       │
│  Tauri or Electron       │◄──────►│  core.run_turn()             │
│  renders AgentEvents     │  IPC   │  emits AgentEvent JSON       │
│  answers PermissionNeeded│        │  127.0.0.1 only, token auth  │
└──────────────────────────┘        └──────────────────────────────┘
                                                  ▲
                                                  │ same protocol
                                    ┌─────────────┴──────────────┐
                                    │  terminal adapter (today)  │
                                    └────────────────────────────┘
```

Why this way:

- **A headless daemon comes free** — servers, CI, automation, cron-driven agents, all without a UI.
- **The language boundary is clean.** The UI can be TypeScript without anyone attempting to port the agent loop to TypeScript.
- **Crash isolation.** A UI crash does not kill a running turn; the UI reconnects and resumes streaming.
- **Remote later.** The same protocol over TLS gives phone/web clients with no core changes.
- **The pattern is already proven here** — `zen_proxy` demonstrates a local HTTP service works fine in this environment.

The alternative — embedding CPython in Electron via a bridge — couples UI lifecycle to agent lifecycle, complicates packaging far more than it saves, and makes the headless case a special build instead of the default.

**UI framework:** prefer **Tauri** (~10 MB bundle, native webview, Rust shell) over Electron (~120 MB) if the team can take on Rust for the shell. If not, Electron is fine — the decision is reversible because the UI only consumes a JSON event stream. Do not let this choice block the protocol work.

---

## The protocol

Phase 2's event types serialise directly. Add a thin envelope:

```jsonc
// daemon → UI
{"v": 1, "session": "s_01H...", "seq": 42, "type": "ToolStarted",
 "data": {"tool_use_id": "tu_1", "name": "read_file",
          "args": {"file_path": "agent.py"}, "risk": "low", "origin": "built-in"}}

// UI → daemon
{"v": 1, "session": "s_01H...", "type": "UserMessage", "data": {"text": "..."}}
{"v": 1, "session": "s_01H...", "type": "PermissionResponse",
 "data": {"tool_use_id": "tu_1", "decision": "allow"}}
{"v": 1, "session": "s_01H...", "type": "Interrupt"}
```

Rules:

- **`seq` monotonic per session** so the UI can detect gaps and resync after a reconnect.
- **Version the envelope from day one** (`v: 1`). The UI and daemon will be updated separately on user machines; assume version skew.
- **Events are append-only facts.** The UI holds no agent state it cannot rebuild from the event log — that is what makes reconnect trivial.
- **One serialiser, in core**, derived from the dataclasses. Never hand-write JSON on both sides.

### Security — this is a local server, treat it like one

- **Bind `127.0.0.1` only.** Never `0.0.0.0`, not even in dev.
- **Token auth.** Generate a token at daemon start, write it to `~/.tomas/daemon.token` with user-only permissions, require it on connect. Without this, any local process — including a webpage's JS — can drive an agent that has `run_command`.
- **Check `Origin`** on the WebSocket handshake to block browser-based connections.
- **Bind to an ephemeral port**, write it next to the token; do not hardcode one.
- **The daemon inherits the agent's full permissions.** It can read files and run commands. Treat unauthenticated access as full local compromise.

---

## The UI

### Screens

1. **Chat** — the main surface. Messages, streamed text, tool calls as collapsible inline cards (name, args, result, duration), retry/error states as inline notices rather than modal errors.
2. **Permission dialog** — see below.
3. **Sessions** — browse, search, resume, delete. `session_manager` already provides this; it needs UI-free accessors after Phase 2.
4. **Providers** — the Phase 4 `provider_manager` pages: add, probe, switch, per-provider model list. Show probed capabilities honestly ("this provider does not support tool use").
5. **Learned** — *the differentiating screen*. Everything the agent has learned about the user: each fact, confidence, evidence count, last confirmed, the transcript excerpt that produced it, and a delete button. This is what "self-improving, but you stay in control" looks like, and no competitor offers it.
6. **MCP servers** — connected/failed, tool counts, which tools are currently selected (Phase 4's relevance selection) and why.
7. **Settings** — model, permission mode, learning on/off, incognito, data retention.

### The permission dialog is the most important UI in the app

It is the moment the user decides whether to trust the agent. Requirements:

- Show **risk tier prominently**, colour-coded.
- Show the **full command or file path** — never truncated to the point of ambiguity. `run_command` must show the entire command.
- Offer: **Allow once** / **Deny** / **Allow this exact command for this session**. Do **not** offer a blanket "always allow this tool" — that is the exact flaw Phase 2 fixes in `check_permission` (`agent.py:818-820`), and a GUI makes it far too easy to click through.
- **Never default-focus the allow button.** Deny is the safe default.
- Show a **diff preview** for `edit_file` / `write_file` before approval. This is where a GUI decisively beats the terminal, and it is worth building early.

### Streaming and interrupt

- Render `TextDelta` incrementally. After Phase 4's in-process adapter this is real token streaming.
- **Interrupt must work mid-turn** — a stop button that cancels the in-flight model call and any running tool. The terminal has Ctrl+C; the GUI needs an explicit `Interrupt` event, and the core must honour it between tool calls at minimum.
- Show a **live token/cost counter** — `_session_tokens` already tracks it (`agent.py:122`); the terminal prints it after every turn.

### What the user must not see

Per the product goal, learning is invisible by default: no popups, no "I learned something!" toasts. `LearnedSomething` events should be silently recorded and surfaced only in the Learned screen — with, at most, a small unobtrusive indicator. **Inspectable, not intrusive.**

---

## Distribution and updates

A desktop app raises the bar on installation, and the current installer has gaps found during QA (all in `install.ps1`):

| Sev | Issue | Fix |
|---|---|---|
| MED | pip dependency failure only **warns and continues** (`:335-344`); Playwright failure swallowed by an empty `catch {}` (`:331-334`) — yet it still prints "Installation Complete!" | Fail loudly; a partial install must report itself as partial |
| MED | No top-level `$ErrorActionPreference = "Stop"` — `Copy-Item`/`Move-Item`/`Remove-Item` failures pass silently | Set it at the top of the script |
| MED | Uninstall cleanup (`:594-603`) retries `rmdir` with **no limit** in a detached process; prints `[OK] Deleted` *before* deletion is confirmed | Bounded retries, report the real outcome, tell the user if files were locked |
| MED | `TOMAS.ps1:8-10,31` prefers the **system** venv but runs the **local** source | Pick one consistently; for the desktop app, always use the bundled runtime |
| LOW | Template substitution uses regex `-replace` (`:372,384,608`) — an install path containing `$` is mangled | Use literal `.Replace()` |
| LOW | `install.sh` has no upgrade path, unlike Windows' `TOMAS-upgrade.cmd` | Add one for parity |

For the desktop app specifically:

- **Bundle the Python runtime.** Never depend on a system Python. PyInstaller or a python-embed distribution inside the app bundle.
- **Sign the binaries.** Unsigned executables that read files and run commands will be flagged by SmartScreen and by users who are right to be suspicious.
- **Auto-update with a rollback path** (Tauri and Electron both ship updaters). Update the daemon and UI **together**, and use the protocol version to refuse a mismatched pair with a clear message rather than failing obscurely.
- **User data stays in `~/.tomas/`** and is never touched by an update — the rule established in Phase 1. Test an upgrade with real sessions, memory, learned facts and providers present, and confirm all four survive.

---

## Keep the terminal

Do not drop the CLI when the GUI ships. It is a genuinely better fit for a large share of the audience, it is now just another adapter (~300 lines), and it keeps the core honest: if something can only be done through the GUI, event coverage has a gap.

Both front ends should share one `~/.tomas/`, so a session started in the terminal can be resumed in the app and vice versa.

---

## Implementation order

1. **Daemon wrapper** — a WebSocket server around `core.run_turn`, token auth, session multiplexing. Nothing new in the core.
2. **Protocol + serialiser**, generated from the Phase 2 dataclasses. Version it.
3. **A throwaway HTML client** — one file, no framework, just to prove the protocol end to end. Do this before choosing a UI stack; it will expose protocol gaps in hours rather than weeks.
4. **Chat screen** in the real framework, with streaming and tool cards.
5. **Permission dialog** with the diff preview.
6. **Sessions, providers, MCP screens** over the Phase 2/4 UI-free accessors.
7. **Learned screen** — the differentiator.
8. **Packaging, signing, auto-update.**
9. **Interrupt, reconnect, resync** — the reliability details that decide whether the app feels solid.

---

## Verification

- Kill the UI mid-turn; restart it; the session resumes and no events are lost (`seq` continuity).
- Kill the daemon mid-turn; the UI shows a clear disconnected state and recovers on restart.
- Connect without a token → rejected. Connect from a browser page → rejected on `Origin`.
- Run a full session with the network disconnected against a local Ollama model.
- Upgrade with existing sessions, memory, learned facts and provider config present → all four survive.
- Start a session in the terminal, resume it in the app.

## Acceptance criteria

- [ ] The desktop app contains **no agent logic** — only rendering, transport and packaging.
- [ ] The same core serves terminal, desktop and headless with no branching on front end.
- [ ] The daemon binds `127.0.0.1` with token auth and `Origin` checking.
- [ ] Streaming, tool cards, permission dialogs with diff preview, and interrupt all work.
- [ ] The Learned screen shows every learned fact with evidence and allows deletion.
- [ ] Auto-update preserves all user data and refuses mismatched daemon/UI versions cleanly.
- [ ] The terminal UI still works, sharing state with the app.

---

## Closing note for the whole plan

By the time this phase starts, the desktop app should be the *easiest* phase — because Phase 2 did the hard part. If Phase 5 starts to feel like a rewrite, that is the signal that Phase 2 was skipped or left incomplete; go back and finish it rather than pushing agent logic into the UI.

The plan overall is one idea repeated: **things get written and never read** — skills that can't be discovered, notes that never load, memories that get truncated away, tool calls the model never sees, capabilities guessed instead of measured. Every phase closes one of those return paths.
