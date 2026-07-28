#!/usr/bin/env python3
"""
Build Your Own Claude Code — a minimal AI coding agent.

Based on the architecture popularized by the "Build Your Own Claude Code" video
(Devtools Tech): a while-loop that calls an LLM, dispatches tool calls, and
feeds results back until the model returns plain text.

Features:
  - Agent loop with tool use
  - Tools: read_file, write_file, edit_file, list_files, run_command, search_code
  - Project context re-injection (AGENTS.md / AGENT.md)
  - Three-layer memory system (~/.tomas/memory/)
  - Risk-tiered permission system (low / medium / high)
  - Auto-compaction when the context window fills up

Env vars:
  ANTHROPIC_API_KEY   - required API key
  ANTHROPIC_BASE_URL  - optional, point to any Anthropic-compatible endpoint
  AGENT_MODEL         - optional, model name (default: claude-sonnet-4-5)
  AGENT_AUTO_APPROVE  - optional, "1" to auto-approve low-risk tools
"""

from __future__ import annotations

import os
import re
import sys
import json
import subprocess
import urllib.request
import urllib.error
from pathlib import Path
from typing import Callable, Optional

# Try to import Playwright
try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    async_playwright = None

# Load variables from .env into os.environ if present.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # python-dotenv not installed — rely on real env vars instead.
    pass

# Playwright for browser-based fetching (JavaScript rendering)
try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

import anthropic

# MCP and skills support
from mcp_manager import MCPManager
from skills_manager import build_skills_section, discover_skills, cmd_skill_list, cmd_skill_run

# Self-improving system
import self_improve

# Self-notes system
import self_notes

# Session management
from session_manager import (
    save_session, list_sessions, load_session,
    continue_session, delete_session, get_latest_session,
)

# Instructions management
from instructions_manager import (
    build_instructions_section, get_global_instructions,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MODEL = os.environ.get("AGENT_MODEL")
PROJECT_DIR = Path(os.environ.get("AGENT_PROJECT_DIR", os.getcwd())).resolve()
MEMORY_DIR = Path.home() / ".tomas" / "memory"
MAX_TOKENS = 8192
COMPACTION_THRESHOLD = 0.75  # compact when total budget (msg_tok + TOOL_TOKENS + MAX_TOKENS) exceeds this fraction of CONTEXT_WINDOW
DEFAULT_CONTEXT_WINDOW = 128_000  # fallback if API doesn't report context window
CONTEXT_WINDOW = DEFAULT_CONTEXT_WINDOW  # will be updated dynamically at startup

# Known model context windows (fallback when API is not reachable)
MODEL_CONTEXT_MAP: dict[str, int] = {
    # Zen models
    "deepseek-v4-flash-free": 1_000_000,
    "big-pickle": 128_000,
    # Anthropic
    "claude-sonnet-4-5": 200_000,
    "claude-sonnet-4": 200_000,
    "claude-3-5-sonnet-20241022": 200_000,
    "claude-3-haiku-20240307": 200_000,
    "claude-opus-4-5": 200_000,
    "claude-opus-4": 200_000,
    # OpenRouter / common
    "gpt-4o-mini": 128_000,
    "gpt-4o": 128_000,
    "gpt-4": 128_000,
    "gpt-3.5-turbo": 16_385,
    # DeepSeek
    "deepseek-chat": 128_000,
    "deepseek-reasoner": 128_000,
}

# Will be updated at startup if we can fetch from API
_current_context_window: int = DEFAULT_CONTEXT_WINDOW
AUTO_APPROVE_LOW = os.environ.get("AGENT_AUTO_APPROVE", "1") == "1"
YOLO_MODE = False  # when True, all tools are auto-approved without any prompt

# ── Session token tracking ──
_session_tokens = {"input": 0, "output": 0, "calls": 0}
_last_turn_usage = {"input": 0, "output": 0}

# ── Session continuation ──
# Set by agent_cli.py before calling main() to continue a previous session.
CONTINUE_SESSION_ID: Optional[str] = None

# ── Client factory: supports ANTHROPIC_EXTRA_HEADERS env var (JSON) ──
_client_instance = None

def _get_client():
    """Return a cached Anthropic client, re-initialised if provider changed."""
    global _client_instance
    # Read current env values each time — update_dotenv in the CLI sets os.environ
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    base = os.environ.get("ANTHROPIC_BASE_URL", "")
    extra_hdr = os.environ.get("ANTHROPIC_EXTRA_HEADERS", "")
    # Build a simple cache key so we know when env changed
    cache_key = f"{key}::{base}::{extra_hdr}"
    if _client_instance is not None:
        prev_key = getattr(_client_instance, "_cache_key", None)
        if prev_key == cache_key:
            return _client_instance
    headers = json.loads(extra_hdr) if extra_hdr else None
    _client_instance = anthropic.Anthropic(
        api_key=key or None,
        base_url=base or None,
        default_headers=headers,
    )
    _client_instance._cache_key = cache_key  # type: ignore[attr-defined]
    return _client_instance

def reinit_client():
    """Force the client to be re-created on next use (called after provider change)."""
    global _client_instance
    _client_instance = None


def _ensure_zen_proxy():
    """Auto-start the Zen proxy daemon if the agent points at it."""
    base_url = os.environ.get("ANTHROPIC_BASE_URL", "")
    if "127.0.0.1:6446" in base_url or "localhost:6446" in base_url:
        try:
            from zen_proxy import check_status, start_proxy
            if not check_status(6446):
                print(f'  {DIM}Starting Zen proxy on port 6446...{RESET}')
                start_proxy(6446, daemon=True)
                import time
                time.sleep(0.5)
                if check_status(6446):
                    print(f'  {GREEN}✓{RESET}  Zen proxy is running')
                else:
                    print(f'  {YELLOW}⚠{RESET}  Zen proxy may not have started')
            # else: proxy already running, nothing to do
        except ImportError:
            print(f'  {RED}✗{RESET}  zen_proxy module not found — cannot start proxy')
        except Exception as exc:
            print(f'  {RED}✗{RESET}  Failed to start Zen proxy: {exc}')


def _fetch_model_context_window() -> int:
    """Query the API's /v1/models endpoint to get the real context window for the current model.
    Falls back to MODEL_CONTEXT_MAP, then to DEFAULT_CONTEXT_WINDOW if the API is unreachable."""
    model = _get_model()
    base_url = os.environ.get("ANTHROPIC_BASE_URL", "")
    if not base_url:
        # No API base URL — use fallback map
        return MODEL_CONTEXT_MAP.get(model, DEFAULT_CONTEXT_WINDOW)
    try:
        models_url = base_url.rstrip("/") + "/v1/models"
        req = urllib.request.Request(models_url, method="GET")
        # Use the same API key if set
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if key:
            req.add_header("x-api-key", key)
            req.add_header("anthropic-version", "2023-06-01")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            # OpenAI-style: data.data[].context_window
            # Anthropic-style: data.data[].context_window
            models_list = data if isinstance(data, list) else data.get("data", [])
            for m in models_list:
                if m.get("id") == model:
                    cw = m.get("context_window") or m.get("context") or 0
                    if cw:
                        return int(cw)
            # Check for a top-level context_window field (dict response)
            if isinstance(data, dict):
                cw = data.get("context_window") or data.get("context") or 0
                if cw:
                    return int(cw)
    except Exception:
        pass
    # Fallback to hardcoded map
    return MODEL_CONTEXT_MAP.get(model, DEFAULT_CONTEXT_WINDOW)


# MCP manager (initialized at startup when main() is called)
mcp_manager: Optional[MCPManager] = None
COMBINED_TOOLS: list[dict] = []
TOOL_TOKENS: int = 0  # estimated token count for tool definitions
MCP_TOOL_NAME_MAP: dict[str, str] = {}  # renamed_name -> original_name for conflicting MCP tools

# ── ANSI color constants for the chat UI ──
RESET = '\033[0m'
BOLD = '\033[1m'
DIM = '\033[2m'
GREEN = '\033[92m'
CYAN = '\033[96m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
MAGENTA = '\033[95m'
RED = '\033[91m'
GRAY = '\033[90m'
BOLD_OFF = '\033[22m'

# ---------------------------------------------------------------------------
# Tool definitions (schema sent to the model)
# ---------------------------------------------------------------------------

TOOLS: list[dict] = [
    {
        "name": "read_file",
        "description": "Read a file from the filesystem. Returns contents with line numbers.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Absolute or project-relative path"},
                "offset": {"type": "integer", "description": "Line number to start reading from (1-indexed)"},
                "limit": {"type": "integer", "description": "Max lines to read. Defaults to 2000."},
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write content to a file. Creates the file (and parent dirs) if needed.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Absolute or project-relative path"},
                "content": {"type": "string", "description": "Full file content to write"},
            },
            "required": ["file_path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": "Replace a unique string in a file. old_string must appear exactly once.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Absolute or project-relative path"},
                "old_string": {"type": "string", "description": "Exact text to find"},
                "new_string": {"type": "string", "description": "Replacement text"},
            },
            "required": ["file_path", "old_string", "new_string"],
        },
    },
    {
        "name": "list_files",
        "description": "List files in a directory (non-recursive).",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory to list. Defaults to project root."},
            },
            "required": [],
        },
    },
    {
        "name": "run_command",
        "description": "Execute a shell command and return stdout/stderr.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to run"},
                "timeout": {"type": "integer", "description": "Timeout in seconds. Default 120."},
            },
            "required": ["command"],
        },
    },
    {
        "name": "search_code",
        "description": "Search for a regex pattern across files in a directory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex pattern"},
                "path": {"type": "string", "description": "Directory to search. Defaults to project root."},
                "file_glob": {"type": "string", "description": "File pattern filter, e.g. '*.py'"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "save_memory",
        "description": "Persist a note to the agent's memory for future sessions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Short identifier (kebab-case)"},
                "description": {"type": "string", "description": "One-line summary for the index"},
                "content": {"type": "string", "description": "Full memory content"},
            },
            "required": ["key", "description", "content"],
        },
    },
    {
        "name": "fetch_url",
        "description": "Fetch content from a URL (HTTP/HTTPS). Returns the response body as text.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to fetch"},
                "timeout": {"type": "integer", "description": "Timeout in seconds. Default 30."},
                "max_size": {"type": "integer", "description": "Max response size in bytes. Default 50000000."},
            },
            "required": ["url"],
        },
    },
    {
        "name": "fetch_url_with_browser",
        "description": "Fetch content from a URL using a headless browser (Playwright). Supports JavaScript rendering, SPAs, and dynamic content. Returns the page content as text.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to fetch"},
                "wait_for": {"type": "string", "description": "Selector to wait for before extracting content (optional)"},
                "timeout": {"type": "integer", "description": "Timeout in seconds. Default 30."},
                "max_size": {"type": "integer", "description": "Max response size in bytes. Default 50000000."},
                "screenshot": {"type": "boolean", "description": "Whether to take a screenshot. Default false."},
            },
            "required": ["url"],
        },
    },
    {
        "name": "search_web",
        "description": "Search the internet for current information on any topic. Returns a list of search results with titles, snippets, and URLs. Use this when you need up-to-date information from the web (news, weather, facts, etc.).",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query (e.g. 'weather in Vinnytsia Ukraine' or 'latest AI news 2026')"},
                "max_results": {"type": "integer", "description": "Max results to return. Default 5."},
            },
            "required": ["query"],
        },
    },
]

# ---------------------------------------------------------------------------
# Risk tiers for the permission system
# ---------------------------------------------------------------------------

RISK_LEVELS: dict[str, str] = {
    "read_file": "low",
    "list_files": "low",
    "search_code": "low",
    "edit_file": "medium",
    "write_file": "medium",
    "save_memory": "low",
    "run_command": "high",
    "fetch_url": "low",
    "fetch_url_with_browser": "medium",
    "search_web": "low",
}

# Patterns that are always blocked from run_command
BLOCKED_PATTERNS = ["rm -rf /", "mkfs", "> /dev/sd", "dd if=/dev/zero", ":(){:|:&};:"]

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _resolve(path: str) -> Path:
    p = Path(path)
    if not p.is_absolute():
        p = PROJECT_DIR / p
    return p.resolve()

def _safe(p: Path) -> bool:
    """Ensure the path stays inside the project directory."""
    try:
        p.relative_to(PROJECT_DIR)
        return True
    except ValueError:
        return False

# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

def handle_read_file(params: dict) -> str:
    path = _resolve(params["file_path"])
    if not _safe(path):
        return f"Error: path outside project: {path}"
    if not path.exists():
        return f"Error: file not found: {path}"
    offset = max(0, int(params.get("offset", 1)) - 1)
    limit = int(params.get("limit", 2000))
    with path.open("r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    selected = lines[offset:offset + limit]
    numbered = [f"{i + offset + 1:6}\t{line}" for i, line in enumerate(selected)]
    return "".join(numbered) if numbered else "(empty file)"

def handle_write_file(params: dict) -> str:
    path = _resolve(params["file_path"])
    if not _safe(path):
        return f"Error: path outside project: {path}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(params["content"], encoding="utf-8")
    return f"Successfully wrote {len(params['content'])} chars to {path}"

def handle_edit_file(params: dict) -> str:
    path = _resolve(params["file_path"])
    if not _safe(path):
        return f"Error: path outside project: {path}"
    if not path.exists():
        return f"Error: file not found: {path}"
    content = path.read_text(encoding="utf-8")
    old = params["old_string"]
    count = content.count(old)
    if count == 0:
        return f"Error: old_string not found in {path}"
    if count > 1:
        return f"Error: old_string matches {count} locations; be more specific."
    new_content = content.replace(old, params["new_string"], 1)

    # ── Generate a unified diff preview ──
    import difflib
    old_lines = content.splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)
    diff = difflib.unified_diff(
        old_lines, new_lines,
        fromfile=str(path),
        tofile=str(path),
        n=3,  # context lines
    )
    diff_text = "".join(diff)
    # Color the diff for terminal display
    colored_lines = []
    for line in diff_text.splitlines():
        if line.startswith('+') and not line.startswith('+++'):
            colored_lines.append(f'{GREEN}{line}{RESET}')
        elif line.startswith('-') and not line.startswith('---'):
            colored_lines.append(f'{RED}{line}{RESET}')
        elif line.startswith('@@'):
            colored_lines.append(f'{CYAN}{line}{RESET}')
        else:
            colored_lines.append(f'{DIM}{line}{RESET}')
    colored_diff = "\n".join(colored_lines)

    # Write the new content
    path.write_text(new_content, encoding="utf-8")

    # Return a summary with the diff
    n_add = sum(1 for l in diff_text.splitlines() if l.startswith('+') and not l.startswith('+++'))
    n_del = sum(1 for l in diff_text.splitlines() if l.startswith('-') and not l.startswith('---'))
    return f"Successfully edited {path} (+{n_add} -{n_del} lines)\n\n{colored_diff}"

def handle_list_files(params: dict) -> str:
    path = _resolve(params.get("path", "."))
    if not _safe(path):
        return f"Error: path outside project: {path}"
    if not path.exists():
        return f"Error: directory not found: {path}"
    entries = []
    for child in sorted(path.iterdir()):
        # skip noise
        if child.name in {".git", "__pycache__", ".agent"}:
            continue
        entries.append(f"{'[dir] ' if child.is_dir() else '      '}{child.name}")
    return "\n".join(entries) if entries else "(empty)"

def handle_run_command(params: dict) -> str:
    cmd = params["command"]
    for bad in BLOCKED_PATTERNS:
        if bad in cmd:
            return f"Error: blocked dangerous pattern: {bad}"
    timeout = int(params.get("timeout", 120))
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            timeout=timeout, cwd=str(PROJECT_DIR),
        )
    except subprocess.TimeoutExpired:
        return f"Error: command timed out after {timeout}s"
    output = ""
    if result.stdout:
        output += result.stdout
    if result.stderr:
        output += f"\nSTDERR:\n{result.stderr}"
    if not output.strip():
        output = f"Command completed with exit code {result.returncode}"
    if len(output) > 30000:
        output = output[:15000] + "\n\n... [truncated] ...\n\n" + output[-15000:]
    return output

def handle_search_code(params: dict) -> str:
    pattern = params["pattern"]
    path = _resolve(params.get("path", "."))
    if not _safe(path):
        return f"Error: path outside project: {path}"
    file_glob = params.get("file_glob", "")
    matches: list[str] = []
    glob_iter = path.rglob(file_glob) if file_glob else path.rglob("*")
    for file in glob_iter:
        if not file.is_file():
            continue
        try:
            text = file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if re.search(pattern, line):
                matches.append(f"{file}:{i}: {line}")
                if len(matches) >= 50:
                    matches.append("... (50 matches, truncated)")
                    return "\n".join(matches)
    return "\n".join(matches) if matches else f"No matches for pattern: {pattern}"

def handle_save_memory(params: dict) -> str:
    save_memory(params["key"], params["description"], params["content"])
    return f"Saved memory '{params['key']}'"


def handle_fetch_url(params: dict) -> str:
    """Fetch content from a URL."""
    import urllib.request
    import urllib.error

    url = params["url"]
    timeout = int(params.get("timeout", 15))
    max_size = int(params.get("max_size", 500_000))  # 500KB max for safety

    # Basic URL validation
    if not url.startswith(("http://", "https://")):
        return f"Error: URL must start with http:// or https://"

    # Block dangerous URLs
    blocked_patterns = ["localhost", "127.0.0.1", "0.0.0.0", "::1", "169.254.169.254"]
    for pattern in blocked_patterns:
        if pattern in url:
            return f"Error: blocked URL pattern: {pattern}"

    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Agent-for-TOM/1.0 (fetch_url tool)"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as response:
            # Check content length if available
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > max_size:
                return f"Error: response too large ({content_length} bytes, max {max_size})"

            # Read with size limit
            data = response.read(max_size + 1)
            if len(data) > max_size:
                return f"Error: response exceeds max size ({max_size} bytes)"

            # Decode
            content = data.decode("utf-8", errors="replace")
            return content
    except urllib.error.HTTPError as e:
        return f"Error: HTTP {e.code} {e.reason}"
    except urllib.error.URLError as e:
        return f"Error: {e.reason}"
    except Exception as e:
        return f"Error: {e}"


def handle_fetch_url_with_browser(params: dict) -> str:
    """Fetch content from a URL using a headless browser (Playwright)."""
    if not PLAYWRIGHT_AVAILABLE:
        return "Error: Playwright not available. Install with: pip install playwright && playwright install chromium"

    import asyncio

    url = params["url"]
    wait_for = params.get("wait_for")
    timeout = int(params.get("timeout", 30)) * 1000  # Convert to milliseconds
    max_size = int(params.get("max_size", 50000000))
    take_screenshot = params.get("screenshot", False)

    # Basic URL validation
    if not url.startswith(("http://", "https://")):
        return f"Error: URL must start with http:// or https://"

    # Block dangerous URLs
    blocked_patterns = ["localhost", "127.0.0.1", "0.0.0.0", "::1", "169.254.169.254"]
    for pattern in blocked_patterns:
        if pattern in url:
            return f"Error: blocked URL pattern: {pattern}"

    async def _fetch():
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                context = await browser.new_context(
                    user_agent="Agent-for-TOM/1.0 (fetch_url_with_browser tool)"
                )
                page = await context.new_page()
                
                # Set timeout
                page.set_default_timeout(timeout)
                
                # Navigate to the page
                response = await page.goto(url, wait_until="domcontentloaded")
                
                if response and response.status >= 400:
                    return f"Error: HTTP {response.status} {response.status_text}"
                
                # Wait for specific selector if provided
                if wait_for:
                    try:
                        await page.wait_for_selector(wait_for, timeout=timeout)
                    except Exception:
                        return f"Error: timeout waiting for selector '{wait_for}'"
                
                # Get page content
                content = await page.content()
                
                # Optionally take screenshot
                screenshot_data = None
                if take_screenshot:
                    screenshot_bytes = await page.screenshot(full_page=True)
                    import base64
                    screenshot_data = base64.b64encode(screenshot_bytes).decode('utf-8')
                
                # Close browser
                await browser.close()
                
                # Check size
                if len(content) > max_size:
                    content = content[:max_size] + f"\n\n... [truncated, max {max_size} bytes]"
                
                result = content
                if screenshot_data:
                    result += f"\n\n[Screenshot captured: {len(screenshot_data)} bytes base64]"
                
                return result
            except Exception as e:
                await browser.close()
                raise e

    try:
        return asyncio.run(_fetch())
    except Exception as e:
        return f"Error: {e}"


def handle_search_web(params: dict) -> str:
    """Search the internet using DuckDuckGo (free, no API key needed)."""
    query = params["query"]
    max_results = int(params.get("max_results", 5))

    try:
        from ddgs import DDGS

        results = []
        with DDGS() as ddgs:
            for i, r in enumerate(ddgs.text(query, max_results=max_results)):
                title = r.get("title", "?")
                body = r.get("body", "?")
                href = r.get("href", "?")
                results.append(f"{i+1}. {title}\n   {body}\n   URL: {href}")

        if not results:
            return f"No results found for '{query}'"

        return f"Search results for '{query}':\n\n" + "\n\n".join(results)

    except ImportError:
        return "Error: duckduckgo_search not installed. Run: pip install duckduckgo_search"
    except Exception as e:
        return f"Error searching the web: {e}"


HANDLERS: dict[str, Callable[[dict], str]] = {
    "read_file": handle_read_file,
    "write_file": handle_write_file,
    "edit_file": handle_edit_file,
    "list_files": handle_list_files,
    "run_command": handle_run_command,
    "search_code": handle_search_code,
    "save_memory": handle_save_memory,
    "fetch_url": handle_fetch_url,
    "fetch_url_with_browser": handle_fetch_url_with_browser,
    "search_web": handle_search_web,
}

def execute_tool(name: str, params: dict) -> str:
    handler = HANDLERS.get(name)
    if handler:
        try:
            return handler(params)
        except Exception as e:
            return f"Error: {e}"
    # Try MCP tool dispatch (with name mapping for renamed conflicting tools)
    if mcp_manager:
        mcp_name = MCP_TOOL_NAME_MAP.get(name, name)  # resolve renamed name -> original
        return mcp_manager.call_tool(mcp_name, params)
    return f"Error: unknown tool '{name}'"

# ---------------------------------------------------------------------------
# Permission system
# ---------------------------------------------------------------------------

def check_permission(name: str, params: dict) -> bool:
    global YOLO_MODE
    if YOLO_MODE:
        return True  # YOLO mode approves everything
    risk = RISK_LEVELS.get(name, "high")
    if risk == "low" and AUTO_APPROVE_LOW:
        return True
    risk_colors = {"low": GREEN, "medium": YELLOW, "high": RED}
    risk_color = risk_colors.get(risk, RED)
    print(f'\n  {risk_color}{BOLD}⚠ Permission ({risk.upper()} risk){RESET}')
    print(f'  {DIM}Tool:{RESET} {BOLD}{name}{RESET}')
    for k, v in params.items():
        display = str(v)[:200]
        if len(str(v)) > 200:
            display += "..."
        print(f'  {DIM}{k}:{RESET} {display}')
    try:
        resp = input(f'  {YELLOW}Allow?{RESET} [y/N/always]: ').strip().lower()
    except EOFError:
        return False
    if resp == "always":
        RISK_LEVELS[name] = "low"
        return True
    return resp == "y"

# ---------------------------------------------------------------------------
# System prompt + project context re-injection
# ---------------------------------------------------------------------------

BASE_PROMPT = """You are a coding assistant that helps with software engineering tasks.
You have tools for reading, writing, and editing files, listing directories,
running shell commands, searching code, and saving memories.

Rules:
- Always read a file before editing it.
- Prefer edit_file over write_file for existing files.
- Keep responses concise. Focus on code, not lengthy explanations.
- Use absolute or project-relative paths.
- If a task is done, stop calling tools and summarize."""

def _truncate_section(text: str, max_chars: int, label: str = "") -> str:
    """Truncate a section to max_chars, adding a notice if cut."""
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    # Try to cut at a line boundary for readability
    last_nl = cut.rfind('\n')
    if last_nl > max_chars * 0.8:
        cut = cut[:last_nl]
    notice = f"\n[... truncated: {len(text)} → {len(cut)} chars ...]"
    if label:
        notice = f"\n[... {label} truncated: {len(text)} → {len(cut)} chars ...]"
    return cut + notice


# Maximum size for each system-prompt section (in characters)
MAX_INSTRUCTIONS_CHARS = 8000       # AGENTS.md + global instructions
MAX_MEMORY_CHARS = 2000             # memory index
MAX_SKILLS_CHARS = 4000             # skills section
MAX_SELF_IMPROVE_CHARS = 1500       # self-improvement context
MAX_TOTAL_SYSTEM_PROMPT = 20000     # hard cap on the entire system prompt


def build_system_prompt() -> str:
    prompt = BASE_PROMPT
    # project-level instructions from AGENTS.md / agent.md + .tomas/instructions/
    instructions_section = build_instructions_section(PROJECT_DIR)
    if instructions_section:
        instructions_section = _truncate_section(
            instructions_section, MAX_INSTRUCTIONS_CHARS, "instructions"
        )
        prompt += f"\n\n{instructions_section}"
    # legacy support: AGENT_INSTRUCTIONS.md or BEHAVIOR.md (loaded after for compatibility)
    for candidate in [PROJECT_DIR / "AGENT_INSTRUCTIONS.md", PROJECT_DIR / "BEHAVIOR.md"]:
        if candidate.exists():
            legacy = candidate.read_text(encoding="utf-8")
            legacy = _truncate_section(legacy, 2000, candidate.name)
            prompt += f"\n\n# Agent Instructions ({candidate.name})\n{legacy}"
            break
    # memory index
    memory = load_memory_index()
    if memory:
        memory = _truncate_section(memory, MAX_MEMORY_CHARS, "memory")
        prompt += f"\n\n# Memory index\n{memory}"
    # installed skills
    skills_section = build_skills_section()
    if skills_section:
        skills_section = _truncate_section(
            skills_section, MAX_SKILLS_CHARS, "skills"
        )
        prompt += f"\n\n{skills_section}"
    # ── Self-improvement context ──
    try:
        si_parts: list[str] = []
        si_session = self_improve.get_session_analysis()
        if si_session and si_session.get("purpose") != "unknown":
            purpose = si_session.get("purpose", "unknown")
            stage = si_session.get("stage", "unknown")
            keywords = si_session.get("keywords", [])
            si_parts.append(
                f"# Session Context (self-improving system)\n"
                f"Current session purpose: {purpose}\n"
                f"Current task stage: {stage}\n"
                f"Recent topics: {', '.join(keywords[:8])}\n"
                f"Complexity: {si_session.get('complexity', 'unknown')}\n"
            )
        # Active self-improvement tips
        active_tips = self_improve.get_active_tips()
        if active_tips:
            tips_lines = ["# Self-Improvement Tips for This Session"]
            for i, tip in enumerate(active_tips[:5], 1):
                tips_lines.append(f"{i}. {tip.get('message', '')}")
            si_parts.append("\n".join(tips_lines))
        if si_parts:
            si_text = "\n\n".join(si_parts)
            si_text = _truncate_section(
                si_text, MAX_SELF_IMPROVE_CHARS, "self-improvement"
            )
            prompt += f"\n\n{si_text}"
    except Exception:
        pass
    # ── Hard cap on the total system prompt ──
    if len(prompt) > MAX_TOTAL_SYSTEM_PROMPT:
        prompt = _truncate_section(
            prompt, MAX_TOTAL_SYSTEM_PROMPT, "system prompt"
        )
    return prompt

# ---------------------------------------------------------------------------
# Three-layer memory system
# ---------------------------------------------------------------------------

def load_memory_index() -> str:
    idx = MEMORY_DIR / "MEMORY.md"
    if idx.exists():
        return idx.read_text(encoding="utf-8")
    return ""

def save_memory(key: str, description: str, content: str) -> None:
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{key.replace(' ', '-').lower()}.md"
    filepath = MEMORY_DIR / filename
    filepath.write_text(
        f"---\nname: {key}\ndescription: {description}\n---\n\n{content}",
        encoding="utf-8",
    )
    idx = MEMORY_DIR / "MEMORY.md"
    lines = idx.read_text(encoding="utf-8").splitlines() if idx.exists() else []
    new_entry = f"- [{key}]({filename}) - {description}"
    updated = False
    for i, line in enumerate(lines):
        if filename in line:
            lines[i] = new_entry
            updated = True
            break
    if not updated:
        lines.append(new_entry)
    idx.write_text("\n".join(lines) + "\n", encoding="utf-8")

# ---------------------------------------------------------------------------
# Context management — auto-compaction
# ---------------------------------------------------------------------------

def _estimate_tokens(messages: list) -> int:
    """Rough token estimate. For HTML-heavy tool results use chars//3."""
    total_chars = 0
    for m in messages:
        content = m.get("content", "")
        if isinstance(content, list):
            # tool_results: each item has a 'content' field
            for item in content:
                if isinstance(item, dict) and "content" in item:
                    total_chars += len(str(item["content"]))
                else:
                    total_chars += len(str(item))
        else:
            total_chars += len(str(content))
    return total_chars // 3

def _estimate_system_prompt_tokens(system_prompt: str) -> int:
    """Estimate tokens for the system prompt string."""
    return len(system_prompt) // 3

def maybe_compact(messages: list, system_prompt: str = "") -> list:
    """Compact the conversation if it's getting too large.

    Now accounts for system_prompt + tool definitions + max_tokens in the budget.
    """
    msg_tok = _estimate_tokens(messages)
    sys_tok = _estimate_system_prompt_tokens(system_prompt) if system_prompt else 0
    # Total budget includes system prompt + tool definitions + max_tokens + messages
    total_budget = CONTEXT_WINDOW * COMPACTION_THRESHOLD
    if msg_tok + sys_tok + TOOL_TOKENS + MAX_TOKENS < total_budget:
        return messages
    print(f'  {DIM}[context] compacting conversation...{RESET}')
    try:
        resp = _get_client().messages.create(
            model=_get_model(),
            max_tokens=4096,
            system=("Summarize this conversation. Keep all file paths, decisions made, "
                    "errors encountered, and current task state. Be specific about what "
                    "was changed and why."),
            messages=messages,
        )
        summary = "".join(b.text for b in resp.content if hasattr(b, "text"))
        print(f'  {GREEN}✓{RESET} {DIM}compacted to summary ({len(summary)} chars){RESET}')
        compacted = [
            {"role": "user", "content": f"[Conversation summary]\n{summary}"},
            {"role": "assistant", "content": "I have the context from our previous conversation. What should I work on next?"},
        ]
        compacted.extend(messages[-4:])
        return compacted
    except Exception as e:
        print(f'  {RED}⚠{RESET} {DIM}compaction failed: {e}{RESET}')
        # Fallback: leave room for tools + max_tokens
        budget = int((CONTEXT_WINDOW - TOOL_TOKENS - MAX_TOKENS) * 0.5)
        keep = [messages[-1]] if messages else []
        running = _estimate_tokens(keep)
        for m in reversed(messages[:-1]):
            if running + _estimate_tokens([m]) > budget:
                break
            keep.insert(0, m)
            running = _estimate_tokens(keep)
        print(f'  {YELLOW}⚠{RESET} {DIM}truncated to {len(keep)} messages ({running} est. tokens){RESET}')
        return keep

# ---------------------------------------------------------------------------
# The agent loop
# ---------------------------------------------------------------------------

MAX_TOOL_CALLS_PER_TURN = 25  # safety limit to prevent infinite tool loops
_streaming_disabled = False  # set True if provider doesn't support streaming


def _is_retryable_error(err: Exception) -> bool:
    """Return True if the error is transient and worth retrying (429, 5xx, timeout)."""
    err_msg = str(err)
    # Rate limits and server errors are retryable
    if any(k in err_msg for k in ("429", "rate_limit", "Too Many Requests",
                                    "502", "503", "504", "Bad Gateway",
                                    "Service Unavailable", "Gateway Timeout",
                                    "timeout", "timed out")):
        return True
    # Check for status code attribute
    status = getattr(err, 'status_code', None)
    if status and status >= 500:
        return True
    return False


def _is_client_error(err: Exception) -> bool:
    """Return True if the error is a client-side error (400, 401, 403, 422) that should NOT be retried."""
    err_msg = str(err)
    if any(k in err_msg for k in ("400", "Bad Request", "invalid_request",
                                    "401", "Unauthorized", "authentication",
                                    "403", "Forbidden", "permission",
                                    "422", "Unprocessable")):
        return True
    status = getattr(err, 'status_code', None)
    if status and 400 <= status < 500 and status != 429:
        return True
    return False


def agent_loop(system_prompt: str, messages: list) -> str:
    """Keep calling the model until it stops requesting tools.

    Includes:
    - Streaming output (tokens printed as they arrive)
    - Tool-call loop limit (MAX_TOOL_CALLS_PER_TURN) to prevent infinite loops
    - Smart retry: retries 429/5xx with exponential backoff, never retries 400/401
    """
    import time
    import anthropic

    max_retries = 3
    tool_call_count = 0
    global _streaming_disabled

    while True:
        combined_tools = COMBINED_TOOLS

        # ── API call with smart retry ──
        response = None
        for attempt in range(max_retries + 1):
            try:
                # Try streaming for real-time output (only if not previously disabled)
                if not _streaming_disabled:
                    try:
                        stream_result = _agent_loop_streamed(
                            system_prompt, messages, combined_tools, tool_call_count
                        )
                        if stream_result == "__TOOLS__":
                            # Model requested tools during streaming — fall back to
                            # non-streaming to get the complete tool_use blocks
                            pass
                        else:
                            return stream_result
                    except (AttributeError, TypeError):
                        # Streaming not supported by this provider — disable for rest of session
                        _streaming_disabled = True
                    except anthropic.InternalServerError:
                        raise  # let the retry logic handle it

                response = _get_client().messages.create(
                    model=_get_model(),
                    max_tokens=MAX_TOKENS,
                    system=system_prompt,
                    tools=combined_tools,
                    messages=messages,
                )
                break  # success
            except anthropic.InternalServerError as e:
                if _is_client_error(e) and not _is_retryable_error(e):
                    # 400/401/403 — don't retry, it's a client error
                    print(f"\n  {RED}✗{RESET} API client error (not retrying): {e}")
                    return f"I'm sorry, but the AI service rejected the request. The system prompt or message may be too large. Try /compact to reduce context size."
                if not _is_retryable_error(e):
                    # Other non-retryable error
                    print(f"\n  {RED}✗{RESET} API error: {e}")
                    return "I'm sorry, but there was an error communicating with the AI service."
                if attempt < max_retries:
                    delay = 5 * (2 ** attempt)  # 5, 10, 20 seconds
                    print(f"\n  {YELLOW}⚠{RESET} Transient error — retrying in {delay}s (attempt {attempt + 1}/{max_retries})...")
                    time.sleep(delay)
                else:
                    print(f"  {RED}✗{RESET} Still failing after {max_retries} retries.")
                    return "I'm sorry, but the AI service is unavailable right now. Please try again in a few minutes."
            except Exception as e:
                if attempt < max_retries and _is_retryable_error(e):
                    delay = 5 * (2 ** attempt)
                    print(f"\n  {YELLOW}⚠{RESET} Retrying in {delay}s (attempt {attempt + 1}/{max_retries})...")
                    time.sleep(delay)
                else:
                    print(f"\n  {RED}✗{RESET} Unexpected error: {e}")
                    return "I'm sorry, but an unexpected error occurred."

        if response is None:
            return "I'm sorry, but the AI service could not be reached."

        # ── Track token usage ──
        global _last_turn_usage, _session_tokens
        if hasattr(response, "usage") and response.usage:
            _last_turn_usage["input"] = response.usage.input_tokens or 0
            _last_turn_usage["output"] = response.usage.output_tokens or 0
            _session_tokens["input"] += _last_turn_usage["input"]
            _session_tokens["output"] += _last_turn_usage["output"]
            _session_tokens["calls"] += 1

        if response.stop_reason != "tool_use":
            text = "".join(b.text for b in response.content if hasattr(b, "text"))
            # Print the response with label (non-streaming path)
            if text:
                print(f'  {MAGENTA}{BOLD}▌ TOMAS{RESET}')
                print(f'  {text}')
            return text

        # ── Tool-call loop limit ──
        tool_calls_this_round = sum(
            1 for b in response.content if b.type == "tool_use"
        )
        tool_call_count += tool_calls_this_round
        if tool_call_count > MAX_TOOL_CALLS_PER_TURN:
            print(f'    {RED}⚠{RESET}  Tool-call limit reached ({MAX_TOOL_CALLS_PER_TURN}). Stopping to prevent infinite loop.')
            messages.append({"role": "user", "content": [
                {"type": "text", "text": f"Tool call limit ({MAX_TOOL_CALLS_PER_TURN}) reached. Please summarize what you've found so far and provide a response to the user."}
            ]})
            # One more call to get the final summary
            try:
                final_resp = _get_client().messages.create(
                    model=_get_model(),
                    max_tokens=MAX_TOKENS,
                    system=system_prompt,
                    tools=combined_tools,
                    messages=messages,
                )
                return "".join(b.text for b in final_resp.content if hasattr(b, "text"))
            except Exception:
                return "I've reached the tool-call limit. Here's what I found so far — please ask me to continue if you need more."

        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                args_str = json.dumps(block.input)[:120]
                # Determine tool origin label
                tool_origin = ""
                if block.name in HANDLERS:
                    tool_origin = f"{DIM}[built-in]{RESET}"
                elif mcp_manager:
                    # Check renamed MCP tools first, then original names
                    mcp_orig = MCP_TOOL_NAME_MAP.get(block.name, block.name)
                    srv = mcp_manager.get_server_for_tool(mcp_orig)
                    if srv:
                        tool_origin = f"{DIM}[MCP: {srv}]{RESET}"
                print(f'    {YELLOW}⚡{RESET} {BOLD}{block.name}{RESET} {tool_origin}({DIM}{args_str}...{RESET})')
                if not check_permission(block.name, block.input):
                    result = "Error: user denied this tool call."
                else:
                    result = execute_tool(block.name, block.input)
                # ── Record tool call for self-improvement ──
                try:
                    self_improve.record_tool_call(block.name, block.input, result[:200])
                except Exception:
                    pass
                # Truncate large tool results to avoid blowing up context
                if isinstance(result, str) and len(result) > 100_000:
                    print(f'    {RED}⚠{RESET}  tool result truncated: {len(result)} chars → 100K')
                    result = result[:100_000] + f"\n[...truncated, full result was {len(result)} chars]"
                # ── Show a brief result preview so the user can see the
                #    tool's output inline in the chat (first 1-2 lines). ──
                if isinstance(result, str) and result.strip():
                    preview = result.strip().splitlines()
                    shown = preview[0][:160] if preview else ""
                    if len(preview) > 1 or len(result) > 160:
                        shown += f' {DIM}…{RESET}'
                    print(f'    {GREEN}↳{RESET} {DIM}{shown}{RESET}')
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })
        messages.append({"role": "user", "content": tool_results})


def _agent_loop_streamed(
    system_prompt: str, messages: list, combined_tools: list, tool_call_count: int
) -> str:
    """Stream the model response token-by-token for real-time output.

    Only handles the text-streaming part. If the model requests tools,
    returns the sentinel "__TOOLS__" so the caller can fall back to
    non-streaming for tool processing.

    Returns:
        - The full text response (if no tools were called)
        - "__TOOLS__" sentinel (if the model requested tool calls)
    """
    import anthropic

    text_parts: list[str] = []
    has_tool_use = False
    stop_reason = None
    usage_info = None

    print(f'  {MAGENTA}{BOLD}▌ TOMAS{RESET}')
    print(f'  ', end='', flush=True)

    try:
        with _get_client().messages.stream(
            model=_get_model(),
            max_tokens=MAX_TOKENS,
            system=system_prompt,
            tools=combined_tools,
            messages=messages,
        ) as stream:
            for event in stream:
                if event.type == "content_block_delta":
                    if event.delta.type == "text_delta":
                        text = event.delta.text
                        text_parts.append(text)
                        sys.stdout.write(text)
                        sys.stdout.flush()
                elif event.type == "content_block_start":
                    if event.content_block.type == "tool_use":
                        has_tool_use = True
                elif event.type == "message_stop":
                    final_msg = stream.get_final_message()
                    stop_reason = final_msg.stop_reason
                    usage_info = getattr(final_msg, "usage", None)
            print()  # end the streamed line
    except (AttributeError, TypeError) as e:
        # Provider doesn't support streaming — raise to trigger fallback
        raise
    except anthropic.InternalServerError:
        raise
    except Exception as e:
        # Other streaming errors — fall back
        print(f'\n  {YELLOW}⚠{RESET} Streaming interrupted, falling back...')
        raise TypeError(f"streaming failed: {e}")

    # Track usage
    global _last_turn_usage, _session_tokens
    if usage_info:
        _last_turn_usage["input"] = usage_info.input_tokens or 0
        _last_turn_usage["output"] = usage_info.output_tokens or 0
        _session_tokens["input"] += _last_turn_usage["input"]
        _session_tokens["output"] += _last_turn_usage["output"]
        _session_tokens["calls"] += 1

    full_text = "".join(text_parts)

    # If the model requested tools, return sentinel so caller falls back
    if has_tool_use or stop_reason == "tool_use":
        return "__TOOLS__"

    return full_text

# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------

SLASH_COMMANDS = {
    "help":         {"desc": "Show this help message",            "icon": "ℹ"},
    "clear":        {"desc": "Clear conversation history",        "icon": "✧"},
    "status":       {"desc": "Show current model and connection", "icon": "◈"},
    "model":        {"desc": "Display the active LLM model",      "icon": "◎"},
    "mode":         {"desc": "Show/change mode: /mode [auto|default]", "icon": "⚙"},
    "compact":      {"desc": "Force compact conversation now",    "icon": "⚙"},
    "skills":       {"desc": "List installed skills",            "icon": "⚡"},
    "skill":        {"desc": "Run a skill: /skill <name>",        "icon": "⚡"},
    "pdf-report":   {"desc": "Generate AI news PDF report",      "icon": "📄"},
    "zen":          {"desc": "OpenCode Zen proxy status",         "icon": "◉"},
    "self-improve": {"desc": "Self-improvement system status",    "icon": "🧠"},
    "si":           {"desc": "Alias for /self-improve",           "icon": "🧠"},
    "save":         {"desc": "Save current session",              "icon": "💾"},
    "load":         {"desc": "Load a saved session: /load <id>",  "icon": "📂"},
    "session":      {"desc": "Session mgmt: list/save/continue",  "icon": "📋"},
    "sessions":     {"desc": "Alias for /session list",            "icon": "📋"},
    "note":         {"desc": "Create a self-note: /note <title> <content>", "icon": "📝"},
    "notes":        {"desc": "List all self-notes",               "icon": "📒"},
    "exit":         {"desc": "Exit TOMAS",                        "icon": "✕"},
}

def _get_model() -> str:
    """Read model from environment."""
    return os.environ.get("AGENT_MODEL") or "Not set"


def _show_commands(match: str = "") -> str:
    """Build a formatted help string for matching slash commands."""
    lines = []
    lines.append(f'  {BOLD}Available commands{RESET}')
    lines.append(f'  {DIM}{"─" * 46}{RESET}')
    for cmd, info in sorted(SLASH_COMMANDS.items()):
        if match and not cmd.startswith(match.lower()):
            continue
        icon = info["icon"]
        desc = info["desc"]
        padded = " " * max(1, 12 - len(cmd))
        lines.append(f'    {DIM}{icon}{RESET}  {CYAN}/{cmd}{RESET}{padded}{DIM}{desc}{RESET}')
    lines.append('')
    lines.append(f'  {DIM}Type{RESET} {CYAN}/command{RESET} {DIM}to run — or just{RESET} {CYAN}/{RESET} {DIM}to see all{RESET}')
    lines.append(f'  {DIM}⇧+Space{RESET} {DIM}toggle  ·  {DIM}F5/6/7/8{RESET} {DIM}auto / default / strict / yolo{RESET}')
    return '\n'.join(lines)


def handle_slash_command(cmd_args: str, messages: list) -> str | None:
    """
    Handle a slash command (text after the '/').

    Returns:
      - response text to display
      - "__exit__"     to break the main loop
      - "__continue__" to skip agent processing and continue
      - None           if command produced no output
    """
    global mcp_manager, AUTO_APPROVE_LOW, YOLO_MODE
    parts = cmd_args.strip().split(maxsplit=1)
    cmd = parts[0].lower() if parts else ""

    # ── Just "/" alone — show all commands ──
    if not cmd:
        return _show_commands()

    # ── Handle aliases ──
    if cmd in ("q", "quit"):
        return "__exit__"
    if cmd in ("h", "?"):
        cmd = "help"

    # ── Match against known commands ──
    exact = [name for name in SLASH_COMMANDS if name == cmd]
    partial = [name for name in SLASH_COMMANDS if name.startswith(cmd)]

    if not exact and not partial:
        # No matches at all — show error with hint
        close = [name for name in SLASH_COMMANDS
                 if cmd in name or any(cmd[i:i+2] in name for i in range(len(cmd) - 1))]
        if close:
            return (
                f'  {YELLOW}Unknown command "{cmd}". Did you mean:{RESET}\n'
                + _show_commands()
            )
        return f'  {RED}Unknown command "{cmd}". Type{RESET} {CYAN}/{RESET} to see all commands.'

    if partial and not exact and len(partial) == 1:
        # Single unambiguous partial match — treat as the full command
        cmd = partial[0]
        exact = [cmd]

    if partial and not exact:
        # Multiple partial matches — show suggestions
        return _show_commands(cmd)

    # ── Execute the matched command ──
    cmd = exact[0]

    if cmd == "help":
        return _show_commands()

    if cmd == "clear":
        messages.clear()
        return f'  {GREEN}✓{RESET} Conversation cleared ({_get_model()}).'

    if cmd == "status":
        model_status = _get_model()
        cw = _current_context_window
        if YOLO_MODE:
            mode = "yolo"
            mode_color = RED
        else:
            mode = "auto" if AUTO_APPROVE_LOW else "default"
            mode_color = GREEN if AUTO_APPROVE_LOW else YELLOW
        lines = [
            f'  {BOLD}TOMAS Status{RESET}',
            f'  {DIM}{"─" * 46}{RESET}',
            f'  {CYAN}◎{RESET}  Model:     {model_status}',
            f'  {CYAN}▣{RESET}  Context:   {cw:,} tokens',
            f'  {CYAN}⚙{RESET}  Mode:      {mode_color}{mode}{RESET}',
            f'  {CYAN}✉{RESET}  Messages:  {len(messages)} in history',
        ]
        try:
            from mcp_manager import read_mcp_servers
            servers = read_mcp_servers()
            enabled = sum(1 for c in servers.values() if not c.get("disabled"))
            total = len(servers)
            lines.append(f'  {CYAN}⬡{RESET}  MCP:       {enabled}/{total} servers enabled')
        except Exception:
            pass
        if mcp_manager:
            lines.append(f'  {CYAN}⚡{RESET}  MCP tools: {len(mcp_manager.tools)} loaded')
        lines.append(f'  {CYAN}⌨{RESET}  Toggle:    {DIM}⇧+Space{RESET}  ·  {DIM}F5{RESET} auto  {DIM}F6{RESET} default  {DIM}F7{RESET} strict  {DIM}F8{RESET} yolo')
        # ── Token stats ──
        s = _session_tokens
        if s["calls"] > 0:
            pct = (s["input"] + s["output"]) / cw * 100
            lines.append(f'  {CYAN}≡{RESET}  Tokens:   {s["input"]:,} in · {s["output"]:,} out ({s["calls"]} calls) · {pct:.1f}% of {cw:,} used')
        return '\n'.join(lines)

    if cmd == "model":
        cw = _current_context_window
        return f'  {CYAN}◎{RESET} {BOLD}Model:{RESET} {_get_model()} ({cw:,} token context)'

    if cmd == "mode":
        arg = parts[1].lower() if len(parts) > 1 else ""
        if arg in ("auto", "automatic", "on", "1", "yes"):
            AUTO_APPROVE_LOW = True
            return (
                f'  {GREEN}✓{RESET} Mode set to {BOLD}auto{RESET} — '
                f'low-risk tools will be auto-approved.'
            )
        elif arg in ("default", "normal", "off", "0", "no"):
            AUTO_APPROVE_LOW = False
            return (
                f'  {GREEN}✓{RESET} Mode set to {BOLD}default{RESET} — '
                f'you will confirm each tool use.'
            )
        elif arg in ("strict",):
            AUTO_APPROVE_LOW = False
            YOLO_MODE = False
            # Strict mode removes any risk-level overrides made via "always"
            for k in list(RISK_LEVELS.keys()):
                if k not in ("read_file", "list_files", "search_code", "edit_file",
                             "write_file", "save_memory", "run_command", "fetch_url",
                             "fetch_url_with_browser", "search_web"):
                    RISK_LEVELS[k] = "high"
            return (
                f'  {GREEN}✓{RESET} Mode set to {BOLD}strict{RESET} — '
                f'all tools require confirmation, risk overrides cleared.'
            )
        elif arg in ("yolo", "YOLO"):
            AUTO_APPROVE_LOW = True
            YOLO_MODE = True
            return (
                f'  {BOLD}{RED}⚡ YOLO mode enabled!{RESET} {RED}All tools auto-approved.{RESET}'
            )
        # No arg or unknown arg — show current mode
        if YOLO_MODE:
            mode = "yolo"
        else:
            mode = "auto" if AUTO_APPROVE_LOW else "default"
        lines = [
            f'  {BOLD}Current Mode{RESET}',
            f'  {DIM}{"─" * 46}{RESET}',
            f'  {CYAN}⚙{RESET}  Mode:        {BOLD}{mode}{RESET}',
            f'  {CYAN}✓{RESET}  Auto-approve: {"ON" if AUTO_APPROVE_LOW else "OFF"}',
            '',
            f'  {DIM}Quick keys:{RESET}',
            f'    {DIM}⇧+Space{RESET}  — toggle  ·  {DIM}F5{RESET} auto  {DIM}F6{RESET} default  {DIM}F7{RESET} strict  {DIM}F8{RESET} yolo',
            f'  {DIM}Slash commands:{RESET}',
            f'    /mode auto     — auto-approve low-risk tools',
            f'    /mode default  — ask before every tool',
            f'    /mode strict   — ask for everything, clear overrides',
            f'    /mode yolo     — {RED}auto-approve ALL tools (no prompts){RESET}',
        ]
        return '\n'.join(lines)

    if cmd == "compact":
        if not messages:
            return f'  {DIM}No conversation to compact.{RESET}'
        before = len(messages)
        messages[:] = maybe_compact(messages)
        after = len(messages)
        return f'  {GREEN}✓{RESET} Compacted ({before} → {after} messages).'

    if cmd == "zen":
        try:
            from zen_proxy import check_status, ZEN_MODELS
            port = 6446
            running = check_status(port)
            lines = [
                f'  {BOLD}OpenCode Zen{RESET}',
                f'  {DIM}{"─" * 46}{RESET}',
                f'  {"◉" if running else "○"}  Proxy: {"running" if running else "not running"}',
            ]
            if running:
                base = os.environ.get("ANTHROPIC_BASE_URL", "")
                model = os.environ.get("AGENT_MODEL", "Not set")
                lines.append(f'  {CYAN}◈{RESET}  Endpoint: {base}')
                lines.append(f'  {CYAN}◎{RESET}  Model:    {model}')
                lines.append('')
                lines.append(f'  {DIM}Available free models:{RESET}')
                for m in ZEN_MODELS:
                    lines.append(f'    {DIM}•{RESET} {m}')
            else:
                lines.append('')
                lines.append(f'  {YELLOW}Tip: Start the proxy from the{RESET}')
                lines.append(f'  {YELLOW}  Connect / configure provider menu.{RESET}')
            return '\n'.join(lines)
        except Exception as e:
            return f'  {YELLOW}Zen: {e}{RESET}'

    if cmd == "skills":
        return cmd_skill_list()

    if cmd == "skill":
        skill_name = parts[1] if len(parts) > 1 else ""
        if not skill_name:
            lines = [f'  {YELLOW}Usage:{RESET} {CYAN}/skill <name>{RESET}']
            lines.append(f'  {DIM}Try {RESET}{CYAN}/skills{RESET} {DIM}to see available skills.{RESET}')
            return '\n'.join(lines)
        result = cmd_skill_run(skill_name)
        if result is None:
            return f'  {RED}Skill "{skill_name}" not found. Try{RESET} {CYAN}/skills{RESET}'
        # Inject skill content into the conversation as a user message
        messages.append({"role": "user", "content": result})
        print(f'  {YELLOW}⚡ Skill call:{RESET} {BOLD}{skill_name}{RESET} {DIM}[loaded into conversation]{RESET}')
        return None  # signals main loop to fall through to agent loop

    if cmd == "pdf-report":
        try:
            from pdf_report_skill import generate_ai_news_pdf
            output = generate_ai_news_pdf()
            return f'  {GREEN}✓{RESET} PDF report generated: {BLUE}{output}{RESET}'
        except Exception as e:
            return f'  {RED}⚠{RESET} PDF report failed: {e}'

    # ── Session commands ──
    if cmd in ("session", "sessions"):
        # "sessions" is an alias that defaults to the "list" subcommand.
        # parts is split with maxsplit=1, so parts[1] holds "sub [arg]".
        # Re-split to extract the subcommand and any trailing argument.
        rest = parts[1] if len(parts) > 1 else ""
        rest_parts = rest.split(maxsplit=1)
        sub = rest_parts[0].lower() if rest_parts else ""
        sid_arg = rest_parts[1].strip() if len(rest_parts) > 1 else ""
        # /sessions (alias) with no subcommand → list
        if cmd == "sessions" and not sub:
            sub = "list"

        if sub in ("list", "ls"):
            sessions = list_sessions(limit=20)
            if not sessions:
                return f'  {DIM}No saved sessions.{RESET}'
            lines = [f'  {BOLD}Saved Sessions ({len(sessions)}){RESET}']
            lines.append(f'  {DIM}{"─" * 60}{RESET}')
            for s in sessions:
                sid = s.get("id", "?")[:22]
                ts = s.get("timestamp_str", "?")
                proj = s.get("project", "?")
                msgs = s.get("message_count", 0)
                model = s.get("model", "?")[:25]
                summary = s.get("summary", "")[:60]
                lines.append(
                    f'  {CYAN}{sid}{RESET}  {DIM}{ts}{RESET}  '
                    f'{GREEN}{proj}{RESET}  {msgs}msgs'
                )
                if summary:
                    lines.append(f'  {DIM}  {summary}{RESET}')
                lines.append('')
            return '\n'.join(lines)

        elif sub in ("save",):
            sid = save_session(messages)
            s = _session_tokens
            return (
                f'  {GREEN}✓{RESET} Session saved: {CYAN}{sid}{RESET}\n'
                f'  {DIM}  Tokens: {s["input"]:,} in · {s["output"]:,} out'
                f' ({s["calls"]} calls){RESET}'
            )

        elif sub in ("continue", "load"):
            if not sid_arg:
                return f'  {YELLOW}Usage:{RESET} {CYAN}/session continue <id>{RESET}\n  {DIM}Use{RESET} {CYAN}/session list{RESET} {DIM}to see session IDs.{RESET}'
            sid = sid_arg
            loaded = continue_session(sid)
            if loaded is None:
                return f'  {RED}✗{RESET} Session not found: {sid}'
            # Replace current messages with the loaded ones
            messages.clear()
            messages.extend(loaded)
            # Show the full conversation history so the user has context
            lines = [
                f'  {GREEN}✓{RESET} Continuing session {CYAN}{sid}{RESET} '
                f'({len(loaded)} messages loaded).',
                f'  {DIM}{"─" * 50}{RESET}',
            ]
            for m in loaded:
                role = m.get("role", "?")
                content = m.get("content", "")
                text = _format_block_content(content)
                if role == "user":
                    icon = f'{GREEN}◆{RESET}'
                    label = f'{GREEN}{BOLD}You{RESET}'
                elif role == "assistant":
                    icon = f'{MAGENTA}▌{RESET}'
                    label = f'{MAGENTA}{BOLD}TOMAS{RESET}'
                else:
                    icon = f'{DIM}·{RESET}'
                    label = f'{DIM}{role}{RESET}'
                display = text.replace('\n', '\n      ')
                if len(display) > 300:
                    display = display[:300] + f' {DIM}…{RESET}'
                lines.append(f'  {icon} {label}')
                lines.append(f'      {display}')
            lines.append(f'  {DIM}{"─" * 50}{RESET}')
            lines.append(f'  {DIM}Type your next message to continue the conversation.{RESET}')
            return '\n'.join(lines)

        elif sub in ("delete", "rm"):
            if not sid_arg:
                return f'  {YELLOW}Usage:{RESET} {CYAN}/session delete <id>{RESET}'
            sid = sid_arg
            if delete_session(sid):
                return f'  {GREEN}✓{RESET} Session deleted: {sid}'
            else:
                return f'  {RED}✗{RESET} Session not found: {sid}'

        elif sub in ("latest",):
            latest = get_latest_session()
            if latest is None:
                return f'  {DIM}No saved sessions.{RESET}'
            sid = latest.get("id", "?")
            ts = latest.get("timestamp_str", "?")
            proj = latest.get("project", "?")
            msgs = latest.get("message_count", 0)
            summary = latest.get("summary", "")[:80]
            lines = [
                f'  {BOLD}Latest Session{RESET}',
                f'  {DIM}{"─" * 50}{RESET}',
                f'  ID:      {CYAN}{sid}{RESET}',
                f'  When:    {ts}',
                f'  Project: {GREEN}{proj}{RESET}',
                f'  Messages: {msgs}',
            ]
            if summary:
                lines.append(f'  Summary: {DIM}{summary}{RESET}')
            lines.append('')
            lines.append(f'  {YELLOW}Tip:{RESET} Use {CYAN}/session continue {sid}{RESET} to pick up where you left off.')
            return '\n'.join(lines)

        else:
            lines = [
                f'  {BOLD}Session Management{RESET}',
                f'  {DIM}{"─" * 50}{RESET}',
                f'  {CYAN}/session list{RESET}       — List saved sessions',
                f'  {CYAN}/session save{RESET}       — Save current conversation',
                f'  {CYAN}/session continue <id>{RESET}  — Load and continue a session',
                f'  {CYAN}/session latest{RESET}     — Show latest session info',
                f'  {CYAN}/session delete <id>{RESET}  — Delete a session',
            ]
            return '\n'.join(lines)

    # ── Self-improvement commands ──
    if cmd in ("self-improve", "si"):
        sub = parts[1].lower() if len(parts) > 1 else ""

        if sub in ("analyze", "scan"):
            self_improve.analyze_patterns(force=True)
            self_improve.generate_skills_for_all_ready_patterns()
            self_improve.generate_tips()
            self_improve.update_session_analysis()
            return f'  {GREEN}✓{RESET} Forced full analysis — patterns, skills, tips updated.'

        if sub in ("skills",):
            skills = self_improve.get_auto_generated_skills()
            if not skills:
                return f'  {DIM}No auto-generated skills yet.{RESET}'
            lines = [f'  {BOLD}Auto-Generated Skills{RESET}']
            lines.append(f'  {DIM}{"─" * 46}{RESET}')
            for s in skills:
                lines.append(f'    {DIM}📄{RESET} {s.get("name", "?")}')
            return '\n'.join(lines)

        if sub in ("tips",):
            tips = self_improve.get_tips()
            if not tips:
                return f'  {DIM}No tips generated yet.{RESET}'
            active = self_improve.get_active_tips()
            lines = [
                f'  {BOLD}Self-Improvement Tips{RESET}',
                f'  {DIM}{"─" * 46}{RESET}',
                f'  {DIM}Total: {len(tips)}  Active: {len(active)}{RESET}',
                '',
            ]
            for i, t in enumerate(tips):
                status = f'{GREEN}✓{RESET}' if t.get("applied") else f'{YELLOW}○{RESET}'
                lines.append(f'  {status} {t.get("message", "")}')
            return '\n'.join(lines)

        if sub in ("patterns",):
            patterns = self_improve.get_patterns()
            if not patterns:
                return f'  {DIM}No patterns detected yet.{RESET}'
            lines = [
                f'  {BOLD}Detected Patterns{RESET}',
                f'  {DIM}{"─" * 46}{RESET}',
            ]
            for p in sorted(patterns, key=lambda x: -x.get("count", 0)):
                ptype = p.get("type", "?")
                pcount = p.get("count", 0)
                if ptype == "frequent_tool":
                    lines.append(f'    {DIM}🔧{RESET} {p.get("tool")} ({pcount}×)')
                elif ptype == "topic":
                    lines.append(f'    {DIM}📌{RESET} topic: {p.get("keyword")} ({pcount}×)')
                elif ptype == "tool_sequence":
                    lines.append(f'    {DIM}🔗{RESET} {p.get("sequence")} ({pcount}×)')
                elif ptype == "repetition":
                    kws = ", ".join(p.get("keywords", [])[:3])
                    lines.append(f'    {DIM}🔁{RESET} repeated: {kws} ({pcount}×)')
            return '\n'.join(lines)

        # Default: show status
        return self_improve.get_self_improve_status()

    # ── Self-note commands ──
    if cmd == "note":
        # parts is split with maxsplit=1: parts[1] = "title content..."
        rest = parts[1] if len(parts) > 1 else ""
        rest_parts = rest.split(maxsplit=1)
        if len(rest_parts) < 2:
            return (
                f'  {YELLOW}Usage:{RESET} {CYAN}/note <title> <content>{RESET}\n'
                f'  {DIM}Create a self-note about something the agent learned.{RESET}'
            )
        title = rest_parts[0]
        content = rest_parts[1]
        note_id = self_notes.create_note(
            title=title,
            content=content,
            note_type="insight",
        )
        return f'  {GREEN}✓{RESET} Note created: {CYAN}{note_id}{RESET}'

    if cmd == "notes":
        notes = self_notes.list_notes(limit=20)
        if not notes:
            return f'  {DIM}No self-notes yet. Use /note <title> <content> to create one.{RESET}'
        lines = [f'  {BOLD}Self-Notes ({len(notes)}){RESET}']
        lines.append(f'  {DIM}{"─" * 50}{RESET}')
        for n in notes:
            nid = n.get("id", "?")[:22]
            title = n.get("title", "?")[:50]
            ntype = n.get("type", "insight")
            tags = n.get("tags", [])
            tag_str = f' [{", ".join(tags)}]' if tags else ""
            lines.append(f'  {DIM}[{ntype}]{RESET} {title} {DIM}{nid}{RESET}{tag_str}')
        return "\n".join(lines)

    if cmd == "exit":
        return "__exit__"

    return None


# ---------------------------------------------------------------------------
# Real-time input reader with slash suggestions
# ---------------------------------------------------------------------------

# Input history — persists across calls to read_input_with_suggestions
_input_history: list[str] = []
_history_index: int = 0


def _read_input_cross_platform(prompt: str) -> str:
    """Cross-platform input fallback for Linux/macOS.

    Uses standard input() with optional readline support for history.
    Provides basic slash-command tab completion via readline if available.
    """
    global _input_history

    # Try to enable readline for arrow-key history + tab completion
    try:
        import readline

        # Set up history file
        hist_file = Path.home() / ".tomas" / "input_history.txt"
        hist_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            readline.read_history_file(str(hist_file))
        except (OSError, FileNotFoundError):
            pass

        # Tab completion for slash commands
        def _slash_completer(text: str, state: int):
            if text.startswith('/'):
                matches = [f"/{n}" for n in SLASH_COMMANDS if n.startswith(text[1:])]
                return matches[state] if state < len(matches) else None
            return None

        readline.set_completer(_slash_completer)
        readline.parse_and_bind("tab: complete")
        readline.set_history_length(100)
    except ImportError:
        hist_file = None

    try:
        result = input(prompt).strip()
    except EOFError:
        return "exit"

    # Save to history
    if result and not result.startswith('/'):
        if not _input_history or _input_history[-1] != result:
            _input_history.append(result)
            if len(_input_history) > 100:
                _input_history.pop(0)

    # Save readline history
    if hist_file:
        try:
            import readline
            readline.write_history_file(str(hist_file))
        except (OSError, ImportError):
            pass

    return result


def read_input_with_suggestions(prompt: str) -> str:
    """Read a line of input with live slash-command suggestions beneath the prompt.

    Uses msvcrt (Windows) for character-by-character input.  When the line
    starts with ``/``, matching ``SLASH_COMMANDS`` are shown on the line
    directly below the prompt and updated after each keystroke.

    **Interactive features**:

    * ``↑`` / ``↓``  — navigate input history (when not in command mode)
    * ``↑`` / ``↓``  — navigate through suggestion items (in command mode)
    * ``Tab``        — auto-complete to the current match or common prefix
    * ``Enter``      — accept the highlighted suggestion (if any), or submit as-is
    * ``Esc``        — clear the input line

    Falls back to a cross-platform input with basic slash-command completion
    when msvcrt is unavailable (Linux/macOS).
    """
    try:
        import msvcrt  # Windows-only
        import sys
    except ImportError:
        return _read_input_cross_platform(prompt)

    global AUTO_APPROVE_LOW, YOLO_MODE, _history_index  # allow F-key / YOLO mode switching
    base_prompt = prompt
    sys.stdout.write(prompt)
    sys.stdout.flush()

    buffer: list[str] = []
    showing = False
    selected: int | None = None  # index of the currently highlighted suggestion

    # Reset history navigation to past-the-end
    _history_index = len(_input_history)

    # ── mode helpers ─────────────────────────────────────────────────┬─
    def _mode_badge() -> str:
        if YOLO_MODE:
            return f'[{RED}YOLO{RESET}]'
        mode = "auto" if AUTO_APPROVE_LOW else "default"
        color = GREEN if AUTO_APPROVE_LOW else YELLOW
        return f'[{color}{mode}{RESET}]'

    def _build_prompt() -> str:
        badge = _mode_badge()
        idx = base_prompt.find('TOMAS')
        if idx >= 0:
            return base_prompt[:idx] + badge + ' ' + base_prompt[idx:]
        return badge + ' ' + base_prompt

    def _set_mode(m: str):
        global AUTO_APPROVE_LOW, YOLO_MODE
        YOLO_MODE = False  # any explicit mode switch exits YOLO except YOLO itself
        if m == "auto":
            AUTO_APPROVE_LOW = True
        elif m == "default":
            AUTO_APPROVE_LOW = False
        elif m == "strict":
            AUTO_APPROVE_LOW = False
            # Reset non-built-in risk overrides
            builtins = {"read_file", "list_files", "search_code", "edit_file",
                        "write_file", "save_memory", "run_command", "fetch_url",
                        "fetch_url_with_browser", "search_web"}
            for k in list(RISK_LEVELS.keys()):
                if k not in builtins:
                    RISK_LEVELS[k] = "high"
        elif m == "yolo":
            AUTO_APPROVE_LOW = True
            YOLO_MODE = True

    # ── helpers ──────────────────────────────────────────────────────────

    def _repr() -> str:
        return ''.join(buffer)

    def _is_slash() -> bool:
        return _repr().startswith('/')

    def _cmd_filter() -> str:
        """Return the text after the leading ``/``, stripped + lowercased."""
        return _repr()[1:].strip().lower()

    def _get_matches() -> list[str]:
        cf = _cmd_filter()
        return sorted(
            name for name in SLASH_COMMANDS
            if not cf or name.startswith(cf)
        )

    def _refresh():
        """Clear the input line and re-draw prompt + buffer."""
        sys.stdout.write('\r\033[K')
        sys.stdout.write(_build_prompt())
        sys.stdout.write(_repr())
        sys.stdout.flush()

    def _show():
        nonlocal showing, selected
        matches = _get_matches()
        cf = _cmd_filter()

        # Mode status string (always shown in suggestion line)
        if YOLO_MODE:
            mode = "yolo"
            mode_clr = RED
        else:
            mode = "auto" if AUTO_APPROVE_LOW else "default"
            mode_clr = GREEN if AUTO_APPROVE_LOW else YELLOW
        mode_str = f'  {DIM}mode:{RESET} {mode_clr}{mode}{RESET}  {DIM}⇧+Space:toggle  F5/6/7/8:auto·default·strict·yolo{RESET}'

        if matches:
            parts = []
            for i, name in enumerate(matches):
                if selected is not None and i == selected:
                    parts.append(f'{BOLD}{CYAN}/{name}{RESET}')
                else:
                    parts.append(f'{CYAN}/{name}{RESET}')
            hint = f'  {DIM}↑↓·Tab·Enter{RESET}'
            text = '  ' + '  '.join(parts) + hint + mode_str
        else:
            # No matches — show every command dimmed + mode info
            parts = [f'{DIM}/{n}{RESET}' for n in sorted(SLASH_COMMANDS)]
            text = '  ' + '  '.join(parts) + mode_str
            selected = None

        # Move to the line below, clear it, print, return
        sys.stdout.write('\033[1B\r\033[2K')
        sys.stdout.write(text)
        sys.stdout.write('\033[1A\r')
        _refresh()
        showing = True

    def _hide():
        nonlocal showing, selected
        if showing:
            sys.stdout.write('\033[1B\r\033[2K\033[1A\r')
            _refresh()
            showing = False
            selected = None

    # ── main input loop ──────────────────────────────────────────────────

    try:
        while True:
            ch = msvcrt.getch()

            # ── Enter ──────────────────────────────────────────────────────
            if ch == b'\r':
                chosen = selected  # save before _hide() clears it
                _hide()
                sys.stdout.write('\n')
                sys.stdout.flush()
                # If a suggestion was highlighted, accept it
                if chosen is not None and _is_slash():
                    matches = _get_matches()
                    if chosen < len(matches):
                        buffer = ['/', matches[chosen]]
                result = _repr()
                # Save non-empty, non-command input to history (max 100)
                if result.strip() and not result.startswith('/'):
                    if not _input_history or _input_history[-1] != result:
                        _input_history.append(result)
                        if len(_input_history) > 100:
                            _input_history.pop(0)
                return result

            # ── Ctrl+C ────────────────────────────────────────────────────
            if ch == b'\x03':
                _hide()
                sys.stdout.write('\n')
                sys.stdout.flush()
                raise KeyboardInterrupt

            # ── Arrow / function keys ─────────────────────────────────────
            if ch == b'\xe0':
                ext = msvcrt.getch()
                if showing and _is_slash():
                    matches = _get_matches()
                    if not matches:
                        continue
                    if ext == b'H':  # ↑ Up arrow — navigate suggestions
                        selected = 0 if selected is None else max(0, selected - 1)
                        _show()
                    elif ext == b'P':  # ↓ Down arrow — navigate suggestions
                        selected = 0 if selected is None else min(len(matches) - 1, selected + 1)
                        _show()
                elif ext == b'H':  # ↑ Up arrow — history recall
                    if _input_history:
                        _hide()
                        if _history_index > 0:
                            _history_index -= 1
                        buffer = list(_input_history[_history_index])
                        _refresh()
                elif ext == b'P':  # ↓ Down arrow — history forward
                    _hide()
                    if _history_index < len(_input_history) - 1:
                        _history_index += 1
                        buffer = list(_input_history[_history_index])
                    else:
                        _history_index = len(_input_history)
                        buffer = []
                    _refresh()
                # ← / → are silently consumed
                continue

            # ── F5/F6/F7/F8 — quick mode switch ─────────────────────────
            if ch == b'\x00':
                ext2 = msvcrt.getch()
                if ext2 == b'\x3f':    # F5 — toggle auto/default
                    _set_mode("default" if AUTO_APPROVE_LOW else "auto")
                elif ext2 == b'\x40':  # F6 — default mode
                    _set_mode("default")
                elif ext2 == b'\x41':  # F7 — strict mode
                    _set_mode("strict")
                elif ext2 == b'\x42':  # F8 — YOLO mode
                    _set_mode("yolo")
                _refresh()
                if _is_slash():
                    _show()
                else:
                    _hide()
                continue

            # ── Tab — auto-complete ───────────────────────────────────────
            if ch == b'\t':
                if _is_slash():
                    cf = _cmd_filter()
                    if cf:
                        matches = sorted(
                            name for name in SLASH_COMMANDS
                            if name.startswith(cf)
                        )
                        if len(matches) == 1:
                            # Unambiguous → replace buffer with full command
                            buffer = ['/', matches[0]]
                            selected = None
                        elif len(matches) > 1:
                            # Several matches → extend to the longest common prefix
                            prefix = os.path.commonprefix(matches)
                            if prefix and prefix != cf:
                                buffer = ['/', prefix]
                                selected = None
                    _refresh()
                    _show()
                continue

            # ── Shift+Space — cycle mode: auto → default → yolo → auto
            if ch == b'\x20':
                shift_held = False
                try:
                    import ctypes
                    shift_held = bool(ctypes.windll.user32.GetAsyncKeyState(0x10) & 0x8000)
                except Exception:
                    pass
                if shift_held:
                    # Cycle: auto → default → yolo → auto → ...
                    if YOLO_MODE:
                        _set_mode("auto")
                    elif AUTO_APPROVE_LOW:
                        _set_mode("default")
                    else:
                        _set_mode("yolo")
                    _refresh()
                    if _is_slash():
                        _show()
                    else:
                        _hide()
                    continue  # Don't insert the space
                # Normal space: fall through to printable ASCII handler

            # ── Backspace ─────────────────────────────────────────────────
            if ch in (b'\x08', b'\x7f'):
                if buffer:
                    buffer.pop()
                    selected = None
                    _refresh()
                    if _is_slash():
                        _show()
                    else:
                        _hide()
                continue

            # ── Escape — clear whole input ────────────────────────────────
            if ch == b'\x1b':
                if buffer:
                    buffer.clear()
                    selected = None
                    _hide()
                    _refresh()
                continue

            # ── Printable ASCII ───────────────────────────────────────────
            if len(ch) == 1 and 32 <= ch[0] < 127:
                buffer.append(chr(ch[0]))
                selected = None
                _refresh()
                if _is_slash():
                    _show()
                else:
                    _hide()
                continue

            # ── Everything else (non-printable, utf-8 multi-byte, etc.) ──
            continue

    except KeyboardInterrupt:
        raise


# ---------------------------------------------------------------------------
# Conversation history display (used when continuing a session)
# ---------------------------------------------------------------------------

def _format_block_content(content, max_len: int = 200) -> str:
    """Format a message content (str or list of blocks) into a single string."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for block in content:
            if isinstance(block, dict):
                t = block.get("type", "")
                if t == "text":
                    texts.append(block.get("text", ""))
                elif t == "tool_use":
                    name = block.get("name", "?")
                    inp = json.dumps(block.get("input", {}))[:80]
                    texts.append(f"[tool_use: {name}({inp})]")
                elif t == "tool_result":
                    rc = str(block.get("content", ""))[:120]
                    texts.append(f"[tool_result: {rc}]")
            elif hasattr(block, "type"):
                t = getattr(block, "type", "")
                if t == "text":
                    texts.append(getattr(block, "text", ""))
                elif t == "tool_use":
                    texts.append(f"[tool_use: {getattr(block, 'name', '?')}]")
                elif t == "tool_result":
                    texts.append("[tool_result]")
        return " ".join(texts)
    return str(content)


def _print_conversation_history(messages: list) -> None:
    """Pretty-print a full conversation history to the terminal.

    Used when continuing a session so the user can see everything that was
    said before typing a new message.
    """
    for i, m in enumerate(messages):
        role = m.get("role", "?")
        content = m.get("content", "")
        text = _format_block_content(content)

        if role == "user":
            icon = f'{GREEN}◆{RESET}'
            label = f'{GREEN}{BOLD}You{RESET}'
        elif role == "assistant":
            icon = f'{MAGENTA}▌{RESET}'
            label = f'{MAGENTA}{BOLD}TOMAS{RESET}'
        else:
            icon = f'{DIM}·{RESET}'
            label = f'{DIM}{role}{RESET}'

        # Truncate very long messages for display, but keep a reasonable preview
        display = text.replace('\n', '\n      ')
        if len(display) > 500:
            display = display[:500] + f' {DIM}…({len(text)} chars total){RESET}'

        print(f'  {icon} {label}')
        print(f'      {display}')
        print()


# ---------------------------------------------------------------------------
# REPL
# ---------------------------------------------------------------------------

def main() -> int:
    global mcp_manager, _current_context_window, CONTEXT_WINDOW, CONTINUE_SESSION_ID

    # ── Auto-start Zen proxy if needed ──
    _ensure_zen_proxy()

    # ── Fetch real context window for the current model ──
    _current_context_window = _fetch_model_context_window()
    CONTEXT_WINDOW = _current_context_window

    # ── Startup banner ──
    print()
    print(f'  {CYAN}{BOLD}████████╗ ██████╗ ███╗   ███╗ █████╗ ███████╗{RESET}')
    print(f'  {CYAN}{BOLD}╚══██╔══╝██╔═══██╗████╗ ████║██╔══██╗██╔════╝{RESET}')
    print(f'  {CYAN}{BOLD}   ██║   ██║   ██║██╔████╔██║███████║███████╗{RESET}')
    print(f'  {CYAN}{BOLD}   ██║   ██║   ██║██║╚██╔╝██║██╔══██║╚════██║{RESET}')
    print(f'  {CYAN}{BOLD}   ██║   ╚██████╔╝██║ ╚═╝ ██║██║  ██║███████║{RESET}')
    print(f'  {CYAN}{BOLD}   ╚═╝    ╚═════╝ ╚═╝     ╚═╝╚═╝  ╚═╝╚══════╝{RESET}')
    print()
    print(f'  {DIM}Model:{RESET}    {CYAN}{_get_model()}{RESET}')
    cw_str = f'{_current_context_window:,}' if _current_context_window else '?'
    print(f'  {DIM}Context:{RESET}  {CYAN}{cw_str} tokens{RESET}')
    print(f'  {DIM}Project:{RESET}  {BLUE}{PROJECT_DIR.name}{RESET}')
    mode_name = 'auto' if AUTO_APPROVE_LOW else 'default'
    mode_color = GREEN if AUTO_APPROVE_LOW else YELLOW
    yolo_name = 'YOLO' if YOLO_MODE else ''
    mode_display = f'{mode_color}{mode_name}{RESET}'
    if YOLO_MODE:
        mode_display = f'{RED}{BOLD}YOLO ⚡{RESET}'
    print(f'  {DIM}Mode:{RESET}      {mode_display}')
    print()

    # ── Initialize MCP connections (single pass, no pre-test) ──
    global COMBINED_TOOLS
    from mcp_manager import read_mcp_servers
    try:
        mcp_manager = MCPManager()
        all_config = read_mcp_servers()
        disabled = {n for n, c in all_config.items() if c.get("disabled")}

        mcp_manager.discover_and_connect(config=all_config)

        # Compute summary after connecting
        connected_servers = set(mcp_manager.servers.keys())
        attempted = {n for n in all_config if n not in disabled}
        failed = attempted - connected_servers
        total_tools = len(mcp_manager.tools)

        if connected_servers:
            tag = ""
            if disabled:
                tag += f", {DIM}{len(disabled)} disabled{RESET}"
            if failed:
                tag += f", {RED}{len(failed)} failed{RESET}"
            print(f'  {GREEN}✓{RESET}  {BOLD}MCP:{RESET} {len(connected_servers)} connected ({total_tools} tools){tag}')
        else:
            parts = []
            if disabled:
                parts.append(f"{len(disabled)} disabled")
            if failed:
                parts.append(f"{len(failed)} failed")
            status = ", ".join(parts) if parts else f'{DIM}no servers configured{RESET}'
            print(f'  {DIM}MCP:{RESET} {status}')
        # Show per-server failure reasons
        if mcp_manager.failed_servers:
            for srv_name, err_msg in mcp_manager.failed_servers.items():
                print(f'  {RED}  ✗{RESET} {srv_name}: {DIM}{err_msg[:120]}{RESET}')

        # Pre-compute the final tool list once (not on every turn)
        global TOOL_TOKENS, MCP_TOOL_NAME_MAP
        if mcp_manager.tools:
            # Check for name conflicts with built-in tools and rename MCP tools accordingly
            builtin_names = {t["name"] for t in TOOLS}
            mcp_tools = []
            MCP_TOOL_NAME_MAP = {}
            renames = 0
            for t in mcp_manager.tools:
                original = t["name"]
                if original in builtin_names:
                    new_name = f"mcp_{original}"
                    renamed = dict(t)
                    renamed["name"] = new_name
                    # Update description to note the rename
                    renamed["description"] = f"[MCP: {original}] {renamed.get('description', '')}"
                    mcp_tools.append(renamed)
                    MCP_TOOL_NAME_MAP[new_name] = original
                    renames += 1
                else:
                    mcp_tools.append(t)
            combined = TOOLS + mcp_tools
            if renames:
                print(f'  {YELLOW}⚠{RESET}  Renamed {renames} MCP tool(s) to avoid built-in name conflicts')
            if len(combined) > 128:
                n_builtin = len(TOOLS)
                keep = 128 - n_builtin
                dropped = len(mcp_tools) - keep
                print(f'  {YELLOW}⚠{RESET}  Truncated MCP tools: keeping {keep} of {len(mcp_tools)} ({dropped} dropped, API 128-tool limit)')
                COMBINED_TOOLS = TOOLS + mcp_tools[:keep]
            else:
                COMBINED_TOOLS = combined
        else:
            COMBINED_TOOLS = TOOLS
            MCP_TOOL_NAME_MAP = {}
        TOOL_TOKENS = sum(len(json.dumps(t)) for t in COMBINED_TOOLS) // 6
    except Exception as e:
        print(f'  {RED}⚠{RESET}  MCP initialization failed: {e}')
        mcp_manager = MCPManager()
        COMBINED_TOOLS = TOOLS
        MCP_TOOL_NAME_MAP = {}
        TOOL_TOKENS = sum(len(json.dumps(t)) for t in COMBINED_TOOLS) // 6

    # ── Initialize self-improving system ──
    self_improve.init()
    print(f'  {GREEN}✦{RESET}  Self-improving system active — learning from interactions')

    print(f'  {DIM}─── Type {RESET}{BOLD}quit{RESET}{DIM} or {RESET}{BOLD}exit{RESET}{DIM} to leave · {RESET}{BOLD}/help{RESET}{DIM} for commands · Ctrl+C also works ───{RESET}')
    print()

    messages: list = []

    # ── Continue previous session if requested ──
    if CONTINUE_SESSION_ID:
        try:
            from session_manager import continue_session, load_session
            loaded = continue_session(CONTINUE_SESSION_ID)
            if loaded:
                messages = loaded
                print(f'  {GREEN}✓{RESET}  Continuing session {CYAN}{CONTINUE_SESSION_ID[:16]}{RESET} ({len(loaded)} messages)')
                print(f'  {DIM}{"─" * 50}{RESET}')
                _print_conversation_history(loaded)
                print(f'  {DIM}{"─" * 50}{RESET}')
                print(f'  {DIM}Type your next message to continue the conversation.{RESET}')
                print()
        except Exception as e:
            print(f'  {YELLOW}⚠{RESET}  Could not load session: {e}')

    try:
        while True:
            try:
                user_input = read_input_with_suggestions(f'  {CYAN}{BOLD}TOMAS{RESET}{CYAN} »{RESET} ').strip()
            except EOFError:
                break
            if user_input.lower() in ("quit", "exit"):
                break
            if not user_input:
                continue

            # ── Record for self-improvement ──
            self_improve.record_user_message(user_input)

            # ── Slash command handling ──
            if user_input.startswith('/'):
                result = handle_slash_command(user_input[1:], messages)
                if result == "__exit__":
                    break
                if result == "__continue__":
                    continue
                if result is not None:
                    # Non-None result from a processed slash command
                    print(f'  {MAGENTA}{BOLD}▌ TOMAS{RESET}')
                    print(f'  {result}')
                    print()
                    continue
                # None result: skill was loaded; fall through to agent loop
                # (skill content already injected by handle_slash_command)
            else:
                # Regular user message
                messages.append({"role": "user", "content": user_input})
            messages = maybe_compact(messages)
            system_prompt = build_system_prompt()  # re-inject every turn
            # Re-check compaction with the actual system prompt size
            messages = maybe_compact(messages, system_prompt)
            result = agent_loop(system_prompt, messages)
            # Streaming already printed the response with the ▌ TOMAS label.
            # For non-streaming fallback, result is the text — print it.
            if result and not result.startswith("I'm sorry"):
                # Check if streaming already output the text (it prints inline)
                # We detect this by checking if the result was already shown.
                # The streaming path prints text as it arrives and returns it.
                # To avoid double-printing, we only print if the result
                # wasn't streamed. We use a simple heuristic: streaming
                # sets a flag. For safety, if result contains text not
                # already shown, print it.
                pass  # streaming handles output; non-streaming also handles it
            elif result:
                # Error messages from the agent loop
                print(f'  {MAGENTA}{BOLD}▌ TOMAS{RESET}')
                print(f'  {result}')
            # ── Token usage info ──
            t = _last_turn_usage
            s = _session_tokens
            if s["calls"] > 0:
                pct = (t["input"] + t["output"]) / CONTEXT_WINDOW * 100
                print(f'  {DIM}┄  {t["input"]:,} in  {t["output"]:,} out  ·  total: {s["input"]:,} in  {s["output"]:,} out  ·  {pct:.1f}% of {CONTEXT_WINDOW:,} ctx{RESET}')
            print()
    except KeyboardInterrupt:
        print()
        print(f'  {DIM}Bye!{RESET}')
    finally:
        # ── Save session on exit ──
        if messages:
            try:
                sid = save_session(
                    messages,
                    model=_get_model(),
                    token_usage=dict(_session_tokens),
                    session_id=CONTINUE_SESSION_ID,
                )
                print(f'  {DIM}💾 Session saved: {sid}{RESET}')
            except Exception as e:
                print(f'  {DIM}⚠  Session save skipped: {e}{RESET}')
            CONTINUE_SESSION_ID = None
        # ── Clean up MCP connections ──
        if mcp_manager:
            mcp_manager.disconnect_all()
    return 0

if __name__ == "__main__":
    sys.exit(main())