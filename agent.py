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
import time
import json
import shutil
import tempfile
import subprocess
import urllib.request
import urllib.error
from pathlib import Path
from typing import Callable, Optional

# ── Windows console setup ──
# The UI prints box-drawing and symbol glyphs (▌ ✧ ⚙ ⚡ ↳). On a console whose
# codepage is not UTF-8 (cp1251, cp1252, cp437 — common on non-English Windows)
# print() raises UnicodeEncodeError and kills the agent. Force UTF-8 with a
# replacement fallback, and enable VT100 so ANSI colours work on legacy conhost.
if sys.platform == "win32":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    try:
        os.system("")  # enables ANSI escape processing in legacy consoles
    except Exception:
        pass

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

# Learning system (facts, retrieval, reflection)
import learning

# Agent core (headless, event-emitting) + the terminal front end for it
from core import loop as core_loop
from core.loop import run_turn
from core.permissions import ApprovalStore
from core.state import AgentState
from adapters.terminal import TerminalAdapter

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
# Per-session, not per-process. These used to accumulate for the life of the
# interpreter, so two sessions run back to back reported byte-identical usage
# and a session that did no work still claimed 1.6M input tokens.
_session_tokens = {"input": 0, "output": 0, "calls": 0}
_last_turn_usage = {"input": 0, "output": 0}

# ── Session telemetry (P6-8) ──
# Per-turn wall clock and per-tool-call outcome, so a saved session can say
# which call was slow and which one failed.
_turn_timings: list[float] = []
_tool_log: list[dict] = []
_session_started_at: float = time.time()
# Turns that produced no assistant reply (e.g. retries exhausted on a 429).
_failed_turns: list[dict] = []


def reset_session_state() -> None:
    """Start a fresh session's accounting. Called when a session begins or
    when /clear discards the conversation."""
    global _session_started_at
    _session_tokens.update({"input": 0, "output": 0, "calls": 0})
    _last_turn_usage.update({"input": 0, "output": 0})
    _turn_timings.clear()
    _tool_log.clear()
    _failed_turns.clear()
    _session_started_at = time.time()


def session_telemetry() -> dict:
    """Telemetry block for the session file."""
    total = round(sum(_turn_timings), 2)
    n = len(_turn_timings)
    return {
        "turn_metrics": {
            "total_duration_sec": total,
            "avg_turn_sec": round(total / n, 2) if n else 0.0,
            "turn_timings": [round(t, 2) for t in _turn_timings],
        },
        "tool_log": list(_tool_log),
        "failed_turns": list(_failed_turns),
    }

# ── Session continuation ──
# Set by agent_cli.py before calling main() to continue a previous session.
CONTINUE_SESSION_ID: Optional[str] = None

# ── Client factory: supports ANTHROPIC_EXTRA_HEADERS env var (JSON) ──
_client_instance = None

def _get_client():
    """Return a cached client for the active provider.

    An endpoint that speaks OpenAI wire format gets the in-process adapter
    (no daemon, real incremental streaming); everything else gets the
    Anthropic SDK. Both present the same surface to core/loop.py.
    """
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

    client = None
    try:
        import openai_adapter
        if openai_adapter.should_use_adapter():
            client = openai_adapter.build_from_active()
    except Exception:
        client = None

    if client is None:
        headers = json.loads(extra_hdr) if extra_hdr else None
        client = anthropic.Anthropic(
            api_key=key or None,
            base_url=base or None,
            default_headers=headers,
        )
    _client_instance = client
    _client_instance._cache_key = cache_key  # type: ignore[attr-defined]
    return _client_instance

def reinit_client():
    """Force the client to be re-created on next use (called after provider change)."""
    global _client_instance
    _client_instance = None


def _ensure_zen_proxy():
    """Start the Zen proxy daemon — only when explicitly asked for.

    Translation now runs in-process (openai_adapter), so the daemon is no
    longer on the agent's critical path. It stays available because pointing
    *other* tools at Zen is a real use for it: set TOMAS_ZEN_PROXY=1.
    """
    if os.environ.get("TOMAS_ZEN_PROXY", "") != "1":
        return
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
# Every tool that exists after name resolution — built-ins plus every
# connected MCP tool. COMBINED_TOOLS is the subset that fits the budget;
# select_tools() picks that subset per turn from this list.
ALL_TOOLS: list[dict] = []
TOOL_TOKENS: int = 0  # estimated token count for tool definitions
MCP_TOOL_NAME_MAP: dict[str, str] = {}  # renamed_name -> original_name for conflicting MCP tools


def resolve_mcp_tool_conflicts(mcp_tools: list[dict], builtin_names: Optional[set] = None) -> tuple[list[dict], dict[str, str], int]:
    """
    Resolve MCP/built-in tool name collisions by prefixing conflicting MCP
    tools with 'mcp_' (e.g. read_file -> mcp_read_file).

    Returns (resolved_mcp_tools, name_map, renames) where:
      - resolved_mcp_tools: MCP tools with collisions renamed (originals untouched)
      - name_map:          {renamed_name: original_name} for renamed tools
      - renames:           number of tools that were renamed
    """
    if builtin_names is None:
        builtin_names = {t["name"] for t in TOOLS}
    resolved: list[dict] = []
    name_map: dict[str, str] = {}
    renames = 0
    for t in mcp_tools:
        original = t["name"]
        if original in builtin_names:
            new_name = f"mcp_{original}"
            renamed = dict(t)
            renamed["name"] = new_name
            renamed["description"] = f"[MCP: {original}] {renamed.get('description', '')}"
            resolved.append(renamed)
            name_map[new_name] = original
            renames += 1
        else:
            resolved.append(t)
    return resolved, name_map, renames


def apply_tool_cap(mcp_tools: list[dict], max_allowed: int = 128) -> tuple[list[dict], int]:
    """
    Merge built-in TOOLS with MCP tools and truncate to max_allowed tools.
    Built-in tools are always kept; excess MCP tools are dropped in order.

    This is the no-context fallback. When there is a user message to select
    against, `select_tools` picks by relevance instead — see P4-8. Kept
    because startup has no context yet and something must be sent.

    Returns (combined_tools, dropped).
    """
    n_builtin = len(TOOLS)
    keep = max(0, max_allowed - n_builtin)
    dropped = max(0, len(mcp_tools) - keep)
    if dropped:
        return TOOLS + mcp_tools[:keep], dropped
    return TOOLS + mcp_tools, 0


def tool_relevance(tool: dict, query_keywords: set) -> float:
    """Score one tool against the words the user just used.

    Same shape as `learning.retrieval.score_fact` — one scoring idea, two
    uses. Name matches count double: a tool called `take_screenshot` is a
    better answer to "screenshot" than one that merely mentions screenshots
    in its description.
    """
    if not query_keywords:
        return 0.0
    from learning.text import extract_keywords
    name = (tool.get("name") or "").replace("_", " ").replace("-", " ")
    desc = tool.get("description") or ""
    name_words = set(extract_keywords(name, max_keywords=8))
    desc_words = set(extract_keywords(desc, max_keywords=20))
    score = 2.0 * len(query_keywords & name_words) + len(query_keywords & desc_words)
    # Cheap substring credit for compound names retrieval would not split.
    lowered = f"{tool.get('name','')} {desc}".lower()
    score += 0.5 * sum(1 for kw in query_keywords if len(kw) > 4 and kw in lowered)
    return score


def select_tools(all_tools: list[dict], context: str,
                 budget: int) -> tuple[list[dict], list[dict]]:
    """Fit the tool list to the budget by relevance to the current turn.

    Built-ins are always kept — they are what the agent is. The rest of the
    budget goes to the MCP tools most relevant to what the user is actually
    doing, rather than to whichever server connected first, which is what
    list-order truncation really selected on.

    Returns (selected, withheld).
    """
    builtin_names = {t["name"] for t in TOOLS}
    builtins = [t for t in all_tools if t["name"] in builtin_names]
    mcp = [t for t in all_tools if t["name"] not in builtin_names]
    remaining = max(0, budget - len(builtins))

    if len(mcp) <= remaining:
        return builtins + mcp, []
    if remaining == 0:
        return builtins, mcp

    from learning.text import extract_keywords
    query_keywords = set(extract_keywords(context or "", max_keywords=15))
    if not query_keywords:
        return builtins + mcp[:remaining], mcp[remaining:]

    scored = sorted(
        ((tool_relevance(t, query_keywords), i, t) for i, t in enumerate(mcp)),
        key=lambda x: (-x[0], x[1]),          # stable: original order breaks ties
    )
    chosen = [t for _, _, t in scored[:remaining]]
    withheld = [t for _, _, t in scored[remaining:]]
    return builtins + chosen, withheld


def withheld_tools_notice(withheld: list[dict]) -> str:
    """One line telling the model what it does not currently have.

    A silent capability gap is unrecoverable — the model cannot ask for a
    tool it has no idea exists. Naming the servers makes it recoverable.
    """
    if not withheld:
        return ""
    servers = []
    for t in withheld:
        origin = _tool_origin(t.get("name", ""))
        if origin.startswith("MCP: "):
            srv = origin[5:]
            if srv not in servers:
                servers.append(srv)
    where = f" from {', '.join(sorted(servers)[:6])}" if servers else ""
    return (f"\n\n{len(withheld)} additional tool(s){where} are connected but not "
            f"loaded for this turn. If you need one, say which capability you "
            f"need and the user can re-ask; do not assume it is unavailable.")


def _active_capabilities():
    """Probed capabilities of the active provider, or optimistic defaults.

    Never probes on this path — capabilities are data read from disk, and a
    turn must not pay network latency to find out what it can do.
    """
    try:
        import provider_manager
        return provider_manager.capabilities_for_active()
    except Exception:
        from types import SimpleNamespace
        return SimpleNamespace(
            streaming=True, tool_use=True, parallel_tool_calls=True,
            system_prompt=True, prompt_caching=False, vision=False,
            context_window=DEFAULT_CONTEXT_WINDOW, max_tools=128,
            max_output_tokens=MAX_TOKENS, probed_at=0.0)


def degrade_capability(field: str, reason: str = "") -> None:
    """Record that the provider cannot do something, and remember it.

    This is the whole of 'degrade, never fail': a capability the provider
    lacks costs the user a feature, never the session. The first discovery is
    paid once, then persisted so the next session starts already knowing.
    """
    try:
        import provider_manager
        provider = provider_manager.get_active()
        if provider is None or not getattr(provider.capabilities, field, False):
            return
        setattr(provider.capabilities, field, False)
        provider_manager.persist_capabilities(provider)
        note = f" ({reason})" if reason else ""
        print(f'  {YELLOW}⚠{RESET}  {provider.name} cannot {field.replace("_", " ")}'
              f'{note} — falling back{DIM}, remembered for next time{RESET}')
    except Exception:
        pass


def tool_ceiling() -> int:
    """How many tools this endpoint will accept.

    Read from the active provider's probed capabilities. It used to be
    inferred from whether the model name contained the substring "free" —
    which cut a model called `my-free-model` to a quarter of its budget for
    its name, and gave a self-hosted endpoint the cloud default it could not
    honour.
    """
    try:
        import provider_manager
        return max(1, int(provider_manager.capabilities_for_active().max_tools))
    except Exception:
        return 128


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
        "description": "Replace a string in a file. old_string must appear exactly once, unless replace_all is true.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Absolute or project-relative path"},
                "old_string": {"type": "string", "description": "Exact text to find"},
                "new_string": {"type": "string", "description": "Replacement text"},
                "replace_all": {"type": "boolean", "description": "Replace every occurrence instead of requiring a unique match. Use this for mechanical substitutions rather than issuing one call per site."},
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
        "description": "Execute a shell command. Returns '[exit N — ok|FAILED]' followed by stdout and any stderr, so you never need to append '2>&1' or infer success from the text.",
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
        "description": "Search for a regex pattern across files. Returns the true match count; page through large result sets with offset.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex pattern"},
                "path": {"type": "string", "description": "Directory or single file to search. Defaults to project root."},
                "file_glob": {"type": "string", "description": "File pattern filter, e.g. '*.py'. Ignored when path is a file."},
                "offset": {"type": "integer", "description": "Skip this many matches. Default 0."},
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
    {
        "name": "read_mcp_resource",
        "description": (
            "Read a resource exposed by a connected MCP server (a file, "
            "database row, document, or similar). Call with no arguments to "
            "list what is available. Resources are read-only data the server "
            "publishes; they are not tools."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "uri": {"type": "string", "description": "Resource URI. Omit to list all available resources."},
                "server": {"type": "string", "description": "MCP server name. Only needed when two servers publish the same URI."},
            },
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
    "read_mcp_resource": "low",
}

# Commands that only read. `run_command` is the most-used tool in the corpus
# (72 of 209 calls) and every one of them blocked on a prompt, because
# `git status` and `rm -rf` shared a single tier.
READONLY_CMD = re.compile(
    r'^\s*(git\s+(status|log|diff|show|branch)\b'
    r'|(?:[\w.\\/:-]*[\\/])?python(?:\.exe)?\s+-m\s+(?:unittest|pytest)\b'
    r'|pytest\b|dir\b|ls\b|type\b|cat\b|echo\b|where\b|which\b|findstr\b'
    r'|pip\s+(list|show|freeze)\b)',
    re.I,
)

# Any of these turns a command into something a single-token classifier cannot
# reason about. The corpus contains `del x && type y` — a delete wearing a
# read's clothes — and `dir /b n* & echo --- & type n`, where cmd.exe treats a
# lone `&` as a separator. Chaining is not classified; it is high.
_CMD_SEPARATORS = ("&&", "||", ";", "|", ">", "<", "&", "`", "$(")


def risk_for(name: str, params: Optional[dict] = None) -> str:
    """Risk tier for a specific call, not just a tool name."""
    if name == "run_command":
        cmd = (params or {}).get("command", "")
        if any(sep in cmd for sep in _CMD_SEPARATORS):
            return "high"
        if re.search(r'\b(del|rm|rmdir|erase|move|ren|format|curl|wget)\b', cmd, re.I):
            return "high"
        return "low" if READONLY_CMD.match(cmd) else "high"
    return RISK_LEVELS.get(name, "high")

# Patterns that are always blocked from run_command
BLOCKED_PATTERNS = ["rm -rf /", "mkfs", "> /dev/sd", "dd if=/dev/zero", ":(){:|:&};:"]

# Ceiling on a single read_file result. The line limit alone is not a size
# limit — one 2000-line read put 45 KB into the context in a single result.
MAX_READ_FILE_CHARS = 20000

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _resolve(path: str) -> Path:
    p = Path(path)
    if not p.is_absolute():
        p = PROJECT_DIR / p
    return p.resolve()

# All user state lives under ~/.tomas (sessions, notes, memory, learned
# skills). Locking the agent out of it does not make anything safer — it just
# pushes the same read through `run_command`, which is a higher risk tier.
TOMAS_HOME = (Path.home() / ".tomas").resolve()


def _within(p: Path, root: Path) -> bool:
    return p == root or root in p.parents


def _safe(p: Path, write: bool = False) -> bool:
    """Project dir is read-write. ~/.tomas is read-only: it is written through
    the typed APIs (save_memory, self_notes, session_manager) that own each
    file's schema, never by hand."""
    if _within(p, PROJECT_DIR):
        return True
    return not write and _within(p, TOMAS_HOME)


def _outside_project_error(path: Path, write: bool = False) -> str:
    """Say which rule was hit, so the model corrects instead of retrying."""
    if write and _within(path, TOMAS_HOME):
        return (f"Error: {path} is under ~/.tomas, which is read-only. "
                f"Use save_memory or the self_notes API to write there.")
    return (f"Error: path outside project: {path}. "
            f"Readable roots: {PROJECT_DIR} (read-write), {TOMAS_HOME} (read-only).")

# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

def handle_read_file(params: dict) -> str:
    path = _resolve(params["file_path"])
    if not _safe(path):
        return _outside_project_error(path)
    if not path.exists():
        return f"Error: file not found: {path}"
    offset = max(0, int(params.get("offset", 1)) - 1)
    limit = int(params.get("limit", 2000))
    with path.open("r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    selected = lines[offset:offset + limit]
    if not selected:
        return "(empty file)"
    # A line limit alone is not a size limit: a 2000-line read of a wide file
    # put 45 KB into the context in one tool result. Cap by characters too,
    # and say where to resume so the truncation is recoverable.
    out: list[str] = []
    used = 0
    for i, line in enumerate(selected):
        entry = f"{i + offset + 1:6}\t{line}"
        if used + len(entry) > MAX_READ_FILE_CHARS:
            next_line = i + offset + 1
            out.append(f"\n... [truncated at line {next_line} of {len(lines)} — "
                       f"re-read with offset={next_line} to continue] ...\n")
            break
        out.append(entry)
        used += len(entry)
    return "".join(out)

def handle_write_file(params: dict) -> str:
    path = _resolve(params["file_path"])
    if not _safe(path, write=True):
        return _outside_project_error(path, write=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(params["content"], encoding="utf-8")
    return f"Successfully wrote {len(params['content'])} chars to {path}"

def handle_edit_file(params: dict) -> str:
    path = _resolve(params["file_path"])
    if not _safe(path, write=True):
        return _outside_project_error(path, write=True)
    if not path.exists():
        return f"Error: file not found: {path}"
    content = path.read_text(encoding="utf-8")
    old = params["old_string"]
    replace_all = bool(params.get("replace_all", False))
    count = content.count(old)
    if count == 0:
        return f"Error: old_string not found in {path}"
    if count > 1 and not replace_all:
        # Ambiguity still fails loudly; replace_all is how you say "all of them"
        # instead of synthesising N disambiguating contexts by hand.
        return (f"Error: old_string matches {count} locations; be more specific, "
                f"or pass replace_all=true to replace all {count}.")
    new_content = content.replace(old, params["new_string"], -1 if replace_all else 1)

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
    note = f", {count} replacements" if replace_all and count > 1 else ""
    return f"Successfully edited {path} (+{n_add} -{n_del} lines{note})\n\n{colored_diff}"

def handle_list_files(params: dict) -> str:
    path = _resolve(params.get("path", "."))
    if not _safe(path):
        return _outside_project_error(path)
    if not path.exists():
        return f"Error: directory not found: {path}"
    entries = []
    for child in sorted(path.iterdir()):
        # skip noise
        if child.name in {".git", "__pycache__", ".agent"}:
            continue
        entries.append(f"{'[dir] ' if child.is_dir() else '      '}{child.name}")
    return "\n".join(entries) if entries else "(empty)"

# Matches a `python -c "..."` payload at the end of a command line. cmd.exe
# cannot carry newlines or nested quotes through such a payload, so it is
# round-tripped via a temporary script file instead.
_PYTHON_INLINE_RE = re.compile(
    r'python(?:\.exe)?\s+(?:-u\s+)?-c\s+"(.*)"\s*$', re.S | re.I
)
# `python -c` without -u: cmd.exe swallows stdout of short-lived processes.
_PYTHON_DASH_C_RE = re.compile(r'\bpython(\.exe)?\s+-c\b', re.I)


def _normalise_windows_command(cmd: str) -> tuple[str, Optional[str]]:
    """Work around two cmd.exe defects around inline python payloads.

    Returns (command, temp_dir_to_clean). The temp directory is created
    outside the project so scratch files never land in the source tree and
    never collide with `unittest discover`.
    """
    if sys.platform != "win32":
        return cmd, None
    # 1. Force unbuffered output.
    cmd = _PYTHON_DASH_C_RE.sub(lambda m: f"python{m.group(1) or ''} -u -c", cmd)
    # 2. Multi-line or nested-quote payloads cannot survive cmd.exe tokenising.
    m = _PYTHON_INLINE_RE.search(cmd)
    if m and ("\n" in m.group(1) or "'" in m.group(1)):
        temp_dir = tempfile.mkdtemp(prefix="tomas_exec_")
        script = Path(temp_dir) / "_exec.py"
        script.write_text(m.group(1), encoding="utf-8")
        cmd = cmd[:m.start()] + f'"{sys.executable}" -u "{script}"'
        return cmd, temp_dir
    return cmd, None


def handle_run_command(params: dict) -> str:
    cmd = params["command"]
    for bad in BLOCKED_PATTERNS:
        if bad in cmd:
            return f"Error: blocked dangerous pattern: {bad}"
    timeout = int(params.get("timeout", 120))

    cmd, temp_dir = _normalise_windows_command(cmd)
    # Child processes must emit UTF-8 rather than the console codepage,
    # otherwise non-ASCII output is mangled beyond recovery on the way back.
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True,
            encoding="utf-8", errors="replace",
            timeout=timeout, cwd=str(PROJECT_DIR), env=env,
        )
    except subprocess.TimeoutExpired:
        return f"Error: command timed out after {timeout}s"
    finally:
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)

    parts = []
    if (result.stdout or "").strip():
        parts.append(result.stdout.rstrip())
    if (result.stderr or "").strip():
        parts.append(f"[stderr]\n{result.stderr.rstrip()}")
    body = "\n".join(parts) or "(no output)"
    if len(body) > 30000:
        body = body[:15000] + "\n\n... [truncated] ...\n\n" + body[-15000:]
    # The exit code is always reported. A command that fails while still
    # writing to stdout used to be indistinguishable from one that succeeded.
    status = "ok" if result.returncode == 0 else f"FAILED (exit {result.returncode})"
    return f"[exit {result.returncode} — {status}]\n{body}"

SEARCH_PAGE_SIZE = 50


def handle_search_code(params: dict) -> str:
    pattern = params["pattern"]
    path = _resolve(params.get("path", "."))
    if not _safe(path):
        return _outside_project_error(path)
    file_glob = params.get("file_glob", "")
    offset = max(0, int(params.get("offset", 0)))

    # A file is a legitimate search target. rglob() on one yields nothing, so
    # the old code answered "no matches" for a path that was never searched —
    # a confident false negative the model had no reason to doubt.
    if path.is_file():
        candidates = [path]
    elif path.is_dir():
        candidates = path.rglob(file_glob) if file_glob else path.rglob("*")
    else:
        return f"Error: path does not exist: {path}"

    try:
        regex = re.compile(pattern)
    except re.error as e:
        return f"Error: invalid regex {pattern!r}: {e}"

    matches: list[str] = []
    total = 0
    for file in candidates:
        if not file.is_file():
            continue
        try:
            text = file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if regex.search(line):
                total += 1
                if offset < total <= offset + SEARCH_PAGE_SIZE:
                    matches.append(f"{file}:{i}: {line}")

    if total == 0:
        return f"No matches for pattern: {pattern}"
    header = f"{total} match{'' if total == 1 else 'es'}"
    shown = len(matches)
    if total > offset + shown:
        # Report the true total so the model can decide whether to page,
        # instead of guessing what "truncated" hid.
        header += (f" — showing {offset + 1}-{offset + shown}; "
                   f"re-run with offset={offset + shown} for more")
    elif offset:
        header += f" — showing {offset + 1}-{offset + shown}"
    return header + "\n" + "\n".join(matches)

def handle_save_memory(params: dict) -> str:
    save_memory(params["key"], params["description"], params["content"])
    return f"Saved memory '{params['key']}'"


def handle_read_mcp_resource(params: dict) -> str:
    """Read (or list) MCP resources.

    The MCP spec defines resources alongside tools, and much of the
    ecosystem's value sits there; the manager surfaced only tools until now.
    """
    if mcp_manager is None:
        return "Error: no MCP servers are connected."
    uri = (params or {}).get("uri", "").strip()
    server = (params or {}).get("server") or None
    if not uri:
        resources = mcp_manager.list_resources()
        if not resources:
            return "No MCP resources are available from the connected servers."
        lines = [f"{len(resources)} MCP resource(s) available:"]
        for r in resources[:100]:
            desc = (r.get("description") or r.get("name") or "")[:90]
            lines.append(f"  [{r['server']}] {r.get('uri', '?')}  {desc}")
        if len(resources) > 100:
            lines.append(f"  ... and {len(resources) - 100} more")
        return "\n".join(lines)
    return mcp_manager.read_resource(uri, server=server)


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
    """Search the internet using DDGS / Playwright by default, with fallbacks."""
    query = params["query"]
    max_results = int(params.get("max_results", 5))

    # ── Primary: DDGS ──
    try:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS

        results = []
        with DDGS() as ddgs:
            for i, r in enumerate(ddgs.text(query, max_results=max_results)):
                title = r.get("title", "?")
                body = r.get("body", "?")
                href = r.get("href", "?")
                results.append(f"{i+1}. {title}\n   {body}\n   URL: {href}")

        if results:
            return f"Search results for '{query}':\n\n" + "\n\n".join(results)

    except Exception:
        pass

    # ── Secondary: Playwright Bing / Google Chrome ──
    try:
        from playwright.async_api import async_playwright
        import urllib.parse
        import asyncio

        encoded_query = urllib.parse.quote_plus(query)
        search_url = f"https://www.bing.com/search?q={encoded_query}"

        async def _playwright_search():
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                )
                await page.goto(search_url, timeout=15000, wait_until="domcontentloaded")
                
                results = []
                elements = await page.query_selector_all("li.b_algo")
                for el in elements:
                    title_el = await el.query_selector("h2 a")
                    snippet_el = await el.query_selector("p, div.b_caption")
                    if title_el:
                        title = (await title_el.inner_text()).strip()
                        href = await title_el.get_attribute("href") or ""
                        snippet = (await snippet_el.inner_text()).strip() if snippet_el else ""
                        if title and href.startswith("http"):
                            results.append(f"{len(results)+1}. {title}\n   {snippet}\n   URL: {href}")
                            if len(results) >= max_results:
                                break
                await browser.close()
                if results:
                    return f"Search results for '{query}' (via Playwright / Chrome):\n\n" + "\n\n".join(results)
                return None

        pw_results = asyncio.run(_playwright_search())
        if pw_results:
            return pw_results
    except Exception:
        pass

    # ── Tertiary fallback: DuckDuckGo HTML ──
    try:
        import urllib.request
        import urllib.parse
        import re

        encoded = urllib.parse.quote_plus(query)
        req = urllib.request.Request(
            f"https://html.duckduckgo.com/html/?q={encoded}",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode('utf-8', errors='ignore')
            results = []
            matches = re.findall(r'<a class="result__a" href="([^"]+)">(.*?)</a>', html)
            for i, (href, title_html) in enumerate(matches[:max_results]):
                clean_title = re.sub(r'<[^>]+>', '', title_html).strip()
                results.append(f"{i+1}. {clean_title}\n   URL: {href}")
            if results:
                return f"Search results for '{query}':\n\n" + "\n\n".join(results)
    except Exception as e:
        return f"Error searching the web: {e}"

    return f"No results found for '{query}'"


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
    "read_mcp_resource": handle_read_mcp_resource,
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
    """Legacy entry point. The agent loop now asks through a
    PermissionResponder (core/permissions.py); this remains for any direct
    caller and shares the same session-scoped ApprovalStore.

    Note it no longer downgrades RISK_LEVELS on "always": that turned one
    approval of, say, `git status` into a blanket grant on every future
    run_command. Approval is scoped to the exact call the user saw.
    """
    if YOLO_MODE:
        return True  # YOLO mode approves everything
    if APPROVALS.is_approved(name, params):
        return True
    risk = risk_for(name, params)
    if risk == "low" and AUTO_APPROVE_LOW:
        return True

    from core.events import PermissionNeeded
    decision = TerminalAdapter().ask(
        PermissionNeeded("", name, params, risk))
    if decision == "always_allow_this_call":
        APPROVALS.approve(name, params)
        return True
    return decision == "allow"

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
- If a task is done, stop calling tools and summarize.
- Memory files listed in the memory index can be read with read_file when you need their detail.
- A denied tool call will stay denied. Do not re-issue it with different wording.
- Reply in the language the user wrote in."""


def _classify_mcp_failures(failed: dict) -> tuple[list, list]:
    """Split MCP failures into 'needs credentials' and 'actually broken'.

    A 401 on an optional server is a permanent, expected state — it should
    not be styled like a crash.
    """
    needs_auth, broken = [], []
    for name, err in (failed or {}).items():
        text = str(err).lower()
        if any(m in text for m in ("401", "403", "unauthor", "forbidden",
                                   "authenticat", "api key", "token")):
            needs_auth.append(name)
        else:
            broken.append(name)
    return needs_auth, broken


def _environment_section() -> str:
    """Tell the model which shell it actually has.

    Without this it reached for `rm`, `ls` and `test -f` on cmd.exe — POSIX
    commands that simply do not exist there — and burned tool calls finding
    out. One line removes the whole class of mistake.
    """
    if sys.platform == "win32":
        return (
            "\n\n# Environment\n"
            "- OS: Windows. `run_command` runs through **cmd.exe**, not bash.\n"
            "- Use `dir`, `type`, `del`, `copy`, `move`, `findstr` — not "
            "`ls`, `cat`, `rm`, `cp`, `mv`, `grep`, `test -f`.\n"
            "- Prefer the built-in tools over shell equivalents: `list_files` "
            "over `dir`, `read_file` over `type`, `search_code` over `findstr`.\n"
            f"- Python interpreter: `{sys.executable}`"
        )
    return (
        "\n\n# Environment\n"
        f"- OS: {sys.platform}. `run_command` runs through a POSIX shell.\n"
        f"- Python interpreter: `{sys.executable}`"
    )

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
MAX_LEARNED_CHARS = 1500            # retrieved facts (bounded by k, not by store size)
MAX_SKILLS_CHARS = 4000             # skills section
MAX_TOTAL_SYSTEM_PROMPT = 20000     # hard cap on the entire system prompt


def build_system_prompt(user_message: str = "") -> str:
    """Build the system prompt for this turn.

    `user_message` is what learned knowledge is retrieved against. An empty
    query falls back to the most recently confirmed facts, so callers that
    have no message yet still get something sensible.
    """
    prompt = BASE_PROMPT + _environment_section()
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
    # ── What the agent has learned — retrieved, not dumped ──
    # This replaces the old memory-index dump, the notes dump and the tips
    # block. Those three grew with everything ever learned until entries
    # silently fell off the end of the budget; retrieval keeps the prompt
    # flat in size no matter how much is stored.
    try:
        learned = learning.recall(user_message, k=5)
        if learned:
            learned = _truncate_section(learned, MAX_LEARNED_CHARS, "learned")
            prompt += ("\n\n# What I've learned about this user and project\n"
                       f"{learned}")
    except Exception:
        pass
    # installed skills — budgeted by whole entries, not by slicing the joined
    # string at a character offset (which used to cut mid-skill-name).
    skills_section = build_skills_section(max_chars=MAX_SKILLS_CHARS)
    if skills_section:
        prompt += f"\n\n{skills_section}"
    # NOTE: the self-improvement tips/session-context block used to be injected
    # here. It was template text addressed to a human developer ("Consider
    # creating shortcuts or aliases for this tool") that consumed context and
    # changed nothing about the model's behaviour. Reflection replaces it; the
    # generator code is still in self_improve.py pending deletion.
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
    """Persist an explicit "remember this" from the user.

    Writes the markdown file (still useful to read and edit by hand) and
    records the same thing as an `explicit` fact, which is what retrieval
    actually reads. Explicit means the user said it outright — no inference —
    so it goes active immediately rather than through the evidence gate.
    """
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

    if learning.is_enabled():
        try:
            learning.remember("explicit", f"{description}: {content}".strip(": "),
                              evidence=f"user asked to remember '{key}'",
                              scope="global")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Learning system wiring
# ---------------------------------------------------------------------------

def _learning_call_model(model: str, system: str, messages: list,
                         max_tokens: int) -> str:
    """Adapter so learning/ can reach the model without importing agent."""
    resp = _get_client().messages.create(
        model=model, max_tokens=max_tokens, system=system, messages=messages,
    )
    return "".join(b.text for b in resp.content if hasattr(b, "text"))


def init_learning() -> None:
    """Scope the store to this project and import the pre-Phase-3 stores."""
    try:
        learning.set_project(PROJECT_DIR)
        imported = learning.migrate_legacy_stores()
        if imported:
            print(f'  {GREEN}✦{RESET} {DIM}Imported {imported} existing '
                  f'memories/notes into the learning store{RESET}')
    except Exception:
        pass


def reflect_on_session_end(messages: list) -> None:
    """Learn from the finished session. Never raises, never blocks the user."""
    if not learning.is_enabled():
        return
    try:
        outcome = learning.run_session_reflection(
            messages, call_model=_learning_call_model)
        for scope in ("global", "project"):
            learning.decay(scope)
        if not outcome:
            return
        if outcome.get("mode") == "shadow":
            print(f'  {DIM}🧠 Reflection logged (shadow mode) — '
                  f'review it with /si reflect{RESET}')
        for summary in outcome.get("promoted", []):
            print(f'  {DIM}🧠 Learned: {summary}{RESET}')
    except Exception:
        pass


def _ago(timestamp: float) -> str:
    if not timestamp:
        return "never"
    seconds = max(0, time.time() - timestamp)
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{int(seconds // size)}{unit} ago"
    return "just now"


def _render_learned_facts() -> str:
    """Everything the agent believes, with the evidence behind it."""
    try:
        rows = []
        for scope in ("global", "project"):
            for fact in learning.load_facts(scope):
                fact = dict(fact)
                fact["scope"] = scope
                rows.append(fact)
    except Exception as e:
        return f'  {RED}✗{RESET} Could not read the learning store: {e}'

    if not rows:
        return (f'  {DIM}Nothing learned yet. Facts appear here as sessions '
                f'accumulate evidence.{RESET}')

    order = {"active": 0, "candidate": 1, "observed": 2}
    rows.sort(key=lambda f: (order.get(f.get("status"), 3),
                             -f.get("evidence_count", 0)))
    active = sum(1 for f in rows if f.get("status") == "active")

    lines = [
        f'  {BOLD}What I have learned{RESET}',
        f'  {DIM}{"─" * 56}{RESET}',
        f'  {DIM}{active} active · {len(rows) - active} still gathering evidence '
        f'· promotes at {learning.PROMOTE_AT}{RESET}',
        '',
    ]
    for fact in rows:
        status = fact.get("status", "observed")
        mark = {"active": f'{GREEN}●{RESET}',
                "candidate": f'{YELLOW}◐{RESET}'}.get(status, f'{DIM}○{RESET}')
        scope = fact.get("scope", "global")
        text = (fact.get("fact") or "").replace("\n", " ")[:100]
        lines.append(f'  {mark} {text}')
        lines.append(f'      {DIM}{fact.get("id", "?")} · {scope} · '
                     f'{fact.get("evidence_count", 0)}× · '
                     f'confirmed {_ago(fact.get("last_seen", 0))}{RESET}')
        evidence = (fact.get("evidence") or [])
        if evidence:
            lines.append(f'      {DIM}└ {str(evidence[-1])[:90]}{RESET}')
    lines.append('')
    lines.append(f'  {DIM}/forget <id> removes one permanently.{RESET}')
    return '\n'.join(lines)


def _render_reflection_log(limit: int = 5) -> str:
    """What reflection would have learned — the shadow-mode review screen."""
    path = learning.LEARNED_DIR / "reflection-log.jsonl"
    if not path.exists():
        return (f'  {DIM}No reflection runs yet. Reflection happens when a '
                f'session ends.{RESET}')
    try:
        entries = [json.loads(ln) for ln in
                   path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    except (OSError, json.JSONDecodeError) as e:
        return f'  {RED}✗{RESET} Could not read the reflection log: {e}'
    if not entries:
        return f'  {DIM}Reflection log is empty.{RESET}'

    lines = [
        f'  {BOLD}Reflection log{RESET} {DIM}(last {min(limit, len(entries))} '
        f'of {len(entries)}){RESET}',
        f'  {DIM}{"─" * 56}{RESET}',
    ]
    for entry in entries[-limit:]:
        result = entry.get("result", {})
        lines.append(f'  {DIM}{_ago(entry.get("at", 0))} · mode={entry.get("mode")} '
                     f'· {entry.get("signals", 0)} correction signals{RESET}')
        for key, label in (("user_preferences", "preference"),
                           ("corrections", "lesson"),
                           ("project_notes", "project"),
                           ("skill_candidates", "skill")):
            for item in (result.get(key) or []):
                text = (item.get("fact") or item.get("lesson")
                        or item.get("name") or "")
                if text:
                    lines.append(f'      {DIM}[{label}]{RESET} {str(text)[:90]}')
        if not any(result.get(k) for k in
                   ("user_preferences", "corrections", "project_notes",
                    "skill_candidates")):
            lines.append(f'      {DIM}(learned nothing — the correct answer '
                         f'for most sessions){RESET}')
    mode_now = os.environ.get("TOMAS_REFLECT", "shadow")
    if mode_now == "shadow":
        lines.append('')
        lines.append(f'  {DIM}Shadow mode: nothing above was written to the '
                     f'store. Set TOMAS_REFLECT=active to enable.{RESET}')
    return '\n'.join(lines)

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

MAX_TOOL_CALLS_PER_TURN = int(os.environ.get("TOMAS_MAX_TOOL_CALLS", "40"))
_streaming_disabled = False  # set True if provider doesn't support streaming

# The loop itself now lives in core/loop.py. These aliases keep older call
# sites working while this shim is still in place.
_is_retryable_error = core_loop.is_retryable_error
_is_client_error = core_loop.is_client_error

# Session-scoped tool approvals. Answering "always" records the exact call the
# user saw; it no longer rewrites RISK_LEVELS for the rest of the process.
APPROVALS = ApprovalStore()


def _tool_origin(name: str) -> str:
    """Human-readable provenance for a tool name."""
    if name in HANDLERS:
        return "built-in"
    if mcp_manager:
        srv = mcp_manager.get_server_for_tool(MCP_TOOL_NAME_MAP.get(name, name))
        if srv:
            return f"MCP: {srv}"
    return "built-in"


_EXIT_CODE_RE = re.compile(r'^\[exit (-?\d+) ')


def _record_tool_call(name: str, args: dict, preview: str,
                      duration_ms: int = 0, ok: bool = True) -> None:
    try:
        self_improve.record_tool_call(name, args, preview)
    except Exception:
        pass
    entry = {
        "turn": len(_turn_timings) + 1,
        "tool": name,
        "duration_sec": round(duration_ms / 1000, 3),
        "ok": ok,
    }
    # run_command now leads with its exit code, so the log can record the real
    # process status rather than "the tool returned a string".
    m = _EXIT_CODE_RE.match(preview or "")
    if m:
        entry["exit"] = int(m.group(1))
    elif not ok or (preview or "").startswith("Error:"):
        entry["exit"] = 1
        entry["error"] = (preview or "")[:200]
    else:
        entry["exit"] = 0
    if entry["exit"] != 0 and "error" not in entry:
        entry["error"] = (preview or "")[:200]
    # Arguments and results are already in `messages`; duplicating them here
    # is how a 6-turn session file reached 190 KB.
    _tool_log.append(entry)


def _last_user_text(messages: list) -> str:
    """The most recent user message as plain text, for tool selection."""
    for m in reversed(messages or []):
        if m.get("role") != "user":
            continue
        content = m.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            # Tool-result turns carry no user intent; keep looking back.
            texts = [b.get("text", "") for b in content
                     if isinstance(b, dict) and b.get("type") == "text"]
            if texts:
                return "\n".join(texts)
    return ""


def tools_for_turn(messages: list) -> tuple[list[dict], list[dict]]:
    """Pick this turn's tool payload.

    Re-selected per turn rather than per session: a user who starts with file
    edits and moves to browser automation should get browser tools when they
    ask for them, not whichever server connected first at startup.
    """
    pool = ALL_TOOLS or COMBINED_TOOLS or TOOLS
    context = _last_user_text(messages)
    try:
        context = f"{context}\n{self_improve.get_session_analysis().get('purpose', '')}"
    except Exception:
        pass
    return select_tools(pool, context, tool_ceiling())


TEXT_TOOL_PROTOCOL = """

## Tool use (text protocol)

This endpoint does not support native tool calling, so tools are invoked by
writing a fenced block. To call one, emit exactly:

```tool_call
{"name": "<tool name>", "input": {...}}
```

Emit one block per call and then stop; the result is returned to you in the
next message. Available tools:
"""

_TEXT_TOOL_RE = re.compile(r"```tool_call\s*\n(.*?)\n?```", re.S)


def describe_tools_as_text(tools: list[dict]) -> str:
    """Render the tool list into the system prompt for the text protocol."""
    lines = [TEXT_TOOL_PROTOCOL]
    for t in tools:
        schema = t.get("input_schema", {}) or {}
        params = ", ".join(schema.get("properties", {}).keys()) or "none"
        lines.append(f"- `{t['name']}`({params}) — {t.get('description', '')[:160]}")
    return "\n".join(lines)


def parse_text_tool_calls(text: str) -> list[dict]:
    """Extract tool calls a model wrote as text. Malformed blocks are skipped."""
    calls = []
    for block in _TEXT_TOOL_RE.findall(text or ""):
        try:
            payload = json.loads(block.strip())
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("name"):
            calls.append({"name": payload["name"],
                          "input": payload.get("input") or {}})
    return calls


def build_state(system_prompt: str, messages: list, responder) -> AgentState:
    """Assemble the turn context the core needs out of this module's globals."""
    selected, withheld = tools_for_turn(messages)
    if withheld:
        system_prompt = system_prompt + withheld_tools_notice(withheld)
    caps = _active_capabilities()

    # ── Degradations. Each costs a feature; none costs the session. ──
    if not caps.tool_use:
        # No native tool calling: describe the tools and parse a text
        # protocol out of the reply instead (see agent_loop).
        system_prompt = system_prompt + describe_tools_as_text(selected)
    if not caps.system_prompt:
        # No system role: prepend it as the first user message.
        messages = ([{"role": "user", "content": system_prompt},
                     {"role": "assistant", "content": "Understood."}]
                    + list(messages))
        system_prompt = ""

    return AgentState(
        system_prompt=system_prompt,
        messages=messages,
        get_client=_get_client,
        get_model=_get_model,
        tools=selected if caps.tool_use else [],
        max_tokens=min(MAX_TOKENS, caps.max_output_tokens) or MAX_TOKENS,
        execute_tool=execute_tool,
        risk_of=risk_for,
        origin_of=_tool_origin,
        responder=responder,
        approvals=APPROVALS,
        auto_approve_low=AUTO_APPROVE_LOW,
        yolo=YOLO_MODE,
        tool_budget=MAX_TOOL_CALLS_PER_TURN,
        streaming_enabled=(not _streaming_disabled) and caps.streaming,
        on_tool_call=_record_tool_call,
    )


TEXT_PROTOCOL_MAX_ROUNDS = 6


def _run_text_protocol(state, adapter, reply: str, messages: list) -> str:
    """Drive tool calls for providers with no native tool support.

    The model writes ```tool_call blocks; we execute them, feed the results
    back, and let it continue. Slower and less reliable than native tool use
    — which is the point: the missing capability costs a feature, not the
    session.
    """
    for _ in range(TEXT_PROTOCOL_MAX_ROUNDS):
        calls = parse_text_tool_calls(reply)
        if not calls:
            return reply
        results = []
        for call in calls:
            name, params = call["name"], call["input"]
            if not request_permission(name, params):
                results.append(
                    f"[{name}] Error: the user denied this tool call. Retrying "
                    f"the same call will be denied again — do not re-issue it.")
                continue
            t0 = time.perf_counter()
            try:
                out = execute_tool(name, params)
                ok = True
            except Exception as e:
                out, ok = f"Error: tool raised {type(e).__name__}: {e}", False
            _record_tool_call(name, params, str(out)[:200],
                              int((time.perf_counter() - t0) * 1000), ok)
            results.append(f"[{name}]\n{str(out)[:state.max_result_chars]}")
        messages.append({"role": "assistant", "content": reply})
        messages.append({"role": "user",
                         "content": "Tool results:\n\n" + "\n\n".join(results)})
        state.messages = messages
        reply = adapter.run(state)
    return reply


def agent_loop(system_prompt: str, messages: list) -> str:
    """Shim — drives core.loop.run_turn through the terminal adapter.

    Kept so the REPL and agent_cli.py keep working unchanged. New front ends
    should drive run_turn directly with their own adapter instead.
    """
    global _streaming_disabled

    interactive = bool(getattr(sys.stdin, "isatty", lambda: False)())
    adapter = TerminalAdapter(interactive=interactive)
    state = build_state(system_prompt, messages, adapter)

    turn_index = len(_turn_timings) + 1
    t0 = time.perf_counter()
    error: Optional[BaseException] = None
    reply = ""
    try:
        # messages already carries the user's turn, so no user_message here.
        reply = adapter.run(state)
        if not _active_capabilities().tool_use:
            reply = _run_text_protocol(state, adapter, reply, messages)
    except BaseException as e:
        error = e
        raise
    finally:
        _turn_timings.append(time.perf_counter() - t0)
        # Propagate what the turn learned back into module state.
        was_streaming = not _streaming_disabled
        _streaming_disabled = not state.streaming_enabled
        if was_streaming and _streaming_disabled:
            # The core already fell back. Persist that only when the failure
            # says something about the provider: a 429 or a 5xx means the
            # endpoint was busy, not that it cannot stream, and writing it
            # down would disable streaming for good over a transient blip.
            if state.streaming_error_retryable:
                _streaming_disabled = False   # retry streaming next turn
            else:
                degrade_capability("streaming", state.streaming_error or "stream failed")
        _last_turn_usage["input"] = state.usage.get("input", 0)
        _last_turn_usage["output"] = state.usage.get("output", 0)
        _session_tokens["input"] += state.usage.get("total_input", 0)
        _session_tokens["output"] += state.usage.get("total_output", 0)
        _session_tokens["calls"] += state.usage.get("calls", 0)
        # A turn that produced nothing is recorded as such. Silence here is
        # what let a session with eight prompts and zero replies be saved,
        # and then be reported as eight turns of completed work.
        if error is not None or not (reply or "").strip():
            # Prefer the reason the core recorded — "empty_reply" with no
            # explanation is the unreadable record P6-11 exists to prevent.
            core_error = getattr(state, "last_error", None)
            if error is not None:
                reason, detail = type(error).__name__, str(error)
            elif core_error:
                reason, detail = "turn_error", core_error
            else:
                reason, detail = "empty_reply", ""
            _failed_turns.append({
                "turn": turn_index,
                "reason": reason,
                "error": detail[:300],
            })
    return reply


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
    "mcp-prompt":   {"desc": "MCP prompt templates: /mcp-prompt [name]", "icon": "◈"},
    "mcp-resources": {"desc": "List resources published by MCP servers", "icon": "◈"},
    "provider":     {"desc": "Show provider capabilities; /provider probe to re-measure", "icon": "◎"},
    "pdf-report":   {"desc": "Generate AI news PDF report",      "icon": "📄"},
    "zen":          {"desc": "OpenCode Zen proxy status",         "icon": "◉"},
    "self-improve": {"desc": "What the agent has learned",        "icon": "🧠"},
    "si":           {"desc": "Alias for /self-improve",           "icon": "🧠"},
    "forget":       {"desc": "Forget a learned fact: /forget <id>", "icon": "🗑"},
    "private":      {"desc": "Toggle incognito (learn nothing)",  "icon": "🕶"},
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
        # A cleared conversation is a new session; its accounting starts over.
        reset_session_state()
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
            # Strict mode clears every approval granted via "always"
            APPROVALS.clear()
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

    if cmd == "provider":
        import provider_manager
        provider = provider_manager.get_active()
        if provider is None:
            return f'  {DIM}No provider is configured. Use{RESET} {CYAN}agent_cli.py{RESET}{DIM} to add one.{RESET}'
        sub = parts[1].lower() if len(parts) > 1 else ""
        if sub == "probe":
            # A capability learned by degradation is sticky by design, so
            # there has to be a way to re-measure — a transient failure must
            # not disable streaming forever.
            caps = provider_manager.probe_and_persist(provider)
            head = f'  {GREEN}✓{RESET} Re-probed {CYAN}{provider.name}{RESET}'
        else:
            caps = provider.capabilities
            head = f'  {BOLD}{provider.name}{RESET} {DIM}({provider.type}){RESET}'
        when = ('never — using optimistic defaults' if not caps.probed
                else time.strftime('%Y-%m-%d %H:%M', time.localtime(caps.probed_at)))
        yn = lambda v: f'{GREEN}yes{RESET}' if v else f'{YELLOW}no{RESET}'
        return '\n'.join([
            head,
            f'  {DIM}{"─" * 46}{RESET}',
            f'  {DIM}Model:{RESET}          {provider.model or "(unset)"}',
            f'  {DIM}Endpoint:{RESET}       {provider.base_url or "(from env)"}',
            f'  {DIM}Probed:{RESET}         {when}',
            '',
            f'  {DIM}Streaming:{RESET}      {yn(caps.streaming)}',
            f'  {DIM}Tool use:{RESET}       {yn(caps.tool_use)}',
            f'  {DIM}System prompt:{RESET}  {yn(caps.system_prompt)}',
            f'  {DIM}Context window:{RESET} {caps.context_window:,}',
            f'  {DIM}Tool ceiling:{RESET}   {caps.max_tools}',
            '',
            f'  {DIM}Run{RESET} {CYAN}/provider probe{RESET} {DIM}to re-measure against the endpoint.{RESET}',
        ])

    if cmd in ("mcp-prompt", "mcp-prompts"):
        # Server-provided prompt templates, surfaced the same way skills are.
        if mcp_manager is None:
            return f'  {DIM}No MCP servers are connected.{RESET}'
        prompts = mcp_manager.list_prompts()
        name = parts[1] if len(parts) > 1 else ""
        if not name:
            if not prompts:
                return f'  {DIM}No connected MCP server provides prompts.{RESET}'
            lines = [f'  {BOLD}MCP Prompts{RESET}', f'  {DIM}{"─" * 46}{RESET}']
            for p in prompts:
                lines.append(f'    {CYAN}{p.get("name", "?")}{RESET} '
                             f'{DIM}[{p["server"]}] {(p.get("description") or "")[:70]}{RESET}')
            lines.append('')
            lines.append(f'  {DIM}Run one with{RESET} {CYAN}/mcp-prompt <name>{RESET}')
            return '\n'.join(lines)
        rendered = mcp_manager.get_prompt(name)
        if rendered.startswith("Error:"):
            return f'  {RED}{rendered}{RESET}'
        return rendered

    if cmd == "mcp-resources":
        return handle_read_mcp_resource({})

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
            reset_session_state()
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

        # ── The learning store (Phase 3) ──
        # The requirement is that the user does not see the machinery. That
        # must not mean the user cannot see it: an agent that silently
        # accumulates wrong beliefs with no way to inspect them is worse than
        # one that does not learn.
        if sub in ("", "facts", "learned"):
            return _render_learned_facts()

        if sub in ("reflect", "reflection"):
            return _render_reflection_log()

        if sub in ("analyze", "scan"):
            self_improve.update_session_analysis()
            return f'  {GREEN}✓{RESET} Session analysis updated.'

        # The keyword-counting generator behind `skills`, `tips`, and
        # `patterns` was deleted in Phase 6. Point at what replaced it rather
        # than leaving three subcommands that silently show nothing.
        if sub in ("skills", "tips", "patterns"):
            return (
                f'  {DIM}The pattern/tip generator was removed — it counted keywords and\n'
                f'  filled in templates, and nothing consumed its output.{RESET}\n'
                f'  {CYAN}/self-improve facts{RESET}      — what the agent has actually learned\n'
                f'  {CYAN}/self-improve reflect{RESET}    — the reflection log\n'
                f'  {CYAN}/skills{RESET}                  — installed skills'
            )

        # Default: show status
        return self_improve.get_self_improve_status()

    if cmd == "forget":
        fact_id = parts[1].strip() if len(parts) > 1 else ""
        if not fact_id:
            return (f'  {YELLOW}Usage:{RESET} {CYAN}/forget <id>{RESET}\n'
                    f'  {DIM}Ids are shown by /si.{RESET}')
        try:
            removed = learning.forget(fact_id)
        except Exception as e:
            return f'  {RED}✗{RESET} Could not forget: {e}'
        if not removed:
            return f'  {DIM}No fact with id {fact_id}.{RESET}'
        return (f'  {GREEN}✓{RESET} Forgotten: {removed.get("fact", "")[:80]}\n'
                f'  {DIM}Tombstoned — reflection will not re-learn it.{RESET}')

    if cmd == "private":
        learning.set_enabled(not learning.is_enabled())
        if learning.is_enabled():
            return f'  {GREEN}✓{RESET} Learning re-enabled for this session.'
        return (f'  {GREEN}✓{RESET} Incognito: nothing from this session will be '
                f'reflected on or stored.')

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
            # Reset non-built-in risk overrides and clear "always" approvals
            APPROVALS.clear()
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
        """Clear the input line and re-draw prompt + buffer.

        Measured in display columns, not `len()`: `\\r\\033[K` only clears the
        line the cursor is on, so a buffer wide enough to wrap leaves debris
        behind. Long input scrolls horizontally instead, keeping the tail —
        the part being typed — visible.
        """
        from text_display import display_width, term_width
        prompt = _build_prompt()
        body = _repr()
        room = term_width() - display_width(prompt) - 1
        if room > 10 and display_width(body) > room:
            kept, used = [], 0
            for ch_ in reversed(body):
                from text_display import char_width
                w = char_width(ch_)
                if used + w > room - 1:
                    break
                kept.append(ch_)
                used += w
            body = '…' + ''.join(reversed(kept))
        sys.stdout.write('\r\033[K')
        sys.stdout.write(prompt)
        sys.stdout.write(body)
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
            # getwch returns a decoded str, so a Ukrainian, Russian,
            # accented-Latin or CJK keystroke arrives as one character
            # instead of a high byte that the old ASCII test discarded.
            ch = msvcrt.getwch()

            # ── Enter ──────────────────────────────────────────────────────
            if ch == '\r':
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
            if ch == '\x03':
                _hide()
                sys.stdout.write('\n')
                sys.stdout.flush()
                raise KeyboardInterrupt

            # ── Arrow / function keys ─────────────────────────────────────
            if ch in ('\xe0', '\x00'):
                # One prefix branch for both extended-key families: getwch
                # reports '\xe0' for arrows and '\x00' for function keys, and
                # the second call gives the key itself.
                ext = msvcrt.getwch()

                # F5–F8 — quick mode switch
                if ext in ('\x3f', '\x40', '\x41', '\x42'):
                    if ext == '\x3f':      # F5 — toggle auto/default
                        _set_mode("default" if AUTO_APPROVE_LOW else "auto")
                    elif ext == '\x40':    # F6 — default mode
                        _set_mode("default")
                    elif ext == '\x41':    # F7 — strict mode
                        _set_mode("strict")
                    else:                  # F8 — YOLO mode
                        _set_mode("yolo")
                    _refresh()
                    if _is_slash():
                        _show()
                    else:
                        _hide()
                    continue

                if showing and _is_slash():
                    matches = _get_matches()
                    if not matches:
                        continue
                    if ext == 'H':  # ↑ Up arrow — navigate suggestions
                        selected = 0 if selected is None else max(0, selected - 1)
                        _show()
                    elif ext == 'P':  # ↓ Down arrow — navigate suggestions
                        selected = 0 if selected is None else min(len(matches) - 1, selected + 1)
                        _show()
                elif ext == 'H':  # ↑ Up arrow — history recall
                    if _input_history:
                        _hide()
                        if _history_index > 0:
                            _history_index -= 1
                        buffer = list(_input_history[_history_index])
                        _refresh()
                elif ext == 'P':  # ↓ Down arrow — history forward
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

            # ── Tab — auto-complete slash commands OR cycle risk mode ──────
            if ch == '\t':
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
                else:
                    # Tab outside slash command → cycle mode: auto → default → yolo → auto
                    if YOLO_MODE:
                        _set_mode("auto")
                    elif AUTO_APPROVE_LOW:
                        _set_mode("default")
                    else:
                        _set_mode("yolo")
                    _refresh()
                    _hide()
                continue

            # ── Backspace ─────────────────────────────────────────────────
            if ch in ('\x08', '\x7f'):
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
            if ch == '\x1b':
                if buffer:
                    buffer.clear()
                    selected = None
                    _hide()
                    _refresh()
                continue

            # ── Printable — any script ────────────────────────────────────
            # `ch.isprintable()` is true for 'a', 'П', 'ї', 'é', '日' and an
            # emoji, and false for control characters. The old test was
            # `32 <= ch[0] < 127` against a *byte*, which silently discarded
            # every Cyrillic keystroke — you could not type Ukrainian or
            # Russian into the prompt at all.
            if ch.isprintable():
                buffer.append(ch)
                selected = None
                _refresh()
                if _is_slash():
                    _show()
                else:
                    _hide()
                continue

            # ── Everything else (remaining control characters) ────────────
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
                    # ensure_ascii=False: history used to replay Cyrillic
                    # arguments as \uXXXX escapes, cut mid-escape at 80.
                    from text_display import shorten
                    inp = shorten(
                        json.dumps(block.get("input", {}), ensure_ascii=False), 80)
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

    # ── This session's accounting starts here, not at import time ──
    reset_session_state()

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

        # One status line. Six red error lines before the user has typed
        # anything were mostly "this optional server has no credentials",
        # which is a fact, not a failure — and not one they can act on here.
        needs_auth, broken = _classify_mcp_failures(mcp_manager.failed_servers)
        if connected_servers:
            line = (f'  {GREEN}✓{RESET}  {BOLD}MCP:{RESET} '
                    f'{len(connected_servers)} connected {DIM}·{RESET} {total_tools} tools')
        else:
            line = f'  {DIM}MCP:{RESET} none connected'
        extras = []
        if disabled:
            extras.append(f'{len(disabled)} disabled')
        if needs_auth:
            extras.append(f'{len(needs_auth)} need credentials')
        if broken:
            extras.append(f'{len(broken)} unavailable')
        if extras:
            line += f' {DIM}·{RESET} {DIM}' + f'{DIM} · {RESET}{DIM}'.join(extras) + RESET
        print(line)
        if needs_auth or broken:
            names = sorted(needs_auth) + sorted(broken)
            shown = ', '.join(names[:4]) + ('…' if len(names) > 4 else '')
            print(f'     {DIM}{shown} — /mcp for details{RESET}')

        # Resolve names once. Which tools are *sent* is decided per turn by
        # select_tools(), against what the user is actually asking for.
        global TOOL_TOKENS, MCP_TOOL_NAME_MAP, ALL_TOOLS
        if mcp_manager.tools:
            mcp_tools, MCP_TOOL_NAME_MAP, renames = resolve_mcp_tool_conflicts(mcp_manager.tools)
            if renames:
                print(f'  {YELLOW}⚠{RESET}  Renamed {renames} MCP tool(s) to avoid built-in name conflicts')
            ALL_TOOLS = TOOLS + mcp_tools
            budget = tool_ceiling()
            COMBINED_TOOLS, dropped = apply_tool_cap(mcp_tools, max_allowed=budget)
            if dropped:
                print(f'  {CYAN}◈{RESET}  {len(mcp_tools)} MCP tools available, '
                      f'{budget} fit per turn {DIM}(selected by relevance to each '
                      f'message; the model is told what is held back){RESET}')
        else:
            ALL_TOOLS = list(TOOLS)
            COMBINED_TOOLS = TOOLS
            MCP_TOOL_NAME_MAP = {}
        TOOL_TOKENS = sum(len(json.dumps(t)) for t in COMBINED_TOOLS) // 6
    except Exception as e:
        print(f'  {RED}⚠{RESET}  MCP initialization failed: {e}')
        mcp_manager = MCPManager()
        ALL_TOOLS = list(TOOLS)
        COMBINED_TOOLS = TOOLS
        MCP_TOOL_NAME_MAP = {}
        TOOL_TOKENS = sum(len(json.dumps(t)) for t in COMBINED_TOOLS) // 6

    # ── Initialize self-improving system ──
    self_improve.init()
    init_learning()
    # One-shot: annotate sessions saved before completeness was recorded, so
    # an abandoned run is distinguishable from a finished one.
    try:
        from session_manager import backfill_completeness
        marked = backfill_completeness()
        if marked:
            print(f'  {YELLOW}⚠{RESET}  Marked {len(marked)} incomplete session(s) '
                  f'{DIM}(saved with turns that produced no reply){RESET}')
    except Exception:
        pass
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
            # Retrieve learned knowledge against what the user just asked.
            system_prompt = build_system_prompt(user_input)  # re-inject every turn
            # Re-check compaction with the actual system prompt size
            messages = maybe_compact(messages, system_prompt)
            result = agent_loop(system_prompt, messages)
            # agent_loop owns both printing and transcript recording: the
            # streaming path prints tokens as they arrive, the non-streaming
            # path prints on return. Only error strings (which are returned
            # without being printed or recorded) need handling here.
            if result and result.startswith("I'm sorry"):
                print(f'  {MAGENTA}{BOLD}▌ TOMAS{RESET}')
                print(f'  {result}')
            # ── Token usage info ──
            t = _last_turn_usage
            s = _session_tokens
            if s["calls"] > 0:
                pct = (t["input"] + t["output"]) / CONTEXT_WINDOW * 100
                print(f'  {DIM}┄  {t["input"]:,} in  {t["output"]:,} out  ·  total: {s["input"]:,} in  {s["output"]:,} out  ·  {pct:.1f}% of {CONTEXT_WINDOW:,} ctx{RESET}')
            # ── Self-improvement analysis, after the reply so it never adds latency ──
            try:
                self_improve.maybe_analyze_after_turn()
            except Exception:
                pass
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
            reflect_on_session_end(messages)
        # ── Clean up MCP connections ──
        if mcp_manager:
            mcp_manager.disconnect_all()
    return 0

if __name__ == "__main__":
    sys.exit(main())