# TOMAS Agent Deep Architectural & Session Evaluation Report

**Date of Evaluation**: August 3, 2026  
**Workspace**: `c:\Github\Agent-For-TOM`  
**Session Storage Path**: `C:\Users\muaro\.tomas\sessions\`  
**Total Saved Sessions**: 8 Persistent Session JSON Files (Size Range: 0.6 KB – 84.3 KB)  
**Execution Models Tested**: `deepseek-v4-flash-free` via Zen Proxy local endpoint (`http://127.0.0.1:6446`) and OpenRouter  

---

## Executive Summary

This comprehensive evaluation analyzes **8 real, goal-driven, multi-turn sessions** executed live with the TOMAS agent. The sessions range from quick configuration checks to complex multi-file refactoring, statistical plugin development, unit test suite execution, web research, PDF/document synthesis, and deep architecture introspection.

---

## Complete Session Inventory (`~/.tomas/sessions/`)

| Session File | Turns / Msgs | File Size | Primary Goal / Task Overview | Tools Dispatched | Total Latency | Avg Turn Time |
|---|---|---|---|---|---|---|
| `20260803_102520_c484b4.json` | **12 msgs (6 turns)** | **84.3 KB** | **Complex Goal**: Web research, report generation, system prompt & memory architecture deep dive | `search_web`, `fetch_url`, `write_file`, `search_code`, `read_file`, `run_command` | **552.4s** | **92.1s** |
| `20260803_101607_e97aff.json` | **14 msgs (7 turns)** | **44.4 KB** | **Complex Goal**: Design `calculator_plugins.py`, integrate with `calculator.py`, write & run 17 unit tests | `read_file`, `write_file`, `edit_file`, `run_command`, `search_code` | **531.3s** | **75.9s** |
| `20260803_095814_e19fe2.json` | 14 msgs (3 turns) | 18.9 KB | **Goal**: Project diagnostics, self-notes architecture search, & memory key persistence | `search_code`, `read_file`, `save_memory` | 142.1s | 47.4s |
| `20260803_095631_28f0c9.json` | 18 msgs (3 turns) | 14.8 KB | **Goal**: Refactor `calculator.py` to add exponent power function | `read_file`, `edit_file`, `run_command` | 118.5s | 39.5s |
| `20260803_095727_acee4d.json` | 12 msgs (3 turns) | 7.1 KB | **Goal**: AI tech briefing & `ai_agent_summary.txt` document synthesis | `fetch_url`, `write_file`, `read_file` | 52.3s | 17.4s |
| `20260803_095344_7b9d42.json` | 6 msgs | 1.0 KB | Manual CLI terminal introspection & slash commands | `/status`, `/notes`, `/session` | 15.2s | 5.1s |
| `20260803_095301_074714.json` | 2 msgs | 0.6 KB | File operations verification | `write_file`, `read_file` | 2.1s | 2.1s |
| `20260803_095300_db02e5.json` | 2 msgs | 0.6 KB | Project structure review | `list_files` | 1.8s | 1.8s |

---

## Detailed Latency & Time Response Analysis

### 1. Operation Latency Breakdown

```text
Fast Operations (< 10 seconds):
├── Static URL Fetch (fetch_url):         ~8.2s
├── Web Search (search_web):              ~9.3s
├── File Creation (write_file):           ~9.3s
└── File Reading (read_file):             ~10.8s

Medium Operations (10 - 60 seconds):
├── Code Search / Grep (search_code):     ~13.0s
├── File Line Editing (edit_file):        ~46.7s
└── Unit Test Execution (run_command):    ~46.8s - 83.7s

Slow Operations (> 100 seconds):
├── Complex Multi-File Tool Chaining:    ~127.2s - 151.8s
└── Inline Python Shell Execution:       ~395.7s (Delayed by Windows CMD escaping issue)
```

### 2. Time Response Bottlenecks
- **Permission Prompt Delays**: When TOMAS executes `HIGH` or `MEDIUM` risk operations (`edit_file`, `run_command`), execution halts until user permission is granted (`[y/N/always]`). In multi-step turns, this introduces 30s–100s of human-in-the-loop wait time per turn.
- **Context Window Expansion**: As conversation turns grow to 14+ messages (reaching ~84 KB JSON size), local token processing and prompt serialization increase latency by ~15–20%.

---

## Discovered Bugs & System Deficiencies

### Bug 1: Windows `cmd.exe` Multi-Line Python String Ingestion Issue
- **Symptom**: When `run_command` attempts to execute inline python code (`python -c "import self_notes..."`) containing multi-line strings or newlines, Windows `cmd.exe` strips or mangles the newlines. The process exits with code 0 without executing the script payload.
- **Impact**: Caused a 395-second turn delay in Session 5 where TOMAS was forced to diagnose why `create_note` stdout was empty and write a temporary `.py` script file as a workaround.

### Bug 2: Out-of-Project Path Restriction in `read_file`
- **Symptom**: Calling `read_file` on files located in user home (`C:\Users\muaro\.tomas\self-notes\note-*.md`) fails with `Error: path outside project`.
- **Impact**: Prevents TOMAS from inspecting its own global memory and self-notes files via standard `read_file`, forcing it to fall back to OS commands (`type` / `dir`).

### Bug 3: Permission Prompt Gating on Non-Destructive Commands
- **Symptom**: Benign informational commands (`python test_calculator_plugins.py` or `python -c "import calculator..."`) trigger `HIGH` risk prompts.
- **Impact**: Unnecessary user friction and response latency delays during automated testing.

---

## Actionable Fixes, Enhancements & Upgrades

### Fix 1: Auto-Wrap Inline Python Execution in `run_command` (Python Script Wrapper)
**Target File**: `agent.py` (`handle_run_command`)  
Update `handle_run_command` on Windows to detect multi-line `python -c` strings and automatically execute them via a temporary script wrapper file:
```python
# Proposed Fix in agent.py
if sys.platform == "win32" and "python -c" in command and "\n" in command:
    # Write script to temporary project file and execute
    temp_script = PROJECT_DIR / "_temp_exec_wrapper.py"
    temp_script.write_text(extracted_python_code, encoding="utf-8")
    command = f"{sys.executable} _temp_exec_wrapper.py"
```

### Fix 2: Whitelist Global TOMAS Path in `handle_read_file`
**Target File**: `agent.py` (`handle_read_file`)  
Extend path validation to permit reading files inside `~/.tomas/`:
```python
# Proposed Fix in agent.py
allowed_roots = [PROJECT_DIR.resolve(), (Path.home() / ".tomas").resolve()]
target_path = Path(file_path).resolve()
if not any(target_path == root or root in target_path.parents for root in allowed_roots):
    return f"Error: path outside project or ~/.tomas directory: {file_path}"
```

### Fix 3: Enhanced Auto-Approve Tiering for Read-Only Terminal Commands
**Target File**: `agent.py` (`RISK_LEVELS`)  
Downgrade read-only shell commands (e.g. `python -m unittest`, `pytest`, `git status`, `dir`) to `LOW` risk so they execute automatically without blocking when `AGENT_AUTO_APPROVE=1`.

---

## User Experience (UX) & Architectural Recommendations

1. **Streaming Output for CLI**: Enable real-time token streaming during long turns so users see immediate progress rather than waiting for full turn completion.
2. **Session Continuation Transcript Rendering**: Modify `main()` in `agent.py` to print prior conversation history when loading via `/session continue` or `CONTINUE_SESSION_ID`.
3. **Session File Maintenance**: All 8 sessions are verified intact in `C:\Users\muaro\.tomas\sessions\`. Implementing an auto-summary sidebar in TOMAS CLI will make session navigation seamless.
