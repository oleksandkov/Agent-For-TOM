# Self-Improving System Plan

## Goal

Build a self-improving system for TOMAS that:
1. Saves notes about itself automatically based on user interactions and session history
2. Supports a memory system the agent can write to and read from across sessions
3. Can be manually triggered by the user ("when I ask to do it it must do it")
4. Integrates into the existing menu and installer
5. All data stored in `~/.tomas/` (not `.agent/`)

---

## Project Data Storage Audit

### Where data currently lives

| Data | Path | Scope | Gitignored |
|------|------|-------|------------|
| Agent memory | `.agent/memory/` | Project-local | Yes |
| Self-improve data | `.agent/self-improve/` | Project-local | Yes |
| Sessions | `~/.tomas/sessions/` | User-global | No |
| Instructions | `~/.tomas/instructions/` | User-global | No |
| MCP config | `~/.claude.json` | User-global | No |
| Skills | `~/.claude/skills/`, `~/.agents/skills/` | User-global | No |
| Source code | `~/.tomas/src/` | User-global | No |
| Env config | `~/.tomas/.env` | User-global | No |
| Venv | `~/.tomas/.venv/` | User-global | No |
| Launcher scripts | `~/.tomas/bin/` | User-global | No |

### What is NOT in `.tomas/`
- Agent memory (`.agent/memory/`) — project-local, should move to `~/.tomas/memory/`
- Self-improve data (`.agent/self-improve/`) — project-local, should move to `~/.tomas/self-improve/`

### What already exists in `~/.tomas/`
- `session_manager.py` — already uses `~/.tomas/sessions/` (fully functional)
- `instructions_manager.py` — already uses `~/.tomas/instructions/` (fully functional)

### What needs to change
- `self_improve.py` — move from `.agent/self-improve/` to `~/.tomas/self-improve/`
- `agent.py` — move memory from `.agent/memory/` to `~/.tomas/memory/`
- New `self_notes.py` — create using `~/.tomas/self-notes/`
- Installers — ensure `~/.tomas/` subdirs are created on first run

---

## Existing Infrastructure

### `session_manager.py` — Already Done
- **Location**: `C:\Users\muaro\Documents\GitHub\Agent-For-TOM\session_manager.py`
- **Storage**: `~/.tomas/sessions/`
- **Functions**: `save_session()`, `load_session()`, `list_sessions()`, `delete_session()`, `continue_session()`, `get_latest_session()`, `clear_all_sessions()`
- **Auto-cleanup**: Removes oldest sessions when exceeding `MAX_SESSIONS = 50`
- **Status**: Fully functional, no changes needed

### `instructions_manager.py` — Already Done
- **Location**: `C:\Users\muaro\Documents\GitHub\Agent-For-TOM\instructions_manager.py`
- **Storage**: `~/.tomas/instructions/` (global) + `~/.tomas/instructions/project/` (per-project)
- **Functions**: `get_global_instructions()`, `get_project_instructions()`, `build_instructions_section()`, `create_default_instructions()`
- **Status**: Fully functional, no changes needed

### `self_improve.py` — Needs Migration
- **Location**: `C:\Users\muaro\Documents\GitHub\Agent-For-TOM\self_improve.py`
- **Current storage**: `.agent/self-improve/` (project-local, via `Path.cwd()`)
- **Needed**: Move to `~/.tomas/self-improve/` (user-global)
- **Key constants to change**:
  - `SELF_IMPROVE_DIR = Path.cwd() / ".agent" / "self-improve"` → `Path.home() / ".tomas" / "self-improve"`
- **Functions**: `log_user_message()`, `log_tool_call()`, `analyze_patterns()`, `generate_skills_for_all_ready_patterns()`, `generate_tips()`, `update_session_analysis()`, `get_self_improve_status()`

### `agent.py` — Needs Migration
- **Location**: `C:\Users\muaro\Documents\GitHub\Agent-For-TOM\agent.py`
- **Current memory**: `.agent/memory/` (project-local, via `PROJECT_DIR`)
- **Needed**: Move to `~/.tomas/memory/` (user-global)
- **Key constant to change**:
  - `MEMORY_DIR = PROJECT_DIR / ".agent" / "memory"` → `Path.home() / ".tomas" / "memory"`

---

## What Needs to Be Built

### 1. `self_notes.py` — New Module

**Purpose**: The agent can write notes about itself — lessons learned, patterns noticed, decisions made — and retrieve them later.

**Storage**: `~/.tomas/self-notes/`

**Note file format** (markdown with YAML frontmatter):
```markdown
---
id: note-20260101-120000-abc123
created_at: 1706793600.0
updated_at: 1706797200.0
type: lesson | pattern | decision | insight
tags: [python, debugging, performance]
source_session: session-abc123
auto_generated: false
---

# Note title

Content of the note...
```

**Functions needed**:
- `create_note(title, content, note_type="insight", tags=None, source_session=None)` — write a new note
- `list_notes(filter_type=None, tag=None)` — list notes with optional filtering
- `get_note(note_id)` — retrieve a specific note
- `search_notes(query)` — full-text search across notes
- `auto_generate_note(interactions, patterns, tips)` — analyze session data and auto-create a note
- `get_notes_for_context()` — return all notes as a string to inject into the system prompt

### 2. Migrate `self_improve.py` to `~/.tomas/self-improve/`

Change `SELF_IMPROVE_DIR` from `Path.cwd() / ".agent" / "self-improve"` to `Path.home() / ".tomas" / "self-improve"`.

This makes self-improvement data global across projects — patterns, tips, and skills learned in one project carry over to all projects.

### 3. Migrate `agent.py` memory to `~/.tomas/memory/`

Change `MEMORY_DIR` from `PROJECT_DIR / ".agent" / "memory"` to `Path.home() / ".tomas" / "memory"`.

This makes memory global across projects — facts remembered in one project are available in all projects.

### 4. Manual Trigger System (Slash Commands)

Add to `SLASH_COMMANDS` in `agent.py`:
- `/save [name]` — save current session to `~/.tomas/sessions/`
- `/load <id_or_name>` — load a saved session (replaces current messages)
- `/sessions` — list saved sessions
- `/note <title> <content>` — create a self-note manually
- `/notes` — list all self-notes
- `/self-improve` — force immediate self-improvement analysis (patterns, tips, skills, notes)

### 5. Agent Loop Integration

**In `agent.py` `main()`**:
- On exit (user types `quit`/`exit` or Ctrl+C): prompt "Save session before exiting? (y/n)"
- On `/save`: call `session_manager.save_session(messages)`
- On `/load`: call `session_manager.load_session(id)` and replace `messages`
- On `/self-improve`: call `self_improve.analyze_patterns()`, `self_improve.generate_tips()`, `self_improve.generate_skills_for_all_ready_patterns()`, and `self_notes.auto_generate_note()`
- On `/note`: call `self_notes.create_note()`
- On `/notes`: call `self_notes.list_notes()` and display

### 6. Menu Integration (`agent_cli.py`)

**New menu item**: Insert after "Check available tools" (index 7):
```
f'  {CYAN}◈{RESET}  Sessions & Notes'
```

**New page function** `page_sessions_notes()`:
- Sub-menu with options:
  1. View saved sessions
  2. Load a session
  3. Save current session
  4. Delete a session
  5. View self-notes
  6. Create a self-note
  7. Run self-improvement analysis
  8. Back to main menu

### 7. Installer Integration

**Changes to `install.ps1`**:
- Add creation of `~/.tomas/sessions/`, `~/.tomas/self-improve/`, `~/.tomas/memory/`, `~/.tomas/self-notes/` directories after source copy
- These directories are also created on first run by Python code (`mkdir(parents=True, exist_ok=True)`), but explicit creation in the installer ensures they exist immediately

**Changes to `install.sh`**:
- Same as above — add `mkdir -p` for the new directories

**No changes needed** to source file copying — `session_manager.py`, `instructions_manager.py`, and the new `self_notes.py` are all in the project root and will be copied to `~/.tomas/src/` automatically.

### 8. `.gitignore` Update

No changes needed. `.agent/` stays gitignored (project-local). `~/.tomas/` is already gitignored. The new `~/.tomas/self-improve/`, `~/.tomas/memory/`, and `~/.tomas/self-notes/` directories are inside `.tomas/` which is already gitignored.

---

## Design Decisions

### Why `~/.tomas/` instead of `.agent/`?
- `.agent/` is project-local — data is lost when you switch projects
- `~/.tomas/` is user-global — self-improvement data persists across all projects
- Sessions should be available regardless of which project you're working on
- Patterns and tips learned in one project benefit all projects
- This matches the existing pattern: `session_manager.py` and `instructions_manager.py` already use `~/.tomas/`

### Why keep `.agent/` for anything?
- `.agent/` can still be used for project-specific generated files (e.g., `.agent/self-improve/` could remain for project-local pattern data if desired)
- But for the self-improving system, global storage in `~/.tomas/` is the right choice

### Why separate `self_notes.py` from `self_improve.py`?
- `self_improve.py` handles pattern detection, skill generation, and tips — it's about the agent's behavior patterns
- `self_notes.py` handles the agent's self-reflection — lessons, decisions, insights
- Separation of concerns keeps each module focused and testable
- The two systems are linked (notes can reference sessions, self-improve can generate notes)

### Why JSON for sessions and Markdown for notes?
- Sessions contain structured data (messages array, metadata) — JSON is natural
- Notes are human-readable and editable — Markdown is ideal
- Both formats are consistent with existing patterns (`~/.tomas/sessions/*.json`, `~/.tomas/instructions/*.md`)

### Auto-naming sessions?
- Use the first user message (truncated to 60 chars) as the default name
- Allow the user to override with `/save My Session Name`
- This matches the pattern of other tools (Claude Code, etc.)

### When does auto-note-taking happen?
- After every 10 interactions, the agent auto-generates a note if patterns are detected
- The `/self-improve` command forces immediate analysis and note generation
- On session save, a summary note is auto-generated

---

## File Changes Summary

| File | Action | Description |
|------|--------|-------------|
| `self_notes.py` | **Create** | New module for self-reflection notes (`~/.tomas/self-notes/`) |
| `self_improve.py` | **Modify** | Change `SELF_IMPROVE_DIR` from `.agent/self-improve/` to `~/.tomas/self-improve/` |
| `agent.py` | **Modify** | Change `MEMORY_DIR` from `.agent/memory/` to `~/.tomas/memory/`; add slash commands for save/load/sessions/notes/self-improve |
| `agent_cli.py` | **Modify** | Add "Sessions & Notes" menu item and `page_sessions_notes()` function |
| `install.ps1` | **Modify** | Add explicit creation of `~/.tomas/sessions/`, `self-improve/`, `memory/`, `self-notes/` dirs |
| `install.sh` | **Modify** | Same as above |
| `requirements.txt` | **No change** | No new dependencies needed |

---

## Implementation Order

1. **Modify `self_improve.py`** — change `SELF_IMPROVE_DIR` to `~/.tomas/self-improve/`
2. **Modify `agent.py`** — change `MEMORY_DIR` to `~/.tomas/memory/`; add slash commands (`/save`, `/load`, `/sessions`, `/note`, `/notes`, `/self-improve`); integrate session save on exit
3. **Create `self_notes.py`** — note CRUD + auto-generation using `~/.tomas/self-notes/`
4. **Modify `agent_cli.py`** — add "Sessions & Notes" menu item and `page_sessions_notes()` function
5. **Modify `install.ps1`** — add directory creation for `sessions/`, `self-improve/`, `memory/`, `self-notes/`
6. **Modify `install.sh`** — same as above
7. **Test** — verify all slash commands work, session save/load persists across runs, notes are created and retrievable, data is stored in `~/.tomas/`

---

## Open Questions

1. **Session message limit**: Should sessions be truncated if they exceed a certain size (e.g., 1000 messages or 50MB)? The existing `maybe_compact()` already handles context window limits, but saved sessions could grow large over many sessions.
2. **Note auto-generation threshold**: How often should the agent auto-generate notes? Every 10 interactions? Every session save? The plan says every 10 interactions, but this could be tuned.
3. **Cross-session notes**: Should notes from previous sessions be injected into the system prompt when a session is loaded? This would give the agent continuity of self-knowledge across sessions.
4. **Memory migration**: When migrating from `.agent/memory/` to `~/.tomas/memory/`, should existing memory files be copied or left in place? The safe approach is to copy on first run to the new location.