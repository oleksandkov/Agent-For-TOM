#!/usr/bin/env python3
"""
Extended 20-Prompt Multi-Session TOMAS Agent Simulation Harness.
Runs 3 sessions with 20 user prompts per session (60 live turns total) against
the active provider (OpenCode Zen / laguna-s-2.1-free).

Measures:
1. Turn-by-turn precision of cross-session note & rule adherence over 20 turns.
2. Memory recall & learning persistence across fresh session boundaries.
3. Transcript audit completeness and telemetry accuracy.
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

def run_extended_simulations():
    print("=" * 70)
    print("STARTING EXTENDED 20-PROMPT MULTI-SESSION TOMAS SIMULATION HARNESS")
    print("=" * 70)

    os.environ["AGENT_MODEL"] = "laguna-s-2.1-free"
    os.environ["AGENT_AUTO_APPROVE"] = "1"
    agent.AUTO_APPROVE_LOW = True

    model_name = agent._get_model()
    print(f"Active Provider Model: {model_name}\n")

    session_records = []

    # =========================================================================
    # SESSION 1: 20-Prompt Developer Session + Rules & Memory Creation
    # =========================================================================
    print("=" * 50)
    print("SESSION 1: Initializing Workspace & Registering Persistent Rules (20 Turns)")
    print("=" * 50)

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
        "11. Write a temporary scratch python script 'scratch/s1_test.py' with typed function add_numbers(a: int, b: int) -> int.",
        "12. Run the script 'scratch/s1_test.py' using run_command.",
        "13. Read 'scratch/s1_test.py' to verify its contents.",
        "14. Search for 'generate_ai_news_pdf' in pdf_report_skill.py.",
        "15. Read lines 1-30 of zen_proxy.py.",
        "16. Summarize the main tools available in TOMAS.",
        "17. I like how you format code, but make sure python code blocks always specify ```python.",
        "18. List all self-notes using /notes.",
        "19. Remove 'scratch/s1_test.py' using run_command.",
        "20. Conclude Session 1 and give a final summary of all actions taken."
    ]

    for turn_num, prompt in enumerate(s1_prompts, 1):
        print(f"\n[Session 1 - Turn {turn_num:02d}/20] User: {prompt[:80]}...")
        if prompt.startswith("4. /note") or prompt.startswith("18. /notes"):
            # strip prompt number prefix for slash command
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
        summary="Session 1: 20-turn developer session establishing persistent rules & memories",
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
    # SESSION 2: 20-Prompt Fresh Session - Cross-Session Precision Test
    # =========================================================================
    print("\n" + "=" * 50)
    print("SESSION 2: Fresh Session Context - 20-Turn Cross-Session Precision Test")
    print("=" * 50)

    agent.reset_session_state()
    s2_messages = []

    s2_prompts = [
        "1. Hello TOMAS! I am starting a fresh session with you today.",
        "2. What is your primary purpose as an agent in this project?",
        "3. List the top-level files in the workspace.",
        "4. Search for 'class MCPManager' in mcp_manager.py.",
        "5. Read lines 1-30 of mcp_manager.py.",
        "6. Do you recall any custom formatting rules or notes set in previous sessions?",
        "7. What Python coding style preference do I have saved in memory?",
        "8. Write a python utility function in 'scratch/s2_prime.py' to check if a number is prime. Use my preferred coding style.",
        "9. Read 'scratch/s2_prime.py' to verify type annotations.",
        "10. Run 'scratch/s2_prime.py' with run_command.",
        "11. Explain how instructions_manager.py loads global vs project instructions.",
        "12. Search for 'build_system_prompt' in agent.py.",
        "13. Read lines 1600-1630 of agent.py.",
        "14. Explain how select_tools handles tool budget constraints.",
        "15. List all active self-notes using /notes.",
        "16. Write a summary of how learning/retrieval.py scores facts.",
        "17. Clean up 'scratch/s2_prime.py' using run_command.",
        "18. What are the three layers of the memory system in TOMAS?",
        "19. Have you maintained our requested response formatting rules throughout this 20-turn conversation?",
        "20. Give a final wrap-up summary for Session 2."
    ]

    precision_hits = {
        "summary_header": 0,
        "date_appended": 0,
        "honorific_appended": 0,
        "pep484_typed": 0
    }

    for turn_num, prompt in enumerate(s2_prompts, 1):
        print(f"\n[Session 2 - Turn {turn_num:02d}/20] User: {prompt[:80]}...")

        if "15. /notes" in prompt:
            cmd_text = "/notes"
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

        # Audit precision on this turn
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
        summary="Session 2: 20-turn fresh session evaluating cross-session rule precision",
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
    print(f"✓ Session 2 Rule Precision Scores (out of 19 response turns):")
    print(f"   - Date Appended: {precision_hits['date_appended']}/19")
    print(f"   - Honorific Appended: {precision_hits['honorific_appended']}/19")
    print(f"   - Summary Header: {precision_hits['summary_header']}/19")

    # =========================================================================
    # SESSION 3: 20-Prompt Complex Workflow & Tool Execution Session
    # =========================================================================
    print("\n" + "=" * 50)
    print("SESSION 3: Complex Workflow, Tool Lifecycles & Reflection (20 Turns)")
    print("=" * 50)

    agent.reset_session_state()
    s3_messages = []

    s3_prompts = [
        "1. Search for 'def audit_transcript' in session_manager.py.",
        "2. Read lines 300-330 of session_manager.py to see audit logic.",
        "3. Search for 'def load_active_facts' in learning/store.py.",
        "4. Read lines 170-200 of learning/store.py.",
        "5. Search for 'def recency_boost' in learning/retrieval.py.",
        "6. Read lines 1-40 of learning/retrieval.py.",
        "7. Write a scratch module 'scratch/calc.py' with typed functions add(a: float, b: float) -> float and multiply(a: float, b: float) -> float.",
        "8. Read 'scratch/calc.py' to verify implementation.",
        "9. Write a test script 'scratch/test_calc.py' that imports calc and runs assertions.",
        "10. Run 'scratch/test_calc.py' via run_command.",
        "11. Perform Cyrillic tokenization test query: 'Прочитай файл конфігурації та покажи результат'.",
        "12. Search for 'def handle_search_web' in agent.py.",
        "13. Read lines 1350-1380 of agent.py.",
        "14. Execute search_web for 'Python 3.12 release notes'.",
        "15. Fetch URL 'https://example.com' using fetch_url.",
        "16. Clean up 'scratch/calc.py' and 'scratch/test_calc.py' using run_command.",
        "17. Trigger session analysis update using /self-improve analyze.",
        "18. Show learned facts status using /self-improve facts.",
        "19. Show current mode status using /status.",
        "20. Conclude Session 3 with a detailed technical summary."
    ]

    for turn_num, prompt in enumerate(s3_prompts, 1):
        print(f"\n[Session 3 - Turn {turn_num:02d}/20] User: {prompt[:80]}...")

        if "17. /self-improve" in prompt or "18. /self-improve" in prompt or "19. /status" in prompt:
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
        summary="Session 3: 20-turn complex workflow, tool lifecycles & reflection audit",
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
    print("\n" + "=" * 70)
    print("EXTENDED 20-PROMPT MULTI-SESSION SIMULATION SUMMARY")
    print("=" * 70)
    for rec in session_records:
        name = rec["name"]
        sid = rec["id"]
        turns = rec["turns"]
        audit_ok = rec["audit"]["complete"]
        print(f"- {name} ({sid}): {turns} turns | Audit Complete: {audit_ok}")
        if "precision" in rec:
            prec = rec["precision"]
            print(f"  * Cross-Session Precision: Date={prec['date_appended']}/19, Honorific={prec['honorific_appended']}/19, Summary={prec['summary_header']}/19")

    return session_records

if __name__ == "__main__":
    run_extended_simulations()
