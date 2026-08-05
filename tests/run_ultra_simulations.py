#!/usr/bin/env python3
"""
Ultra-Long 30-Prompt Multi-Session TOMAS Agent Simulation Harness.
Runs 3 sessions with 30 user prompts per session (90 live AI turns total) against
the active provider (OpenCode Zen / laguna-s-2.1-free).

Measures:
1. Turn-by-turn precision of cross-session note & rule adherence over 30 turns.
2. Complete directory persistence audit across all ~/.tomas/ subdirectories.
3. Transcript audit completeness and telemetry accuracy across 90 turns.
"""

from __future__ import annotations

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
import session_manager
import self_notes
import learning
import provider_manager

def run_ultra_simulations():
    print("=" * 75)
    print("STARTING ULTRA-LONG 30-PROMPT MULTI-SESSION TOMAS SIMULATION HARNESS")
    print("=" * 75)

    os.environ["AGENT_MODEL"] = "laguna-s-2.1-free"
    os.environ["AGENT_AUTO_APPROVE"] = "1"
    agent.AUTO_APPROVE_LOW = True

    model_name = agent._get_model()
    print(f"Active Provider Model: {model_name}\n")

    session_records = []

    # =========================================================================
    # SESSION 1: 30-Prompt Developer Session & Storage Audit Setup
    # =========================================================================
    print("=" * 60)
    print("SESSION 1: Initializing Workspace & Registering Persistent Rules (30 Turns)")
    print("=" * 60)

    agent.reset_session_state()
    s1_messages = []

    s1_prompts = [
        "1. Hello TOMAS! Please list all top-level files in the project root.",
        "2. Read the first 25 lines of AGENTS.md to check our identity.",
        "3. Check which provider configurations exist by reading providers.json.",
        "4. /note persistent_rule Rule 1: Always include a '=== SUMMARY ===' section at top of responses. Rule 2: Always append date [2026-08-05] and 'My Lord' at the end.",
        "5. Save a memory with key 'python-style', description 'Python preference', content 'Always use PEP 484 type annotations in Python code.'",
        "6. Search for 'def resolve_mcp_tool_conflicts' in agent.py.",
        "7. Read lines 330-360 of agent.py to examine conflict resolution logic.",
        "8. Read lines 1-30 of instructions_manager.py.",
        "9. Read lines 1-35 of self_notes.py.",
        "10. Read lines 1-35 of learning/store.py.",
        "11. Read lines 1-35 of learning/retrieval.py.",
        "12. Read lines 1-35 of learning/reflect.py.",
        "13. Read lines 1-35 of self_improve.py.",
        "14. Read lines 1-35 of session_manager.py.",
        "15. Read lines 1-35 of skills_manager.py.",
        "16. Write a temporary scratch python script 'scratch/u1_test.py' with typed function compute_square(x: float) -> float.",
        "17. Run the script 'scratch/u1_test.py' using run_command.",
        "18. Read 'scratch/u1_test.py' to verify its contents.",
        "19. Search for 'generate_ai_news_pdf' in pdf_report_skill.py.",
        "20. Read lines 1-30 of zen_proxy.py.",
        "21. Summarize the main tools available in TOMAS.",
        "22. Feedback note: Ensure python code blocks specify ```python language tag.",
        "23. List all self-notes using /notes.",
        "24. Test web fetch on 'https://example.com' using fetch_url.",
        "25. Search code for 'def audit_transcript' in session_manager.py.",
        "26. Read lines 125-155 of session_manager.py.",
        "27. Remove 'scratch/u1_test.py' using run_command.",
        "28. Trigger session analysis update using /self-improve analyze.",
        "29. Show learned facts status using /self-improve facts.",
        "30. Conclude Session 1 and give a final summary of all actions taken."
    ]

    for turn_num, prompt in enumerate(s1_prompts, 1):
        print(f"\n[Session 1 - Turn {turn_num:02d}/30] User: {prompt[:80]}...")
        if prompt.startswith("4. /note") or prompt.startswith("23. /notes") or prompt.startswith("28. /self-improve") or prompt.startswith("29. /self-improve"):
            cmd_text = prompt.split(". ", 1)[1]
            res = agent.handle_slash_command(cmd_text[1:], s1_messages)
            if res and res != "__continue__":
                print(f"-> Slash output:\n{res[:150]}")
            continue

        s1_messages.append({"role": "user", "content": prompt})
        sys_prompt = agent.build_system_prompt(prompt)
        try:
            reply = agent.agent_loop(sys_prompt, s1_messages)
        except Exception as e:
            reply = f"Error: {e}"
        print(f"-> TOMAS Response Preview: {reply[:180].replace(chr(10), ' ')}...")

    s1_id = session_manager.save_session(
        messages=s1_messages,
        summary="Session 1: 30-turn developer session establishing persistent rules & storage audit",
        model=model_name,
        token_usage=dict(agent._session_tokens),
        telemetry=agent.session_telemetry()
    )
    s1_audit = session_manager.audit_transcript(s1_messages)
    session_records.append({
        "name": "Session 1",
        "id": s1_id,
        "turns": len(s1_prompts),
        "audit": s1_audit
    })
    print(f"\n✓ Session 1 saved: {s1_id} (Audit Complete = {s1_audit['complete']})")

    # =========================================================================
    # SESSION 2: 30-Prompt Fresh Session - Cross-Session Precision Test
    # =========================================================================
    print("\n" + "=" * 60)
    print("SESSION 2: Fresh Session Context - 30-Turn Cross-Session Precision Test")
    print("=" * 60)

    agent.reset_session_state()
    s2_messages = []

    s2_prompts = [
        "1. Hello TOMAS! I am starting a fresh session with you today.",
        "2. What is your primary purpose as an agent in this project?",
        "3. List top-level files in the workspace.",
        "4. Search for 'class MCPManager' in mcp_manager.py.",
        "5. Read lines 1-30 of mcp_manager.py.",
        "6. Do you recall any custom formatting rules or notes set in previous sessions?",
        "7. What Python coding style preference do I have saved in memory?",
        "8. Write a python utility in 'scratch/u2_factorial.py' to compute factorial. Use my preferred coding style.",
        "9. Read 'scratch/u2_factorial.py' to verify type annotations.",
        "10. Run 'scratch/u2_factorial.py' using run_command.",
        "11. Explain how instructions_manager.py loads global vs project instructions.",
        "12. Search for 'build_system_prompt' in agent.py.",
        "13. Read lines 1600-1630 of agent.py.",
        "14. Explain how select_tools handles tool budget constraints.",
        "15. List all active self-notes using /notes.",
        "16. Write a summary of how learning/retrieval.py scores facts.",
        "17. Explain how learning/store.py scopes facts between global and project.",
        "18. Search for 'def redact' in learning/store.py.",
        "19. Read lines 100-130 of learning/store.py.",
        "20. Search for 'def record_observation' in learning/promotion.py.",
        "21. Read lines 1-30 of learning/promotion.py.",
        "22. Search for 'def detect_correction_signals' in learning/corrections.py.",
        "23. Read lines 1-30 of learning/corrections.py.",
        "24. Clean up 'scratch/u2_factorial.py' using run_command.",
        "25. What are the 5 storage areas inside ~/.tomas/?",
        "26. Explain how session transcripts are audited for completeness.",
        "27. Show current mode status using /status.",
        "28. Have you maintained our requested response formatting rules throughout this 30-turn conversation?",
        "29. Summarize what has been accomplished in Session 2.",
        "30. Conclude Session 2."
    ]

    precision_hits = {
        "summary_header": 0,
        "date_appended": 0,
        "honorific_appended": 0,
        "pep484_typed": 0
    }

    for turn_num, prompt in enumerate(s2_prompts, 1):
        print(f"\n[Session 2 - Turn {turn_num:02d}/30] User: {prompt[:80]}...")

        if "15. /notes" in prompt or "27. /status" in prompt:
            cmd_text = prompt.split(". ", 1)[1]
            res = agent.handle_slash_command(cmd_text[1:], s2_messages)
            if res and res != "__continue__":
                print(f"-> Slash output:\n{res[:150]}")
            continue

        s2_messages.append({"role": "user", "content": prompt})
        sys_prompt = agent.build_system_prompt(prompt)
        try:
            reply = agent.agent_loop(sys_prompt, s2_messages)
        except Exception as e:
            reply = f"Error: {e}"

        has_summary = "=== SUMMARY ===" in reply or "SUMMARY" in reply
        has_date = "2026-08-05" in reply
        has_honorific = "My Lord" in reply or "Lord" in reply

        if has_summary: precision_hits["summary_header"] += 1
        if has_date: precision_hits["date_appended"] += 1
        if has_honorific: precision_hits["honorific_appended"] += 1

        print(f"-> TOMAS Response Preview: {reply[:180].replace(chr(10), ' ')}...")
        print(f"   [Precision Check] Summary={has_summary}, Date={has_date}, Honorific={has_honorific}")

    s2_id = session_manager.save_session(
        messages=s2_messages,
        summary="Session 2: 30-turn fresh session evaluating cross-session rule precision",
        model=model_name,
        token_usage=dict(agent._session_tokens),
        telemetry=agent.session_telemetry()
    )
    s2_audit = session_manager.audit_transcript(s2_messages)
    session_records.append({
        "name": "Session 2",
        "id": s2_id,
        "turns": len(s2_prompts),
        "audit": s2_audit,
        "precision": precision_hits
    })
    print(f"\n✓ Session 2 saved: {s2_id} (Audit Complete = {s2_audit['complete']})")
    print(f"✓ Session 2 Rule Precision Scores (out of 28 response turns):")
    print(f"   - Date Appended: {precision_hits['date_appended']}/28")
    print(f"   - Honorific Appended: {precision_hits['honorific_appended']}/28")
    print(f"   - Summary Header: {precision_hits['summary_header']}/28")

    # =========================================================================
    # SESSION 3: 30-Prompt Complex Workflow & Tool Execution Session
    # =========================================================================
    print("\n" + "=" * 60)
    print("SESSION 3: Complex Workflow, Tool Lifecycles & Reflection (30 Turns)")
    print("=" * 60)

    agent.reset_session_state()
    s3_messages = []

    s3_prompts = [
        "1. Search for 'def audit_transcript' in session_manager.py.",
        "2. Read lines 130-160 of session_manager.py.",
        "3. Search for 'def load_active_facts' in learning/store.py.",
        "4. Read lines 170-200 of learning/store.py.",
        "5. Search for 'def recency_boost' in learning/retrieval.py.",
        "6. Read lines 1-40 of learning/retrieval.py.",
        "7. Write scratch module 'scratch/u3_math.py' with typed functions add(a: float, b: float) -> float and multiply(a: float, b: float) -> float.",
        "8. Read 'scratch/u3_math.py'.",
        "9. Write test script 'scratch/test_u3_math.py' importing u3_math and running assertions.",
        "10. Run 'scratch/test_u3_math.py' via run_command.",
        "11. Perform Cyrillic query: 'Прочитай файл конфігурації та покажи результат'.",
        "12. Search for 'def handle_search_web' in agent.py.",
        "13. Read lines 1348-1375 of agent.py.",
        "14. Execute search_web for 'Python 3.12 release notes'.",
        "15. Fetch URL 'https://example.com' using fetch_url.",
        "16. Clean up 'scratch/u3_math.py' and 'scratch/test_u3_math.py' using run_command.",
        "17. Search for 'def execute_tool' in agent.py.",
        "18. Read lines 1450-1480 of agent.py.",
        "19. Search for 'def check_permission' in agent.py.",
        "20. Read lines 1470-1500 of agent.py.",
        "21. Search for 'def risk_for' in agent.py.",
        "22. Read lines 790-815 of agent.py.",
        "23. Trigger session analysis update using /self-improve analyze.",
        "24. Show learned facts status using /self-improve facts.",
        "25. Show reflection log status using /self-improve reflect.",
        "26. Show installed skills list using /skills.",
        "27. Show session list using /sessions list.",
        "28. Show current model status using /model.",
        "29. Show overall status using /status.",
        "30. Conclude Session 3 with a detailed technical summary."
    ]

    for turn_num, prompt in enumerate(s3_prompts, 1):
        print(f"\n[Session 3 - Turn {turn_num:02d}/30] User: {prompt[:80]}...")

        if any(prompt.startswith(f"{i}. /") for i in range(23, 30)):
            cmd_text = prompt.split(". ", 1)[1]
            res = agent.handle_slash_command(cmd_text[1:], s3_messages)
            if res and res != "__continue__":
                print(f"-> Slash output:\n{res[:150]}")
            continue

        s3_messages.append({"role": "user", "content": prompt})
        sys_prompt = agent.build_system_prompt(prompt)
        try:
            reply = agent.agent_loop(sys_prompt, s3_messages)
        except Exception as e:
            reply = f"Error: {e}"
        print(f"-> TOMAS Response Preview: {reply[:180].replace(chr(10), ' ')}...")

    s3_id = session_manager.save_session(
        messages=s3_messages,
        summary="Session 3: 30-turn complex workflow, tool lifecycles & reflection audit",
        model=model_name,
        token_usage=dict(agent._session_tokens),
        telemetry=agent.session_telemetry()
    )
    s3_audit = session_manager.audit_transcript(s3_messages)
    session_records.append({
        "name": "Session 3",
        "id": s3_id,
        "turns": len(s3_prompts),
        "audit": s3_audit
    })
    print(f"\n✓ Session 3 saved: {s3_id} (Audit Complete = {s3_audit['complete']})")

    # =========================================================================
    # SUMMARY REPORT
    # =========================================================================
    print("\n" + "=" * 75)
    print("ULTRA-LONG 30-PROMPT MULTI-SESSION SIMULATION SUMMARY")
    print("=" * 75)
    for rec in session_records:
        name = rec["name"]
        sid = rec["id"]
        turns = rec["turns"]
        audit_ok = rec["audit"]["complete"]
        print(f"- {name} ({sid}): {turns} turns | Audit Complete: {audit_ok}")
        if "precision" in rec:
            prec = rec["precision"]
            print(f"  * Cross-Session Precision: Date={prec['date_appended']}/28, Honorific={prec['honorific_appended']}/28, Summary={prec['summary_header']}/28")

    return session_records

if __name__ == "__main__":
    run_ultra_simulations()
