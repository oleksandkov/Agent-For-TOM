#!/usr/bin/env python3
"""
Multi-session TOMAS agent simulation runner.
Runs live sessions against the active provider, saves session transcripts,
audits cross-session rule/note persistence, and outputs telemetry data.
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

def run_simulation():
    print("=" * 60)
    print("Starting TOMAS Multi-Session Simulation Harness")
    print("=" * 60)

    # Force active model
    os.environ["AGENT_MODEL"] = "laguna-s-2.1-free"
    os.environ["AGENT_AUTO_APPROVE"] = "1"
    agent.AUTO_APPROVE_LOW = True

    model_name = agent._get_model()
    print(f"Active Model: {model_name}")

    session_ids = []
    audit_results = []

    # -------------------------------------------------------------
    # SESSION 1: Interactive task execution + /note & memory creation
    # -------------------------------------------------------------
    print("\n" + "="*40)
    print("SESSION 1: Initializing & Setting User Note/Rule")
    print("="*40)

    agent.reset_session_state()
    s1_messages = []

    s1_prompts = [
        "Hello TOMAS! Please list the top-level files in this repository.",
        "Read the first 30 lines of AGENTS.md to understand project identity.",
        "/note response_formatting Always end every response with the current date: 2026-08-05",
        "Save a memory with key 'date-rule', description 'Rule to append date', content 'Always append current date 2026-08-05 to responses.'",
        "Can you summarize what you have learned in this session?"
    ]

    for turn_idx, prompt in enumerate(s1_prompts, 1):
        print(f"\n[Session 1 - Turn {turn_idx}] User: {prompt}")

        # Check for slash command
        if prompt.startswith("/"):
            res = agent.handle_slash_command(prompt[1:], s1_messages)
            if res and res != "__continue__":
                print(f"-> Slash output:\n{res[:200]}")
            continue

        s1_messages.append({"role": "user", "content": prompt})
        sys_prompt = agent.build_system_prompt(prompt)
        reply = agent.agent_loop(sys_prompt, s1_messages)
        print(f"-> Assistant: {reply[:250]}...")

    s1_id = session_manager.save_session(
        messages=s1_messages,
        summary="Session 1: Initial conversation and rule/note creation",
        model=model_name,
        token_usage=dict(agent._session_tokens),
        telemetry=agent.session_telemetry()
    )
    session_ids.append(s1_id)
    print(f"\n✓ Session 1 saved: {s1_id}")

    # Audit Session 1 transcript
    audit_s1 = session_manager.audit_transcript(s1_messages)
    audit_results.append(("Session 1", s1_id, audit_s1))
    print(f"✓ Session 1 Audit Complete: {audit_s1['complete']}")

    # -------------------------------------------------------------
    # SESSION 2: Fresh Session - Test Cross-Session Recall
    # -------------------------------------------------------------
    print("\n" + "="*40)
    print("SESSION 2: Fresh Session Context - Testing Cross-Session Recall")
    print("="*40)

    agent.reset_session_state()
    s2_messages = []

    # Verify what build_system_prompt recalls
    test_recall_prompt = "What is the date formatting rule or note saved previously?"
    recalled_prompt = agent.build_system_prompt(test_recall_prompt)
    print("\n--- System Prompt Recalled Knowledge Section ---")
    if "What I've learned" in recalled_prompt:
        for line in recalled_prompt.splitlines():
            if "learned" in line or "date" in line.lower() or "response_formatting" in line.lower() or "2026-08-05" in line:
                print(f"  [Recalled Line] {line}")
    else:
        print("  [Notice] Learning recall section present:", "learned" in recalled_prompt.lower())

    s2_prompts = [
        "Hello! I am starting a brand new session with you. What Python interpreter and project setup do we have?",
        "Do you remember or recall any custom note or formatting rule set in a previous session?",
        "Please list the main python files in the project root."
    ]

    for turn_idx, prompt in enumerate(s2_prompts, 1):
        print(f"\n[Session 2 - Turn {turn_idx}] User: {prompt}")

        if prompt.startswith("/"):
            res = agent.handle_slash_command(prompt[1:], s2_messages)
            if res and res != "__continue__":
                print(f"-> Slash output:\n{res[:200]}")
            continue

        s2_messages.append({"role": "user", "content": prompt})
        sys_prompt = agent.build_system_prompt(prompt)
        reply = agent.agent_loop(sys_prompt, s2_messages)
        print(f"-> Assistant: {reply[:250]}...")

    s2_id = session_manager.save_session(
        messages=s2_messages,
        summary="Session 2: Testing cross-session rule/note recall",
        model=model_name,
        token_usage=dict(agent._session_tokens),
        telemetry=agent.session_telemetry()
    )
    session_ids.append(s2_id)
    print(f"\n✓ Session 2 saved: {s2_id}")

    audit_s2 = session_manager.audit_transcript(s2_messages)
    audit_results.append(("Session 2", s2_id, audit_s2))
    print(f"✓ Session 2 Audit Complete: {audit_s2['complete']}")

    # -------------------------------------------------------------
    # SESSION 3: Complex Multi-Turn Workflow & Verification
    # -------------------------------------------------------------
    print("\n" + "="*40)
    print("SESSION 3: Complex Workflow & Tool Execution")
    print("="*40)

    agent.reset_session_state()
    s3_messages = []

    s3_prompts = [
        "Please search for the pattern 'def load_session' in session_manager.py.",
        "Write a temporary test file named 'sim_output_test.txt' with content 'TOMAS simulation execution ok'.",
        "Read 'sim_output_test.txt' to verify its contents.",
        "Clean up by removing 'sim_output_test.txt' using run_command."
    ]

    for turn_idx, prompt in enumerate(s3_prompts, 1):
        print(f"\n[Session 3 - Turn {turn_idx}] User: {prompt}")

        if prompt.startswith("/"):
            res = agent.handle_slash_command(prompt[1:], s3_messages)
            if res and res != "__continue__":
                print(f"-> Slash output:\n{res[:200]}")
            continue

        s3_messages.append({"role": "user", "content": prompt})
        sys_prompt = agent.build_system_prompt(prompt)
        reply = agent.agent_loop(sys_prompt, s3_messages)
        print(f"-> Assistant: {reply[:250]}...")

    s3_id = session_manager.save_session(
        messages=s3_messages,
        summary="Session 3: Complex tool execution & file verification",
        model=model_name,
        token_usage=dict(agent._session_tokens),
        telemetry=agent.session_telemetry()
    )
    session_ids.append(s3_id)
    print(f"\n✓ Session 3 saved: {s3_id}")

    audit_s3 = session_manager.audit_transcript(s3_messages)
    audit_results.append(("Session 3", s3_id, audit_s3))
    print(f"✓ Session 3 Audit Complete: {audit_s3['complete']}")

    # Final summary report
    print("\n" + "="*60)
    print("SIMULATION RUN COMPLETED SUCCESSFULLY")
    print("="*60)
    for name, sid, audit in audit_results:
        print(f"- {name} ({sid}): Audit Complete = {audit['complete']}, Messages = {len(audit.get('orphaned_user_turns', []))}")

    return {
        "session_ids": session_ids,
        "audit_results": audit_results
    }

if __name__ == "__main__":
    run_simulation()
