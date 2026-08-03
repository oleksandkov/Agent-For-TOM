#!/usr/bin/env python3
"""
TOMAS simulation harness — the single entry point.

Replaces five root-level scripts (simulate_tomas_sessions.py,
run_goal_conversations.py, run_long_complex_sessions.py,
run_ultra_long_sessions.py, run_phase3_sessions.py) that did one job with
divergent conventions.

Two modes:

    python -m tests.simulate checks              # offline capability checks
    python -m tests.simulate sessions --turns 3  # live goal-driven sessions

The important change is how failure is reported. The old harness caught
AttributeError from a missing entry point and logged WARN, so a run that
called `agent.handle_fetch_browser` (a name that has never existed; the real
one is `fetch_url_with_browser`) came back green. A harness that cannot tell
"not implemented" from "misspelled" proves nothing.

Here every entry point is resolved up front with a bare getattr. A missing
name is a FAIL and stops the run. WARN is reserved for genuine environment
gaps — no network, no Playwright browser, no configured provider.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent.resolve()
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

import agent
import mcp_manager
import openai_adapter
import provider_manager
import self_improve
import self_notes
import session_manager
import skills_manager

GREEN, YELLOW, RED, DIM, RESET = (
    "\033[92m", "\033[93m", "\033[91m", "\033[2m", "\033[0m",
)


# ══════════════════════════════════════════════════════════════════════
#  Entry points — resolved strictly, before anything runs
# ══════════════════════════════════════════════════════════════════════

# (module, attribute) pairs the harness depends on. If the codebase renames
# one of these, the run fails here with the real name in the message, rather
# than degrading to a WARN 200 lines later.
REQUIRED_ENTRY_POINTS: list[tuple[object, str]] = [
    (agent, "handle_read_file"),
    (agent, "handle_write_file"),
    (agent, "handle_edit_file"),
    (agent, "handle_list_files"),
    (agent, "handle_run_command"),
    (agent, "handle_search_code"),
    (agent, "handle_save_memory"),
    (agent, "handle_fetch_url"),
    (agent, "handle_fetch_url_with_browser"),
    (agent, "handle_search_web"),
    (agent, "handle_read_mcp_resource"),
    (agent, "risk_for"),
    (agent, "resolve_mcp_tool_conflicts"),
    (agent, "apply_tool_cap"),
    (agent, "select_tools"),
    (agent, "tool_ceiling"),
    (agent, "degrade_capability"),
    (agent, "parse_text_tool_calls"),
    (agent, "build_system_prompt"),
    (agent, "agent_loop"),
    (agent, "reset_session_state"),
    (agent, "session_telemetry"),
    (mcp_manager, "MCPManager"),
    (mcp_manager, "read_mcp_servers"),
    (provider_manager, "list_providers"),
    (provider_manager, "get_active"),
    (provider_manager, "activate"),
    (provider_manager, "probe"),
    (provider_manager, "detect_type"),
    (provider_manager, "persist_capabilities"),
    (openai_adapter, "OpenAICompatAdapter"),
    (openai_adapter, "build_from_active"),
    (skills_manager, "improve_skill"),
    (skills_manager, "validate_frontmatter"),
    (session_manager, "save_session"),
    (session_manager, "load_session"),
    (session_manager, "audit_transcript"),
    (self_notes, "create_note"),
    (self_notes, "list_notes"),
    (self_improve, "record_tool_call"),
    (self_improve, "migrate_remove_generated"),
    (skills_manager, "discover_skills"),
    (skills_manager, "build_skills_section"),
]


class EntryPointError(RuntimeError):
    pass


def resolve_entry_points() -> dict[str, object]:
    """Resolve every entry point or fail. No defaults, no fallbacks."""
    resolved: dict[str, object] = {}
    missing: list[str] = []
    for module, attr in REQUIRED_ENTRY_POINTS:
        mod_name = getattr(module, "__name__", str(module))
        try:
            resolved[f"{mod_name}.{attr}"] = getattr(module, attr)
        except AttributeError:
            near = [n for n in dir(module) if attr.split("_")[-1] in n][:3]
            hint = f" (did you mean: {', '.join(near)}?)" if near else ""
            missing.append(f"{mod_name}.{attr}{hint}")
    if missing:
        raise EntryPointError(
            "harness entry points missing from the codebase:\n  - "
            + "\n  - ".join(missing)
        )
    return resolved


# ══════════════════════════════════════════════════════════════════════
#  Result recording
# ══════════════════════════════════════════════════════════════════════

class Results:
    def __init__(self) -> None:
        self.data = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "sections": {},
            "summary": {"total": 0, "passed": 0, "failed": 0, "warnings": 0},
        }

    def log(self, section: str, name: str, status: str,
            details: str = "", metrics: dict | None = None) -> None:
        self.data["sections"].setdefault(section, []).append({
            "name": name, "status": status,
            "details": details, "metrics": metrics or {},
        })
        self.data["summary"]["total"] += 1
        if status == "PASS":
            self.data["summary"]["passed"] += 1
            tag = f"{GREEN}[PASS]{RESET}"
        elif status == "WARN":
            self.data["summary"]["warnings"] += 1
            tag = f"{YELLOW}[WARN]{RESET}"
        else:
            self.data["summary"]["failed"] += 1
            tag = f"{RED}[FAIL]{RESET}"
        print(f"{tag} {section} -> {name}: {details[:130]}")

    def check(self, section: str, name: str, ok: bool, details: str = "",
              metrics: dict | None = None) -> bool:
        self.log(section, name, "PASS" if ok else "FAIL", details, metrics)
        return ok

    @property
    def failed(self) -> int:
        return self.data["summary"]["failed"]


# ══════════════════════════════════════════════════════════════════════
#  Checks — offline, deterministic
# ══════════════════════════════════════════════════════════════════════

def check_tool_layer(r: Results) -> None:
    s = "Tool layer"
    probe = PROJECT_DIR / "_sim_probe.txt"
    try:
        agent.handle_write_file({"file_path": str(probe), "content": "alpha\nbeta\n"})
        out = agent.handle_read_file({"file_path": str(probe)})
        r.check(s, "write/read round-trip", "alpha" in out and "beta" in out, out[:60])

        out = agent.handle_run_command({"command": f'python -c "import sys; print(\'x\'); sys.exit(3)"'})
        r.check(s, "exit code surfaced with stdout",
                "exit 3" in out and "x" in out, out.replace("\n", " ")[:80])

        out = agent.handle_run_command({"command": 'python -c "print(\'\\u2014\\u2192\')"'})
        r.check(s, "utf-8 round-trip", "\u2014\u2192" in out, repr(out)[:60])

        before = set(p.name for p in PROJECT_DIR.iterdir())
        agent.handle_run_command({"command": 'python -c "import sys\nprint(\'multi\')"'})
        after = set(p.name for p in PROJECT_DIR.iterdir())
        r.check(s, "no scratch files left in project", before == after,
                f"new: {sorted(after - before) or 'none'}")

        out = agent.handle_search_code({"pattern": "beta", "path": str(probe)})
        r.check(s, "search_code accepts a file path", "beta" in out, out[:60])

        out = agent.handle_search_code({"pattern": "x", "path": "definitely_missing.py"})
        r.check(s, "search_code errors on missing path", out.startswith("Error:"), out[:70])

        agent.handle_write_file({"file_path": str(probe), "content": "p()\np()\np()\n"})
        out = agent.handle_edit_file({"file_path": str(probe), "old_string": "p()",
                                      "new_string": "q()", "replace_all": True})
        r.check(s, "edit_file replace_all", "3 replacements" in out, out.splitlines()[0][:80])
    finally:
        probe.unlink(missing_ok=True)


def check_sandbox(r: Results) -> None:
    s = "Sandbox"
    sessions = list((Path.home() / ".tomas" / "sessions").glob("*.json"))
    if sessions:
        out = agent.handle_read_file({"file_path": str(sessions[0]), "limit": 1})
        r.check(s, "~/.tomas is readable", not out.startswith("Error:"), out[:50])
        out = agent.handle_write_file({"file_path": str(sessions[0]), "content": "x"})
        r.check(s, "~/.tomas is not writable", out.startswith("Error:"), out[:80])
    else:
        r.log(s, "~/.tomas readable", "WARN", "no sessions on disk to read")
    out = agent.handle_read_file({"file_path": "C:/Windows/System32/drivers/etc/hosts"
                                  if sys.platform == "win32" else "/etc/hosts"})
    r.check(s, "outside both roots is refused", out.startswith("Error:"), out[:70])


def check_risk_tiers(r: Results) -> None:
    s = "Permissions"
    cases = [
        ("git status", "low"), ("git status && rm -rf build", "high"),
        ("dir /b x & del y", "high"), ("python -m unittest discover", "low"),
        ("del probe.py", "high"), ("type note.md", "low"),
        ("python setup.py install", "high"),
    ]
    bad = [c for c, want in cases if agent.risk_for("run_command", {"command": c}) != want]
    r.check(s, "run_command classified per command", not bad, f"misclassified: {bad or 'none'}")


def check_mcp(r: Results) -> None:
    s = "MCP"
    servers = mcp_manager.read_mcp_servers()
    r.check(s, "server config discovered", isinstance(servers, dict),
            f"{len(servers)} servers", {"server_names": sorted(servers)[:20]})

    tools = [{"name": "read_file", "description": "d", "input_schema": {"type": "object"}}]
    resolved, name_map, renames = agent.resolve_mcp_tool_conflicts(tools)
    r.check(s, "builtin collision prefixed",
            renames == 1 and resolved[0]["name"] == "mcp_read_file", str(name_map))

    class _Srv:
        def __init__(self, name, tools):
            self.name, self.tools, self.calls, self._last_error = name, tools, [], None
        def connect(self): return True
        def call_tool(self, n, a): self.calls.append(n); return "ok"

    a = _Srv("srv_a", [{"name": "shared", "inputSchema": {"type": "object"}}])
    b = _Srv("srv_b", [{"name": "shared", "inputSchema": {"type": "object"}}])
    original = mcp_manager.MCPServer
    mcp_manager.MCPServer = lambda name, cfg: {"srv_a": a, "srv_b": b}[name]
    try:
        mgr = mcp_manager.MCPManager()
        mgr.discover_and_connect(config={"srv_a": {}, "srv_b": {}})
    finally:
        mcp_manager.MCPServer = original
    names = [t["name"] for t in mgr.tools]
    r.check(s, "no duplicate tool names", len(names) == len(set(names)), str(names))
    mgr.call_tool("shared", {})
    mgr.call_tool("mcp_srv_b_shared", {})
    r.check(s, "cross-server collision routes correctly",
            a.calls == ["shared"] and b.calls == ["shared"],
            f"a={a.calls} b={b.calls}")

    capped, dropped = agent.apply_tool_cap(
        [{"name": f"t{i}"} for i in range(200)], max_allowed=128)
    r.check(s, "tool cap enforced", len(capped) == 128 and dropped > 0,
            f"{len(capped)} tools, {dropped} dropped")


def check_sessions(r: Results) -> None:
    s = "Sessions"
    broken = [{"role": "user", "content": f"t{i}"} for i in range(3)]
    audit = session_manager.audit_transcript(broken)
    r.check(s, "orphaned turns detected", not audit["complete"], str(audit["orphaned_user_turns"]))

    good = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}]
    r.check(s, "complete transcript passes audit",
            session_manager.audit_transcript(good)["complete"], "")

    sid = session_manager.save_session(
        broken, summary="harness incompleteness probe", model="harness",
        token_usage={"input": 0, "output": 0, "calls": 0},
        telemetry={"turn_metrics": {}, "tool_log": [], "failed_turns": []})
    path = Path.home() / ".tomas" / "sessions" / f"{sid}.json"
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
        r.check(s, "incomplete session marked", saved.get("complete") is False,
                f"complete={saved.get('complete')}")
        r.check(s, "telemetry fields present",
                "turn_metrics" in saved and "tool_log" in saved, "")
    finally:
        path.unlink(missing_ok=True)

    agent.reset_session_state()
    r.check(s, "token usage resets per session",
            agent._session_tokens == {"input": 0, "output": 0, "calls": 0},
            str(agent._session_tokens))


def check_context_budget(r: Results) -> None:
    s = "Context budget"
    section = skills_manager.build_skills_section(max_chars=agent.MAX_SKILLS_CHARS)
    r.check(s, "skills section within budget", len(section) <= agent.MAX_SKILLS_CHARS,
            f"{len(section)} <= {agent.MAX_SKILLS_CHARS}")
    lines = [l for l in section.strip().splitlines() if l.startswith("- ")]
    r.check(s, "skills section ends on a whole entry",
            not lines or section.strip().endswith(lines[-1]), "")
    out = agent.handle_read_file({"file_path": "agent.py"})
    r.check(s, "read_file char-capped with resumable footer",
            len(out) <= agent.MAX_READ_FILE_CHARS + 200 and
            ("re-read with offset=" in out or len(out) < agent.MAX_READ_FILE_CHARS),
            f"{len(out)} chars")
    legacy = [x for x in skills_manager.discover_skills()
              if "self-improve" in str(x.get("file", ""))]
    r.check(s, "legacy generated skills off the discovery path", not legacy,
            f"{len(legacy)} found")


def check_learning(r: Results) -> None:
    s = "Learning"
    gone = [n for n in ("analyze_patterns", "generate_tips", "get_active_tips",
                        "generate_skill_for_pattern", "_maybe_analyze")
            if hasattr(self_improve, n)]
    r.check(s, "pattern generator removed", not gone, f"still present: {gone or 'none'}")
    r.check(s, "interaction log preserved for learning/",
            hasattr(self_improve, "record_tool_call") and
            hasattr(self_improve, "get_all_interactions"), "")
    skills_dir = Path.home() / ".tomas" / "self-improve" / "skills"
    r.check(s, "generated skills migrated off disk", not skills_dir.exists(),
            str(skills_dir))
    note_id = self_notes.create_note(
        title="Harness probe", content="written by tests/simulate.py",
        note_type="insight", tags=["harness"])
    r.check(s, "self-notes writable", bool(note_id), str(note_id))


def check_web(r: Results) -> None:
    """Network-dependent. WARN here is legitimate — this is environment."""
    s = "Web"
    try:
        out = agent.handle_fetch_url({"url": "https://example.com", "timeout": 15})
        if out.startswith("Error:"):
            r.log(s, "fetch_url", "WARN", f"no network: {out[:80]}")
        else:
            r.check(s, "fetch_url", "Example Domain" in out, f"{len(out)} chars")
    except Exception as e:
        r.log(s, "fetch_url", "WARN", f"no network: {e}")
    try:
        out = agent.handle_search_web({"query": "python", "max_results": 3})
        if out.startswith("Error:"):
            r.log(s, "search_web", "WARN", f"unavailable: {out[:80]}")
        else:
            r.check(s, "search_web", len(out) > 40, f"{len(out)} chars")
    except Exception as e:
        r.log(s, "search_web", "WARN", f"unavailable: {e}")


def check_providers(r: Results) -> None:
    s = "Providers"
    from tests import stub_provider
    srv, url = stub_provider.serve()
    try:
        p = provider_manager.Provider(name="_harness_stub", type="custom",
                                      base_url=url, model="stub-model")
        caps = provider_manager.probe(p)
        r.check(s, "capabilities are probed, not guessed",
                caps.probed and caps.context_window == 31337,
                f"ctx={caps.context_window} max_tools={caps.max_tools}")

        adapter = openai_adapter.OpenAICompatAdapter(url, "k")
        reply = adapter.messages.create(
            model="stub-model", max_tokens=16, system="s",
            messages=[{"role": "user", "content": "hi"}], tools=[])
        r.check(s, "in-process adapter completes a blocking call",
                reply.content[0].text == "blocking reply", reply.content[0].text)

        deltas = []
        with adapter.messages.stream(model="stub-model", max_tokens=16, system="s",
                                     messages=[{"role": "user", "content": "hi"}],
                                     tools=[]) as st:
            for ev in st:
                if ev.type == "content_block_delta":
                    deltas.append(ev.delta.text)
        r.check(s, "streaming is incremental, not replayed",
                len(deltas) > 1, f"{len(deltas)} deltas: {deltas}")
    finally:
        srv.shutdown()

    srv2, url2 = stub_provider.serve(stub_provider.NoStreamStub)
    try:
        p2 = provider_manager.Provider(name="_ns", type="custom",
                                       base_url=url2, model="stub-model")
        r.check(s, "a non-streaming endpoint is detected at probe time",
                not provider_manager.probe(p2).streaming, "")
    finally:
        srv2.shutdown()

    dead = provider_manager.Provider(name="_dead", type="custom",
                                     base_url="http://127.0.0.1:1/v1", model="m")
    caps = provider_manager.probe(dead)
    r.check(s, "probing an unreachable endpoint degrades, does not raise",
            caps.streaming and caps.tool_use, "")

    for url_, want in (("https://openrouter.ai/api/v1", "openrouter"),
                       ("http://localhost:11434/v1", "ollama"),
                       ("https://llm.internal.corp/v1", "custom")):
        if provider_manager.detect_type(url_) != want:
            r.check(s, "type detection", False, f"{url_} -> {provider_manager.detect_type(url_)}")
            break
    else:
        r.check(s, "endpoint types resolve (unknown ones are 'custom', not broken)", True, "")


def check_tool_selection(r: Results) -> None:
    s = "Tool selection"
    mk = lambda n, d: {"name": n, "description": d,
                       "input_schema": {"type": "object", "properties": {}}}
    pool = agent.TOOLS + [mk("take_screenshot", "screenshot the browser page"),
                          mk("sql_query", "run a sql query on postgres"),
                          mk("send_email", "send an email")]
    budget = len(agent.TOOLS) + 1
    builtin = {t["name"] for t in agent.TOOLS}
    pick = lambda ctx: [t["name"] for t in
                        agent.select_tools(pool, ctx, budget)[0]
                        if t["name"] not in builtin]
    r.check(s, "browser question selects browser tools",
            pick("screenshot the browser") == ["take_screenshot"], str(pick("screenshot the browser")))
    r.check(s, "database question selects database tools",
            pick("run a sql query") == ["sql_query"], str(pick("run a sql query")))
    _, withheld = agent.select_tools(pool, "screenshot", budget)
    r.check(s, "withheld tools are announced to the model",
            "not loaded" in agent.withheld_tools_notice(withheld), "")


def check_skill_format(r: Results) -> None:
    s = "Skills"
    import tempfile
    from pathlib import Path as _P
    saved = skills_manager.SKILL_DIRS
    with tempfile.TemporaryDirectory() as tmp:
        d = _P(tmp)
        skills_manager.SKILL_DIRS = [d]
        try:
            skills_manager.write_skill(
                d / "probe.md",
                {"name": "probe", "description": "d", "triggers": ["t"],
                 "source": "learned", "version": 1}, "body one")
            (d / "broken.md").write_text("---\nversion: nope\n---\nb\n", encoding="utf-8")
            found = skills_manager.discover_skills()
            r.check(s, "malformed frontmatter is tolerated, not fatal",
                    len(found) == 2 and any(x["problems"] for x in found),
                    f"{len(found)} discovered")
            first = [x for x in found if x["name"] == "probe"][0]
            r.check(s, "bodies load on demand", "content" not in dict.keys(first), "")
            skills_manager.improve_skill("probe", "body two")
            after = skills_manager.find_skill("probe")
            r.check(s, "improve bumps version and keeps provenance",
                    after["version"] == 2 and after["source"] == "learned"
                    and "body one" in after["content"] and "body two" in after["content"],
                    f"v{after['version']} source={after['source']}")
        finally:
            skills_manager.SKILL_DIRS = saved


def run_checks(args: argparse.Namespace) -> int:
    r = Results()
    check_tool_layer(r)
    check_sandbox(r)
    check_risk_tiers(r)
    check_mcp(r)
    check_sessions(r)
    check_context_budget(r)
    check_learning(r)
    check_providers(r)
    check_tool_selection(r)
    check_skill_format(r)
    if not args.offline:
        check_web(r)

    sm = r.data["summary"]
    print(f"\n{'=' * 62}")
    print(f" checks: {sm['total']}  passed: {sm['passed']}  "
          f"warnings: {sm['warnings']}  failed: {sm['failed']}")
    print("=" * 62)
    out = Path(args.out) if args.out else PROJECT_DIR / "simulation_results.json"
    out.write_text(json.dumps(r.data, indent=2), encoding="utf-8")
    print(f"wrote {out}")
    return 1 if r.failed else 0


# ══════════════════════════════════════════════════════════════════════
#  Sessions — live, needs a provider
# ══════════════════════════════════════════════════════════════════════

def run_sessions(args: argparse.Namespace) -> int:
    from tests.session_prompts import SESSIONS, by_name

    selected = SESSIONS
    if args.session:
        entry = by_name(args.session)
        if entry is None:
            print(f"{RED}unknown session: {args.session}{RESET}")
            print(f"available: {', '.join(s[0] for s in SESSIONS)}")
            return 1
        selected = [entry]

    agent.COMBINED_TOOLS = agent.TOOLS
    agent.TOOL_TOKENS = sum(len(json.dumps(t)) for t in agent.TOOLS) // 6

    failures = 0
    for name, goal, turns in selected:
        turns = turns[:args.turns] if args.turns else turns
        print(f"\n{'=' * 62}\n session: {name}\n goal:    {goal}\n{'=' * 62}")
        agent.reset_session_state()
        messages: list[dict] = []
        system_prompt = agent.build_system_prompt(goal)

        for i, prompt in enumerate(turns, 1):
            print(f"\n  [turn {i}/{len(turns)}] {prompt}")
            messages.append({"role": "user", "content": prompt})
            try:
                reply = agent.agent_loop(system_prompt, messages)
            except Exception as e:
                print(f"  {RED}turn failed:{RESET} {type(e).__name__}: {e}")
                reply = ""
            print(f"  -> {(reply or '(no reply)')[:160]}")

        telemetry = agent.session_telemetry()
        sid = session_manager.save_session(
            messages=messages, summary=f"harness: {goal}",
            model=agent._get_model(), token_usage=dict(agent._session_tokens),
            telemetry=telemetry)

        saved = json.loads((Path.home() / ".tomas" / "sessions" / f"{sid}.json")
                           .read_text(encoding="utf-8"))
        tm = saved.get("turn_metrics", {})
        status = f"{GREEN}complete{RESET}" if saved.get("complete") else f"{RED}INCOMPLETE{RESET}"
        if not saved.get("complete"):
            failures += 1
        print(f"\n  saved {sid} [{status}] "
              f"total {tm.get('total_duration_sec', 0)}s "
              f"avg {tm.get('avg_turn_sec', 0)}s "
              f"{len(saved.get('tool_log', []))} tool calls")

    return 1 if failures else 0


# ══════════════════════════════════════════════════════════════════════

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tests.simulate")
    sub = parser.add_subparsers(dest="mode", required=True)

    p_checks = sub.add_parser("checks", help="offline capability checks")
    p_checks.add_argument("--offline", action="store_true",
                          help="skip network-dependent checks")
    p_checks.add_argument("--out", help="where to write the results JSON")

    p_sessions = sub.add_parser("sessions", help="live goal-driven sessions")
    p_sessions.add_argument("--turns", type=int, default=0,
                            help="cap turns per session (0 = all)")
    p_sessions.add_argument("--session", help="run one session by name")

    args = parser.parse_args(argv)

    # Strict resolution happens before any mode runs.
    try:
        resolve_entry_points()
    except EntryPointError as e:
        print(f"{RED}[FAIL]{RESET} {e}")
        return 2
    print(f"{GREEN}[ok]{RESET} {len(REQUIRED_ENTRY_POINTS)} entry points resolved")

    return run_checks(args) if args.mode == "checks" else run_sessions(args)


if __name__ == "__main__":
    sys.exit(main())
