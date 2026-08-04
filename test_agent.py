#!/usr/bin/env python3
"""
Non-interactive test suite for TOMAS agent.
Exercises: built-in tools, session system, skills, MCP, agent loop.
Run: python test_agent.py
"""
from __future__ import annotations

import os
import sys
import json
import tempfile
import traceback
from pathlib import Path

# Force project dir
PROJECT_DIR = Path(__file__).parent.resolve()
os.chdir(PROJECT_DIR)
sys.path.insert(0, str(PROJECT_DIR))

# Force UTF-8 encoding on stdout for Windows compatibility
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Load env
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

PASS = "\033[92m✓ PASS\033[0m"
FAIL = "\033[91m✗ FAIL\033[0m"
INFO = "\033[96mℹ\033[0m"
SECTION = "\033[95m▌\033[0m"

results = {"pass": 0, "fail": 0, "errors": []}


def check(name: str, cond: bool, detail: str = ""):
    if cond:
        results["pass"] += 1
        print(f"  {PASS} {name}")
    else:
        results["fail"] += 1
        results["errors"].append(f"{name}: {detail}")
        print(f"  {FAIL} {name}  {detail}")


def section(title: str):
    print(f"\n{SECTION} {title}")


# ─────────────────────────────────────────────────────────────────────
# 1. Built-in tool handlers
# ─────────────────────────────────────────────────────────────────────
def test_builtin_tools():
    section("Built-in Tools")
    import agent

    # read_file
    out = agent.handle_read_file({"file_path": "CLAUDE.md"})
    check("read_file returns content", "# CLAUDE.md" in out, out[:80])

    # read_file missing
    out = agent.handle_read_file({"file_path": "nonexistent_xyz.py"})
    check("read_file missing → error", "not found" in out, out[:80])

    # list_files
    out = agent.handle_list_files({"path": "."})
    check("list_files lists project", "agent.py" in out, out[:80])

    # search_code
    out = agent.handle_search_code({"pattern": "def main"})
    check("search_code finds main", "main" in out, out[:80])

    # write_file + edit_file roundtrip
    test_file = "test_tmp_file.txt"
    agent.handle_write_file({"file_path": test_file, "content": "hello world"})
    out = agent.handle_read_file({"file_path": test_file})
    check("write_file creates file", "hello world" in out, out[:80])
    agent.handle_edit_file({"file_path": test_file, "old_string": "hello", "new_string": "goodbye"})
    out = agent.handle_read_file({"file_path": test_file})
    check("edit_file replaces text", "goodbye world" in out, out[:80])
    # edit_file non-unique
    agent.handle_write_file({"file_path": test_file, "content": "dup\ndup\ndup"})
    out = agent.handle_edit_file({"file_path": test_file, "old_string": "dup", "new_string": "x"})
    check("edit_file rejects non-unique", "matches 3" in out, out[:80])
    Path(test_file).unlink(missing_ok=True)

    # run_command
    out = agent.handle_run_command({"command": "echo hello_from_cmd"})
    check("run_command executes", "hello_from_cmd" in out, out[:80])
    # blocked command
    out = agent.handle_run_command({"command": "rm -rf /"})
    check("run_command blocks dangerous", "blocked" in out, out[:80])

    # save_memory
    out = agent.handle_save_memory({"key": "test-key", "description": "test desc", "content": "test content"})
    check("save_memory works", "Saved memory" in out, out[:80])
    mem_file = Path.home() / ".tomas" / "memory" / "test-key.md"
    check("save_memory writes file", mem_file.exists())

    # fetch_url
    out = agent.handle_fetch_url({"url": "https://example.com"})
    check("fetch_url fetches example.com", "Example Domain" in out or "example" in out.lower(), out[:120])

    # search_web (may fail if ddgs not installed)
    try:
        out = agent.handle_search_web({"query": "python programming", "max_results": 2})
        check("search_web returns results", "results" in out.lower() or "Error" in out, out[:120])
    except Exception as e:
        check("search_web (skipped)", False, str(e))

    # execute_tool dispatch
    out = agent.execute_tool("read_file", {"file_path": "CLAUDE.md"})
    check("execute_tool dispatches", "# CLAUDE.md" in out, out[:80])
    out = agent.execute_tool("unknown_tool_xyz", {})
    check("execute_tool unknown → error", "unknown tool" in out.lower() or "error" in out.lower(), out[:80])


# ─────────────────────────────────────────────────────────────────────
# 2. Session system
# ─────────────────────────────────────────────────────────────────────
def test_sessions():
    section("Session System")
    from session_manager import (
        save_session, list_sessions, load_session,
        continue_session, delete_session, get_session_count,
    )

    # Save a session
    test_msgs = [
        {"role": "user", "content": "Hello, this is a test conversation."},
        {"role": "assistant", "content": "Hi! I'm TOMAS, ready to help."},
        {"role": "user", "content": "What can you do?"},
        {"role": "assistant", "content": "I can read/write files, run commands, and more."},
    ]
    sid = save_session(test_msgs, summary="Test session for validation", model="test-model")
    check("save_session returns id", bool(sid), sid)

    # List sessions
    sessions = list_sessions(limit=50)
    check("list_sessions returns list", len(sessions) > 0, f"{len(sessions)} sessions")
    found = any(s.get("id") == sid for s in sessions)
    check("saved session appears in list", found, sid)

    # Load session
    data = load_session(sid)
    check("load_session returns data", data is not None)
    if data:
        check("load_session has messages", data.get("message_count") == 4, str(data.get("message_count")))
        check("load_session has summary", "test" in data.get("summary", "").lower())

    # Continue session
    loaded = continue_session(sid)
    check("continue_session returns messages", loaded is not None and len(loaded) == 4, str(len(loaded) if loaded else 0))

    # Overwrite/continue save
    sid2 = save_session(test_msgs + [{"role": "user", "content": "more"}], session_id=sid)
    check("save_session overwrite keeps id", sid2 == sid, f"{sid} != {sid2}")
    data2 = load_session(sid)
    check("overwritten session has 5 msgs", data2 and data2.get("message_count") == 5, str(data2.get("message_count") if data2 else None))

    # Delete
    ok = delete_session(sid)
    check("delete_session removes", ok)
    check("deleted session gone", load_session(sid) is None)

    # Count
    count = get_session_count()
    check("get_session_count returns int", isinstance(count, int) and count >= 0, str(count))


# ─────────────────────────────────────────────────────────────────────
# 3. Skills manager
# ─────────────────────────────────────────────────────────────────────
def test_skills():
    section("Skills Manager")
    from skills_manager import discover_skills, build_skills_section, cmd_skill_list

    skills = discover_skills()
    check("discover_skills returns list", isinstance(skills, list), f"{len(skills)} skills")
    if skills:
        s = skills[0]
        check("skill has name", bool(s.get("name")), str(s.get("name")))
        check("skill has file", "file" in s)
        check("skill has content", bool(s.get("content")))

    section_str = build_skills_section()
    check("build_skills_section returns str", isinstance(section_str, str))

    listing = cmd_skill_list()
    check("cmd_skill_list returns str", isinstance(listing, str))


# ─────────────────────────────────────────────────────────────────────
# 4. MCP manager
# ─────────────────────────────────────────────────────────────────────
def test_mcp():
    section("MCP Manager")
    from mcp_manager import read_mcp_servers, MCPManager

    servers = read_mcp_servers()
    check("read_mcp_servers returns dict", isinstance(servers, dict), f"{len(servers)} servers")
    print(f"  {INFO} Configured MCP servers: {list(servers.keys())}")

    # Try to connect (may fail if npx/node not available)
    try:
        mgr = MCPManager()
        mgr.discover_and_connect(config=servers)
        check("MCPManager connects", isinstance(mgr.tools, list), f"{len(mgr.tools)} tools")
        if mgr.tools:
            t = mgr.tools[0]
            check("MCP tool has name", bool(t.get("name")), str(t.get("name")))
            check("MCP tool has schema", "input_schema" in t, str(t.get("name")))
        if mgr.failed_servers:
            print(f"  {INFO} Failed MCP servers: {mgr.failed_servers}")
        mgr.disconnect_all()
    except Exception as e:
        check("MCPManager init", False, str(e))


# ─────────────────────────────────────────────────────────────────────
# 5. Agent loop (real conversation)
# ─────────────────────────────────────────────────────────────────────
def test_agent_loop():
    section("Agent Loop (live API call)")
    import agent

    # Ensure a provider is active — prefer OpenRouter (more reliable than Zen free tier)
    if not os.environ.get("ANTHROPIC_API_KEY") or "zen-proxy" in os.environ.get("ANTHROPIC_API_KEY", ""):
        try:
            cfg = json.loads((PROJECT_DIR / "providers.json").read_text(encoding="utf-8"))
            providers = cfg.get("providers", {})
            # Prefer OpenRouter over Zen for testing
            for name, prov in providers.items():
                if prov.get("type") == "openrouter" and prov.get("env"):
                    for k, v in prov["env"].items():
                        os.environ[k] = v
                    if prov.get("model"):
                        os.environ["AGENT_MODEL"] = prov["model"]
                    print(f"  {INFO} Using OpenRouter for live API test: {prov.get('model')}")
                    break
            else:
                # Fall back to Zen if OpenRouter not configured
                active = cfg.get("active")
                prov = providers.get(active, {})
                if prov.get("type") == "zen":
                    print(f"  {INFO} Only Zen available (may be rate-limited)")
                    os.environ["ANTHROPIC_API_KEY"] = "zen-proxy-key"
                    os.environ["ANTHROPIC_BASE_URL"] = "http://127.0.0.1:6446"
                    os.environ["AGENT_MODEL"] = prov.get("model", "deepseek-v4-flash-free")
        except Exception as e:
            print(f"  {INFO} Could not load provider: {e}")

    agent.reinit_client()
    model = agent._get_model()
    base = os.environ.get("ANTHROPIC_BASE_URL", "(none)")
    print(f"  {INFO} Model: {model}  Endpoint: {base}")

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(f"  {INFO} No API key — skipping live agent loop test")
        return

    # Test 1: simple greeting (no tools)
    msgs = [{"role": "user", "content": "Reply with exactly: PONG"}]
    try:
        agent.COMBINED_TOOLS = agent.TOOLS  # built-ins only
        agent.TOOL_TOKENS = sum(len(json.dumps(t)) for t in agent.TOOLS) // 6
        agent.mcp_manager = None
        agent.MCP_TOOL_NAME_MAP = {}
        result = agent.agent_loop("You are a test assistant. Reply briefly.", msgs)
        check("agent_loop greeting returns text", bool(result) and "PONG" in result.upper(), (result or "")[:120])
        print(f"  {INFO} Response: {(result or '')[:200]}")
    except Exception as e:
        check("agent_loop greeting", False, f"{type(e).__name__}: {e}")

    # Test 2: tool-calling conversation
    msgs2 = [{"role": "user", "content": "Read the file CLAUDE.md and tell me the first line that contains 'guidelines'. Use the read_file tool."}]
    try:
        result2 = agent.agent_loop("You are a test assistant. Use tools when asked.", msgs2)
        check("agent_loop tool call returns text", bool(result2), (result2 or "")[:120])
        # Check that a tool was actually called (messages grew)
        check("agent_loop invoked tool", len(msgs2) > 1, f"{len(msgs2)} messages after tool call")
        print(f"  {INFO} Tool-call response: {(result2 or '')[:200]}")
    except Exception as e:
        check("agent_loop tool call", False, f"{type(e).__name__}: {e}")


# ─────────────────────────────────────────────────────────────────────
# 6. Session continuation display (the bug)
# ─────────────────────────────────────────────────────────────────────
def test_session_continuation_display():
    section("Session Continuation Display")
    # Verify that main() prints the conversation when continuing.
    # We inspect the source to confirm whether prior messages are shown.
    src = (PROJECT_DIR / "agent.py").read_text(encoding="utf-8")
    has_continue_load = "continue_session(CONTINUE_SESSION_ID)" in src
    check("main() loads continued session", has_continue_load)
    # The bug: it only prints "Continuing session... (N messages)" but NOT the conversation
    shows_conversation = "Continuing session" in src and _prints_full_conversation(src)
    check("main() shows full conversation on continue", shows_conversation,
          "Only prints count, not the messages")


def _prints_full_conversation(src: str) -> bool:
    """Heuristic: does the continue block print the full conversation?"""
    # Either an inline loop over messages, OR a call to a helper that does.
    idx = src.find("if CONTINUE_SESSION_ID:")
    if idx < 0:
        return False
    block = src[idx:idx + 800]
    has_inline = ("for " in block and "messages" in block and "print" in block
                  and "role" in block)
    has_helper = "_print_conversation_history" in block
    return has_inline or has_helper


# ─────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"\n{SECTION} TOMAS Agent Test Suite\n")
    try:
        test_builtin_tools()
    except Exception:
        print(f"  {FAIL} Built-in tools crashed")
        traceback.print_exc()
    try:
        test_sessions()
    except Exception:
        print(f"  {FAIL} Sessions crashed")
        traceback.print_exc()
    try:
        test_skills()
    except Exception:
        print(f"  {FAIL} Skills crashed")
        traceback.print_exc()
    try:
        test_mcp()
    except Exception:
        print(f"  {FAIL} MCP crashed")
        traceback.print_exc()
    try:
        test_session_continuation_display()
    except Exception:
        print(f"  {FAIL} Session continuation test crashed")
        traceback.print_exc()
    try:
        test_agent_loop()
    except Exception:
        print(f"  {FAIL} Agent loop crashed")
        traceback.print_exc()

    print(f"\n{SECTION} Summary")
    print(f"  Passed: {results['pass']}")
    print(f"  Failed: {results['fail']}")
    if results["errors"]:
        print(f"\n  Failures:")
        for e in results["errors"]:
            print(f"    - {e}")
    sys.exit(0 if results["fail"] == 0 else 1)