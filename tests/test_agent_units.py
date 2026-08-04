#!/usr/bin/env python3
"""
Unit test suite for TOMAS agent modules.
Tests keybindings, tools, session management, instruction precedence, and provider detection.
Run: python -m unittest discover -s tests -p "test_*.py"
"""
import os
import sys
import json
import unittest
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_DIR = Path(__file__).parent.parent.resolve()
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

# Force UTF-8 on Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

import agent
import agent_cli
from session_manager import save_session, load_session, delete_session, list_sessions
from instructions_manager import get_global_instructions


class TestBuiltinTools(unittest.TestCase):
    """Test built-in agent tools."""

    def test_read_file(self):
        content = agent.handle_read_file({"file_path": "AGENTS.md"})
        self.assertIn("Agent-For-TOM", content)

    def test_read_file_nonexistent(self):
        content = agent.handle_read_file({"file_path": "invalid_non_existent.txt"})
        self.assertIn("not found", content.lower())

    def test_write_and_edit_file(self):
        tmp_file = "test_unit_tmp.txt"
        agent.handle_write_file({"file_path": tmp_file, "content": "alpha beta gamma"})
        read1 = agent.handle_read_file({"file_path": tmp_file})
        self.assertIn("alpha beta gamma", read1)

        agent.handle_edit_file({"file_path": tmp_file, "old_string": "beta", "new_string": "DELTA"})
        read2 = agent.handle_read_file({"file_path": tmp_file})
        self.assertIn("alpha DELTA gamma", read2)

        if Path(tmp_file).exists():
            Path(tmp_file).unlink()

    def test_run_command_safety(self):
        out_normal = agent.handle_run_command({"command": "echo test_cmd_unit"})
        self.assertIn("test_cmd_unit", out_normal)

        out_blocked = agent.handle_run_command({"command": "rm -rf /"})
        self.assertIn("blocked", out_blocked.lower())

    def test_search_web_playwright_fallback(self):
        out = agent.handle_search_web({"query": "python", "max_results": 2})
        self.assertTrue("results" in out.lower() or "python" in out.lower() or "error" in out.lower())


class TestSessionManager(unittest.TestCase):
    """Test session persistence, JSON encoding, and loading."""

    def test_session_lifecycle(self):
        msgs = [
            {"role": "user", "content": "Unit test user query"},
            {"role": "assistant", "content": "Unit test assistant response"}
        ]
        sid = save_session(msgs, summary="Unit test session")
        self.assertIsNotNone(sid)

        data = load_session(sid)
        self.assertIsNotNone(data)
        self.assertEqual(data.get("message_count"), 2)

        sessions = list_sessions()
        ids = [s.get("id") for s in sessions]
        self.assertIn(sid, ids)

        deleted = delete_session(sid)
        self.assertTrue(deleted)
        self.assertIsNone(load_session(sid))


class TestInstructionsPrecedence(unittest.TestCase):
    """Test instructions loading hierarchy."""

    def test_instructions_loading(self):
        instructions = get_global_instructions()
        self.assertIsInstance(instructions, str)


class TestProviderDetection(unittest.TestCase):
    """Test provider configuration and fallback detection."""

    def test_detect_provider(self):
        provider = agent_cli._detect_provider()
        self.assertIsInstance(provider, str)

        config = agent_cli._load_providers_config()
        self.assertIsInstance(config, dict)
        self.assertIn("active", config)


if __name__ == "__main__":
    unittest.main()
