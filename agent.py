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
  AGENT_MODEL         - optional, model name (e.g. claude-sonnet-5)
  AGENT_AUTO_APPROVE  - optional, "1" to auto-approve low-risk tools
"""

from __future__ import annotations

import os
import re
import sys
import time
import json
import base64
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

# Playwright for browser-based fetching (JavaScript rendering).
# Whether it is installed is answered from the import system's metadata
# instead of by importing it: the package costs ~105 ms to load, and the CLI
# menus only ever need the boolean. The tools that drive a browser import it
# themselves, at the point they are about to use it.
import importlib.util

PLAYWRIGHT_AVAILABLE = importlib.util.find_spec("playwright") is not None
async_playwright = None  # bound on demand by the browser tools

# Load variables from .env into os.environ if present.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # python-dotenv not installed — rely on real env vars instead.
    pass

# NOTE: `anthropic` is deliberately *not* imported here. It is the single
# most expensive import in the project — 1.13 s of the CLI's 1.66 s cold
# start — and the menus never touch it: they import TOOLS, RISK_LEVELS and
# build_system_prompt from this module and go straight to drawing. It is
# imported where it is used, in `_get_client` (and already lazily in
# `core/loop.py`), so only a session that actually talks to a model pays it.

# MCP and skills support
from mcp_manager import MCPManager
from skills_manager import (build_skills_section, build_triggered_skills,
                            discover_skills, cmd_skill_list, cmd_skill_run)

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
import instructions_manager
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

# Where throwaway helper scripts belong. The sandbox allows writes only under
# PROJECT_DIR, so "put it in a temp directory outside the project" — which
# BASE_PROMPT used to say — asked for something that could not work: the model
# tried ~/.tomas/tmp, was refused, tried %TEMP%, was refused again, and only
# then wrote into the repo root it was being told to keep clean. One named
# location inside the sandbox resolves the contradiction.
SCRATCH_DIR = PROJECT_DIR / "_scratch"
def _env_int(name: str, default: int, minimum: int = 1) -> int:
    """An int from the environment, ignoring anything unusable."""
    try:
        value = int(os.environ.get(name, "").strip())
    except (TypeError, ValueError):
        return default
    return value if value >= minimum else default


# Output-token ceiling per turn. Overridable because the right value depends on
# the model: a reasoning model spends most of this budget thinking before it
# writes anything, so a limit that is generous for a chat model truncates it to
# nothing. Raise it when a long document or a reasoning model gets cut off.
MAX_TOKENS = _env_int("AGENT_MAX_TOKENS", 8192, minimum=256)
COMPACTION_THRESHOLD = 0.75  # compact when total budget (msg_tok + TOOL_TOKENS + MAX_TOKENS) exceeds this fraction of CONTEXT_WINDOW
# Absolute ceiling, whatever the window. Past roughly this much conversation the
# cost of re-reading it every turn outweighs what the extra history is worth,
# and the model has usually stopped referring to the early part anyway.
COMPACTION_CEILING = _env_int("AGENT_COMPACT_AT", 120_000, minimum=8_000)
DEFAULT_CONTEXT_WINDOW = 200_000  # fallback if API doesn't report context window (standard Claude tier)
CONTEXT_WINDOW = DEFAULT_CONTEXT_WINDOW  # will be updated dynamically at startup

# ── Characters per token, by what is being counted ──
# These decide when compaction fires, so being wrong in either direction costs
# something real: too low and a long session overflows the window before the
# summariser runs, too high and it compacts history that still fitted.
#
# Prose runs about 4 chars/token. JSON runs far denser — punctuation, quotes
# and short keys all tokenise separately — measured at ~3.5 across the 64 tools
# of three real MCP servers (32,226 chars, ~9,200 tokens).
#
# Both were wrong before: messages used //3 (over-counting prose by ~30%) while
# tool schemas used //6 (under-counting JSON by ~40%, or ~5,400 tokens at a
# 128-tool ceiling). The two errors pulled in opposite directions, so the total
# looked plausible while neither half was.
CHARS_PER_TOKEN_PROSE = 4
CHARS_PER_TOKEN_JSON = 3.5

# Known model context windows (fallback when API is not reachable)
MODEL_CONTEXT_MAP: dict[str, int] = {
    # Zen models
    "deepseek-v4-flash-free": 1_000_000,
    "big-pickle": 128_000,
    # Anthropic
    "claude-fable-5": 200_000,
    "claude-opus-5": 200_000,
    "claude-sonnet-5": 200_000,
    "claude-haiku-4-5-20251001": 200_000,
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
# Bypass mode: yolo, plus the turn is never stopped to ask whether to keep
# going. The two are separate questions and yolo only ever answered the first.
# Observed: a session that approved every tool still halted at the 40-call
# checkpoint and was saved incomplete, mid-task, after 56 calls. Bounded by
# AgentState.max_auto_continuations so "do not ask me" cannot become "bill me
# without limit".
BYPASS_MODE = False

#: Mode order used by Tab cycling and by every place that names the modes.
#: One list, so a new mode cannot be added to the switcher and forgotten in the
#: badge, the status line and the help.
MODE_CYCLE = ("auto", "default", "yolo", "bypass")
ALL_MODES = ("auto", "default", "strict", "yolo", "bypass")


def current_mode_name() -> str:
    """The single answer to "what mode am I in?".

    Derived from the flags rather than stored beside them: several call sites
    used to recompute this inline, so a new mode has to be taught to each of
    them or the banner and the status line start disagreeing.
    """
    if BYPASS_MODE:
        return "bypass"
    if YOLO_MODE:
        return "yolo"
    return "auto" if AUTO_APPROVE_LOW else "default"


def mode_color(mode: Optional[str] = None) -> str:
    """The colour a mode is always shown in. Red means "nothing will stop"."""
    mode = mode or current_mode_name()
    if mode in ("yolo", "bypass"):
        return RED
    if mode == "auto":
        return GREEN
    return YELLOW


#: One line naming every key that switches modes, so the four places that
#: print it cannot drift apart when a mode is added.
MODE_KEYS_HINT = ("F5 auto · F6 default · F7 strict · F8 yolo · F9 bypass "
                  "· Tab cycles")


def set_mode(name: str) -> None:
    """Apply a mode by name. The one place the flags are written together."""
    global AUTO_APPROVE_LOW, YOLO_MODE, BYPASS_MODE
    YOLO_MODE = False
    BYPASS_MODE = False
    if name == "auto":
        AUTO_APPROVE_LOW = True
    elif name == "default":
        AUTO_APPROVE_LOW = False
    elif name == "strict":
        AUTO_APPROVE_LOW = False
        APPROVALS.clear()
        for key in list(RISK_LEVELS.keys()):
            if key not in BUILTIN_TOOL_NAMES:
                RISK_LEVELS[key] = "high"
    elif name == "yolo":
        AUTO_APPROVE_LOW = True
        YOLO_MODE = True
    elif name == "bypass":
        AUTO_APPROVE_LOW = True
        YOLO_MODE = True
        BYPASS_MODE = True

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
    _LAST_TOOL_SELECTION.clear()   # a new session starts with no sticky set
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
        import anthropic  # deferred: see the note at the top of this module
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
                if check_status(6446, use_cache=False):  # verifies our own start
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


def _server_of(name: str) -> Optional[str]:
    """Which MCP server owns this tool. None for built-ins or when no manager
    is connected — callers must treat None as "ungrouped", not as an error."""
    if mcp_manager:
        try:
            return mcp_manager.get_server_for_tool(MCP_TOOL_NAME_MAP.get(name, name))
        except Exception:
            return None
    return None


def tool_simplicity(name: str) -> float:
    """How fundamental a tool name looks, from its segment count alone.

    Within one server the building blocks are the plainly-named tools —
    `add_heading`, `add_table` — while the specialised variants pile on
    qualifiers: `set_table_cell_shading`, `apply_table_alternating_rows`,
    `delete_footnote_robust`. A task that engages a server needs the former;
    it reaches for the latter by name, when it already knows it wants them.
    Segment count is the only signal here that does not require knowing what
    any particular server does.
    """
    segments = [s for s in re.split(r"[_\-]+", name or "") if s]
    return 1.0 / max(1, len(segments) - 1)


# How many tools the turn's primary server is guaranteed, before everything
# else competes on score. Sized to cover a working set (create + the handful
# of verbs that put content into what was created), not a whole server.
SERVER_CORE_QUOTA = 8

# A quota slot is only worth spending on a tool the turn plausibly needs: one
# the request actually scored, or one plainly enough named to be a building
# block (at most three segments). Without this the quota keeps filling from
# whatever is left — `set_table_cell_alternating_shading` and friends — after
# the real working set runs out, and spends budget other servers could use.
CORE_NAME_SIMPLICITY = 0.5


# The MCP tools sent last turn. Used only as a tie-breaker, so it can never
# override relevance — see `rank` inside select_tools.
_LAST_TOOL_SELECTION: set[str] = set()


def _remember_tool_selection(names) -> None:
    _LAST_TOOL_SELECTION.clear()
    _LAST_TOOL_SELECTION.update(names)


def select_tools(all_tools: list[dict], context: str, budget: int,
                 server_of: Optional[Callable[[str], Optional[str]]] = None,
                 ) -> tuple[list[dict], list[dict]]:
    """Fit the tool list to the budget by relevance to the current turn.

    Built-ins are always kept — they are what the agent is. The rest of the
    budget goes to the MCP tools most relevant to what the user is actually
    doing, rather than to whichever server connected first, which is what
    list-order truncation really selected on.

    Relevance alone is not enough, because it scores every tool in isolation.
    Asked to write a methodichka as .docx and .pdf, it handed the model
    `create_document` (3.5) and `convert_to_pdf` (3.0) while every tool that
    puts content *into* a document — `add_heading`, `add_paragraph`,
    `add_table` — scored exactly 0.0 and was withheld: nothing in those names
    or descriptions repeats the user's words. The model could open a document
    and convert one, but could not fill one, so it fell back to `write_file`
    and produced a .docx that was not a real .docx at all.

    So the server that best matches the turn is picked first, by summed
    relevance across its tools, and is guaranteed a `SERVER_CORE_QUOTA`
    working set — its highest-scoring tools, then its plainest-named ones.
    The rest of the budget is filled globally by score, as before.

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

    resolve = server_of or _server_of
    scores = [tool_relevance(t, query_keywords) for t in mcp]
    servers = [resolve(t.get("name", "")) for t in mcp]

    # Reuse last turn's set verbatim when it still covers this turn.
    #
    # Prefix caching is binary: one changed byte anywhere in the tool block and
    # the whole cached prefix is gone, so a set that is *mostly* stable is
    # worth almost nothing. Holding it byte-identical is what turns the system
    # prompt, the tools and the entire history into a cache hit.
    #
    # "Still covers this turn" means the single most relevant tool for the
    # message is already loaded. If the user pivots to a capability that is not
    # in hand, that tool outranks everything present and the set is re-picked —
    # which is the case adaptivity actually exists for.
    if _LAST_TOOL_SELECTION and any(s > 0 for s in scores):
        best = max(range(len(mcp)), key=lambda i: (scores[i], -i))
        if mcp[best].get("name", "") in _LAST_TOOL_SELECTION:
            held = [t for t in mcp if t.get("name", "") in _LAST_TOOL_SELECTION]
            if len(held) == min(remaining, len(_LAST_TOOL_SELECTION)):
                kept = sorted(held, key=lambda t: t.get("name", ""))
                names = {t.get("name", "") for t in kept}
                return (builtins + kept,
                        [t for t in mcp if t.get("name", "") not in names])

    # Rank servers by summed relevance, not by their single best tool: one
    # incidental keyword hit should not outrank a server the whole request is
    # about. `memory` matched "create" twice; `word-docs` matched across
    # create/convert/copy and wins on the total.
    server_totals: dict[str, float] = {}
    for srv, score in zip(servers, scores):
        if srv is not None:
            server_totals[srv] = server_totals.get(srv, 0.0) + score

    # Sort key shared by both phases: score, then whether the tool was already
    # loaded last turn, then plainest name, then the original order so ties
    # never depend on dict iteration.
    #
    # The stickiness term is what makes this affordable to send. Tools are
    # serialised *before* the messages, and every provider that caches a prompt
    # — DeepSeek's automatic caching, Anthropic's cache_control, OpenAI's
    # prefix cache — matches on an exact byte prefix. Selecting purely by score
    # swapped 38 of 64 tool slots between consecutive turns, so the cached
    # prefix was invalidated at the first block and the system prompt, the
    # tools and the whole conversation were re-processed every single message.
    #
    # It is only a tie-breaker, so relevance is unaffected: a tool the turn
    # actually scores still beats a stale one. What it stabilises is the long
    # tail of zero-scoring filler, which is where all the churn was.
    sticky = _LAST_TOOL_SELECTION

    def rank(i: int) -> tuple:
        name = mcp[i].get("name", "")
        return (-scores[i], 0 if name in sticky else 1,
                -tool_simplicity(name), i)

    taken: set[int] = set()
    primary = max(server_totals, key=lambda s: (server_totals[s], s), default=None)
    if primary is not None and server_totals[primary] > 0:
        core = sorted(
            (i for i, s in enumerate(servers)
             if s == primary
             and (scores[i] > 0
                  or tool_simplicity(mcp[i].get("name", "")) >= CORE_NAME_SIMPLICITY)),
            key=rank)
        taken.update(core[:min(SERVER_CORE_QUOTA, remaining)])

    for i in sorted(range(len(mcp)), key=rank):
        if len(taken) >= remaining:
            break
        taken.add(i)

    # Serialise by name, not by score. Selection is already decided by this
    # point and the model does not care about the order — but the byte stream
    # does: an unchanged set ordered by a score that shifts every turn produces
    # different bytes each time, and misses the prefix cache for no reason.
    chosen = sorted((mcp[i] for i in taken), key=lambda t: t.get("name", ""))
    withheld = [mcp[i] for i in sorted(set(range(len(mcp))) - taken, key=rank)]
    _remember_tool_selection(t.get("name", "") for t in chosen)
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


# How much of a tool's prose survives into the payload. Measured across the
# 64 tools of three real MCP servers: the mean schema is 503 chars (~125
# tokens), but the distribution has a long tail — `sequentialthinking` alone is
# 4,056 chars. At a 128-tool ceiling the block costs ~16,100 tokens a turn,
# four times the entire system prompt, and it is re-sent every single turn.
#
# These ceilings are set above the mean on purpose: the point is to clip the
# outliers, not to compress every tool. A description short enough to be
# ambiguous costs a wrong tool call, which is far dearer than the tokens saved.
MAX_TOOL_DESC_CHARS = _env_int("TOMAS_MAX_TOOL_DESC", 600, minimum=120)
MAX_TOOL_PARAM_DESC_CHARS = _env_int("TOMAS_MAX_PARAM_DESC", 220, minimum=40)

# Schema keys that describe the document rather than the call. Models do not
# read them and validators upstream do not require them.
_DROPPABLE_SCHEMA_KEYS = ("$schema", "title", "examples", "additionalProperties",
                          "$id", "definitions", "$defs")


def _clip(text: str, limit: int) -> str:
    """Trim prose to `limit`, preferring a sentence then a word boundary."""
    if not isinstance(text, str) or len(text) <= limit:
        return text
    cut = text[:limit]
    stop = max(cut.rfind(". "), cut.rfind("\n"))
    if stop > limit * 0.6:
        return cut[:stop + 1].rstrip()
    space = cut.rfind(" ")
    return (cut[:space] if space > limit * 0.6 else cut).rstrip() + "…"


def _compact_schema(node, depth: int = 0):
    """Recursively drop documentation-only keys and clip property prose.

    Everything that decides whether a call is well-formed — `type`,
    `properties`, `required`, `enum`, `items`, `default` — is preserved
    untouched. Only prose and metadata are reduced.
    """
    if isinstance(node, list):
        return [_compact_schema(v, depth + 1) for v in node]
    if not isinstance(node, dict):
        return node
    out = {}
    for key, value in node.items():
        if key in _DROPPABLE_SCHEMA_KEYS:
            continue
        if key == "description":
            # The top level of the schema is the tool's own blurb and is
            # clipped by the caller; nested ones are per-property.
            out[key] = _clip(value, MAX_TOOL_PARAM_DESC_CHARS) if depth else value
        else:
            out[key] = _compact_schema(value, depth + 1)
    return out


def compact_tool_schemas(tools: list[dict]) -> list[dict]:
    """Shrink the tool payload without changing what any tool accepts.

    Pure: returns new dicts and never mutates `ALL_TOOLS`, because the same
    tool objects are re-selected every turn and clipping them in place would
    compound turn after turn until the descriptions were gone.
    """
    compacted = []
    for tool in tools:
        if not isinstance(tool, dict):
            compacted.append(tool)
            continue
        new = dict(tool)
        if isinstance(new.get("description"), str):
            new["description"] = _clip(new["description"], MAX_TOOL_DESC_CHARS)
        schema = new.get("input_schema")
        if isinstance(schema, dict):
            new["input_schema"] = _compact_schema(schema)
        compacted.append(new)
    return compacted


def _max_tokens_was_set() -> bool:
    """True when the user named an output budget rather than taking the default."""
    return bool(os.environ.get("AGENT_MAX_TOKENS", "").strip())


def effective_max_tokens(caps) -> int:
    """This turn's output budget, honouring an explicit setting.

    `Capabilities.max_output_tokens` is documented as "the optimistic case;
    probing only ever narrows them" — but *nothing probes this field*, so its
    8192 default was being applied as a hard ceiling that no measurement ever
    justified. `min(MAX_TOKENS, caps.max_output_tokens)` therefore silently
    discarded AGENT_MAX_TOKENS: setting it to 32000 still ran at 8192, and the
    truncation message told the user to raise a variable that could not work.

    Measured against the endpoint that hit this: deepseek-v4-flash-free accepts
    max_tokens up to at least 65,536. The cap cost that provider 8x its real
    budget, which is what truncated a lab-manual build mid-write.

    So an explicit setting always wins here. Note this turns on the *field*
    never being probed, not on `caps.probed` — that flag goes true once any
    probe has run, while `max_output_tokens` stays untouched, so testing it
    would reinstate exactly the clamp this removes. If real probing is ever
    added for this field, make a measured value win instead.
    """
    if _max_tokens_was_set():
        return MAX_TOKENS
    ceiling = getattr(caps, "max_output_tokens", 0) or MAX_TOKENS
    return min(MAX_TOKENS, ceiling) or MAX_TOKENS


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
        "description": "Read a file from the filesystem. Returns contents with line numbers. Also extracts text from .pdf, .docx, .pptx, and .xlsx files -- no separate tool needed for those.",
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
        "description": "Write text content to a file. Creates the file (and parent dirs) if needed. Text formats only -- it cannot produce .docx/.pdf/.xlsx/.pptx, which are structured containers; build those with the word-docs MCP tools or a library.",
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

#: The tools whose risk tier strict mode leaves alone — they are this agent's
#: own, and their tiers are deliberate. Everything else (i.e. MCP) is reset to
#: "high". Previously spelled out as a literal set in two places, which drifted:
#: `read_mcp_resource` was in RISK_LEVELS but in neither copy.
BUILTIN_TOOL_NAMES = frozenset(RISK_LEVELS)

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
    """Say which rule was hit *and where to go instead*.

    Naming only the rule sent the model looking for a second forbidden
    location: told ~/.tomas was read-only it tried the system temp directory,
    was refused again, and spent two tool calls before landing in the project.
    An error that names the writable scratch path ends that search in one.
    """
    if write and _within(path, TOMAS_HOME):
        return (f"Error: {path} is under ~/.tomas, which is read-only. "
                f"For a throwaway helper script use {SCRATCH_DIR}; for durable "
                f"notes use save_memory or the self_notes API.")
    if write:
        return (f"Error: path outside project: {path}. Writes are only allowed "
                f"under {PROJECT_DIR}. For a throwaway helper script use "
                f"{SCRATCH_DIR} — the system temp directory is not writable "
                f"from here.")
    return (f"Error: path outside project: {path}. "
            f"Readable roots: {PROJECT_DIR} (read-write), {TOMAS_HOME} (read-only).")

# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

# Opening these in text mode (the default read_file path) just decodes their
# raw binary bytes as UTF-8 with replacement characters -- the PDF header and
# compressed font streams come back as "%PDF-1.5" and garbage, not the
# document's actual text. Each extractor turns the format into plain text
# read_file can line-number and paginate like any other file.

def _extract_pdf_text(path: Path) -> str:
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    pages = [f"--- page {i} ---\n{page.extract_text() or ''}"
             for i, page in enumerate(reader.pages, 1)]
    return "\n".join(pages)


def _extract_docx_text(path: Path) -> str:
    import docx
    document = docx.Document(str(path))
    parts = [p.text for p in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            parts.append(" | ".join(cell.text for cell in row.cells))
    return "\n".join(parts)


def _extract_pptx_text(path: Path) -> str:
    from pptx import Presentation
    prs = Presentation(str(path))
    parts: list[str] = []
    for i, slide in enumerate(prs.slides, 1):
        parts.append(f"--- slide {i} ---")
        for shape in slide.shapes:
            if shape.has_text_frame:
                parts.append(shape.text_frame.text)
    return "\n".join(parts)


def _extract_xlsx_text(path: Path) -> str:
    import openpyxl
    wb = openpyxl.load_workbook(str(path), data_only=True)
    parts: list[str] = []
    for ws in wb.worksheets:
        parts.append(f"--- sheet: {ws.title} ---")
        for row in ws.iter_rows(values_only=True):
            parts.append("\t".join("" if c is None else str(c) for c in row))
    return "\n".join(parts)


_DOCUMENT_EXTRACTORS = {
    ".pdf": _extract_pdf_text,
    ".docx": _extract_docx_text,
    ".pptx": _extract_pptx_text,
    ".xlsx": _extract_xlsx_text,
}


def handle_read_file(params: dict) -> str:
    path = _resolve(params["file_path"])
    if not _safe(path):
        return _outside_project_error(path)
    if not path.exists():
        return f"Error: file not found: {path}"
    offset = max(0, int(params.get("offset", 1)) - 1)
    limit = int(params.get("limit", 2000))
    suffix = path.suffix.lower()
    if suffix in _DOCUMENT_EXTRACTORS:
        try:
            text = _DOCUMENT_EXTRACTORS[suffix](path)
        except ImportError as e:
            return (f"Error: reading {suffix} files needs an extra package "
                     f"that isn't installed ({e}). Run: pip install -r requirements.txt")
        except Exception as e:
            return f"Error: could not extract text from {path.name}: {e}"
        lines = [line + "\n" for line in text.split("\n")]
    else:
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

# Formats whose files are containers, not text: .docx/.pptx/.xlsx are zip
# archives of XML parts, .pdf has an object table and xref. Writing a string
# to one of these paths produces a file that only *looks* right by extension.
# The read side of this was already fixed (see _DOCUMENT_EXTRACTORS); this is
# the write half. It reported "Successfully wrote 4933 chars" for a .docx
# that nothing could open, and the failure only surfaced several tool calls
# later as "Package not found", by which point the model was debugging its
# converter instead of its choice of tool.
_STRUCTURED_WRITE_FORMATS = {
    ".docx": "a Word document",
    ".doc": "a Word document",
    ".pptx": "a PowerPoint presentation",
    ".ppt": "a PowerPoint presentation",
    ".xlsx": "an Excel workbook",
    ".xls": "an Excel workbook",
    ".pdf": "a PDF file",
    ".odt": "an OpenDocument text file",
    ".ods": "an OpenDocument spreadsheet",
    ".odp": "an OpenDocument presentation",
    ".rtf": "an RTF document",
}


def _structured_write_error(path: Path, kind: str) -> str:
    """Say why this cannot work and what to reach for instead."""
    suffix = path.suffix.lower()
    if suffix in (".docx", ".doc"):
        how = ("the word-docs MCP tools (create_document, add_heading, "
               "add_paragraph, add_table), which build a real document")
    elif suffix == ".pdf":
        how = ("build the .docx first with the word-docs MCP tools, then "
               "convert_to_pdf — that keeps headings and bold. Generating a "
               "PDF from plain text loses all formatting")
    elif suffix in (".xlsx", ".xls"):
        how = "openpyxl through run_command"
    elif suffix in (".pptx", ".ppt"):
        how = "python-pptx through run_command"
    else:
        how = f"a library that writes {kind}, through run_command"
    return (f"Error: {path.name} is {kind} — a structured container, not text. "
            f"write_file would produce a file that has the right extension and "
            f"cannot be opened. Use {how}.")


def handle_write_file(params: dict) -> str:
    path = _resolve(params["file_path"])
    if not _safe(path, write=True):
        return _outside_project_error(path, write=True)
    kind = _STRUCTURED_WRITE_FORMATS.get(path.suffix.lower())
    if kind:
        return _structured_write_error(path, kind)
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
        from playwright.async_api import async_playwright  # deferred, see top
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

Memory and self-improvement:
- When the user states a durable preference, correction, or rule -- "always...",
  "never...", "from now on...", "I prefer...", or a correction to something you
  just did -- call save_memory in that same turn. Do not wait to be asked to
  remember it, and do not merely acknowledge it in prose: an acknowledgement is
  forgotten when the session ends, a saved memory is not.
- Phrase what you save the way the user phrased it. A rule meant for every reply
  ("always end with the date") must be saved in those words, because that is what
  makes it apply on every later turn rather than only when the topic comes up.
- Any standing rule already in your system prompt applies to EVERY reply in this
  session -- including replies about a completely unrelated topic, and including
  turns that are mostly tool calls. Following it on turn 1 and forgetting it by
  turn 20 is the failure to avoid.
- Before producing substantial work for a returning user, apply what you have
  been told about their conventions, terminology and formatting, rather than a
  generic default.
- A denied tool call will stay denied. Do not re-issue it with different wording.
- Reply in the language the user wrote in.
- Never say a file was created, saved, edited, or a task was completed unless
  you actually called the tool for it in this conversation and saw a
  successful result. If you only planned or described an action without
  calling the tool, say exactly that -- do not describe it as done. The turn
  ends the moment your reply has no tool call in it, so if you write "let me
  create..." or "I'll save this as...", call the tool in that same turn
  instead of stopping after the sentence.
- After creating or writing a file the user asked for, verify it exists
  (read_file or list_files) before telling them it is done. State the exact
  path you verified, not just the path you intended to use.
- A helper script you write only to carry out a step -- a converter, a
  generator, glue you will not need again -- is not a deliverable. Only what
  the user asked for belongs in their repo. Write it to `_scratch/` in the
  project root, which exists for exactly this, or skip the file and pass the
  code to `run_command` directly.
- Do NOT try to write to the system temp directory or anywhere under
  `~/.tomas`. Both are outside the writable sandbox and the call will be
  refused; `_scratch/` is the one scratch location you have.

Large deliverables:
- You have a per-reply output limit. A long document -- a methodichka, a
  report, a multi-section guide -- will not fit in one reply, and a reply that
  hits the limit is thrown away: the user gets nothing at all.
- So do not compose a long document in your reply text. Build it on disk,
  section by section: write_file the first section, then edit_file to append
  each following one. Each tool call is a separate turn with its own budget, so
  a document of any length is reachable this way, and progress survives even if
  a later turn fails.
- Say which section you are on as you go, so the user can see progress rather
  than waiting through several silent turns.
- Only after the file is complete and you have verified it with read_file do
  you convert it (e.g. word-docs convert_to_pdf) or summarise it to the user.
- .docx, .pdf, .xlsx and .pptx are structured containers, not text.
  write_file cannot produce them. Build a Word document with the word-docs
  MCP tools (create_document, then add_heading / add_paragraph / add_table),
  and get a PDF from convert_to_pdf on that document -- it keeps headings and
  bold, which re-typesetting the plain text into a new PDF throws away."""


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
MAX_INSTRUCTIONS_CHARS = 24000      # AGENTS.md + CLAUDE.md + global instructions
MAX_DIRECTIVES_CHARS = 1000         # standing rules (bounded by MAX_DIRECTIVES too)
MAX_LEARNED_CHARS = 1500            # retrieved facts (bounded by k, not by store size)
MAX_SKILLS_CHARS = 4000             # skills section
MAX_TRIGGERED_SKILL_CHARS = 6000    # full body of a skill this message triggers
MAX_TOTAL_SYSTEM_PROMPT = 44000     # hard cap on the entire system prompt.
# ~11k tokens against the 200k context the default models actually have.
#
# Raised from 8000/28000 when CLAUDE.md started being loaded alongside AGENTS.md
# (it had been documented as loaded for a long time without being loaded at
# all). Together they are 16,259 chars here, so the old instructions budget was
# discarding 8,259 of them — half the project's conventions, silently, with the
# truncation notice as the only trace.
#
# Affordable because of where this content now sits: the instructions are part
# of the stable prefix, so they are re-read from cache rather than re-tokenised
# every turn. The per-turn cost of a larger stable prefix is close to nothing;
# the per-turn cost of a larger *volatile* tail is not, which is why those
# budgets are unchanged.
#
# Worth knowing: AGENTS.md and CLAUDE.md overlap substantially in this repo —
# both describe the tool layers, the prompt load order and the quirks. Deduping
# them would recover roughly 2,000-3,000 chars of prompt with no loss.


# The smallest the stable prefix may be squeezed to when the volatile tail is
# unusually large. BASE_PROMPT plus the environment section is what the agent
# *is*; below this the rules it operates by start disappearing.
MIN_STABLE_CHARS = 6000

# Cached stable prefix, and the filesystem signature it was built from.
_stable_prefix: Optional[str] = None
_stable_signature: Optional[tuple] = None


def _stable_inputs() -> list[Path]:
    """Every file whose contents can change the stable prefix."""
    import skills_manager
    paths = [PROJECT_DIR / name for name in
             (*instructions_manager.PROJECT_INSTRUCTION_FILES,
              "AGENT_INSTRUCTIONS.md", "BEHAVIOR.md")]
    paths.append(instructions_manager.PROJECT_INSTRUCTIONS_DIR
                 / f"{PROJECT_DIR.name}.md")
    try:
        paths.extend(sorted(instructions_manager.GLOBAL_INSTRUCTIONS_DIR.glob("*.md")))
    except OSError:
        pass
    paths.extend(skills_manager.SKILL_DIRS)
    return paths


def _stable_fingerprint() -> tuple:
    """(path, mtime, size) for each input — cheap, and changes when they do.

    Rebuilding the stable half cost 18.8 ms and 98 `exists()` calls on every
    turn, re-reading files that change at most once a session. Keying on stat
    rather than a timer means an edit to AGENTS.md still takes effect on the
    very next message, which is what makes the file worth editing.
    """
    signature = []
    for path in _stable_inputs():
        try:
            st = path.stat()
            signature.append((str(path), int(st.st_mtime_ns), st.st_size))
        except OSError:
            signature.append((str(path), 0, -1))    # absent is a state too
    return tuple(signature)


def invalidate_prompt_cache() -> None:
    """Drop the cached prefix. For callers that change instructions in-process."""
    global _stable_prefix, _stable_signature
    _stable_prefix = None
    _stable_signature = None


def build_system_prompt(user_message: str = "") -> str:
    """Build the system prompt for this turn, stable part first.

    `user_message` is what learned knowledge is retrieved against. An empty
    query falls back to the most recently confirmed facts, so callers that
    have no message yet still get something sensible.

    ── Why the order is what it is ──

    Everything that does not depend on `user_message` is emitted before
    anything that does. Prefix caching — DeepSeek's automatic kind, Anthropic's
    `cache_control`, OpenAI's — matches on an exact byte prefix, and the system
    prompt is serialised *before* the messages. So the first byte that differs
    from last turn ends the cache hit for the system prompt **and for the whole
    conversation history behind it**.

    The catalogue used to sit last, after two sections rebuilt per message.
    Measured on Zen/DeepSeek over five turns with a populated fact store: 52.0%
    of prompt tokens served from cache, 14,864 billed as new. With the stable
    content moved ahead of the volatile content, the same five turns cached
    83.9% and billed 4,752 — 68% less. In a long session the saving is larger
    still, because it is the history that stops being re-read.
    """
    # ── Stable: identical every turn until a file on disk changes ──
    global _stable_prefix, _stable_signature
    signature = _stable_fingerprint()
    if _stable_prefix is not None and signature == _stable_signature:
        stable = _stable_prefix
    else:
        stable = BASE_PROMPT + _environment_section()
        # project-level instructions from AGENTS.md / agent.md / CLAUDE.md
        # plus ~/.tomas/instructions/
        instructions_section = build_instructions_section(PROJECT_DIR)
        if instructions_section:
            instructions_section = _truncate_section(
                instructions_section, MAX_INSTRUCTIONS_CHARS, "instructions"
            )
            stable += f"\n\n{instructions_section}"
        # legacy support: AGENT_INSTRUCTIONS.md or BEHAVIOR.md
        for candidate in [PROJECT_DIR / "AGENT_INSTRUCTIONS.md",
                          PROJECT_DIR / "BEHAVIOR.md"]:
            if candidate.exists():
                legacy = candidate.read_text(encoding="utf-8")
                legacy = _truncate_section(legacy, 2000, candidate.name)
                stable += f"\n\n# Agent Instructions ({candidate.name})\n{legacy}"
                break
        # installed skills — budgeted by whole entries, not by slicing the
        # joined string at a character offset (which used to cut mid-skill-
        # name). Names only; it does not vary with the message, so it belongs
        # up here where it can be cached rather than re-sent every turn.
        skills_section = build_skills_section(max_chars=MAX_SKILLS_CHARS)
        if skills_section:
            stable += f"\n\n{skills_section}"
        _stable_prefix = stable
        _stable_signature = signature

    # ── Volatile: rebuilt against this turn's message ──
    tail = ""
    # Standing rules — always on, never retrieved. These lead the tail for a
    # measured reason. A 30-turn session ran two standing rules at once: "end
    # with My Lord" from AGENT.md (static, imperative heading) was obeyed 29/29;
    # "append the date" from the fact store (retrieved, filed under "What I've
    # learned") was obeyed 0/29 — while being present in the prompt on all 29
    # turns. The rule was never the problem; the channel was. Directives keep
    # their own imperative heading and still precede the retrieved facts.
    try:
        directives = learning.directives_for_prompt()
        if directives:
            directives = _truncate_section(directives, MAX_DIRECTIVES_CHARS,
                                           "standing rules")
            tail += (
                "\n\n# Standing rules from the user — these apply to EVERY reply\n"
                "You MUST follow every rule below on every single turn. They "
                "apply even when the current message is about something else "
                "entirely, and even on turns where you mostly call tools. These "
                "are not background information about the user; they are "
                "instructions to you, and the user checks whether you followed "
                "them.\n\n"
                f"{directives}")
    except Exception:
        pass
    # ── What the agent has learned — retrieved, not dumped ──
    # This replaces the old memory-index dump, the notes dump and the tips
    # block. Those three grew with everything ever learned until entries
    # silently fell off the end of the budget; retrieval keeps the prompt
    # flat in size no matter how much is stored.
    try:
        learned = learning.recall(user_message, k=5)
        if learned:
            learned = _truncate_section(learned, MAX_LEARNED_CHARS, "learned")
            # Heading rewritten from "What I've learned about this user and
            # project". That phrasing read as a dossier and the model treated it
            # as trivia. Same data, actionable framing.
            tail += ("\n\n# Context retrieved for this message — apply what is "
                     "relevant\n"
                     f"{learned}")
    except Exception:
        pass
    # A skill this message triggers goes in full. The catalogue above only
    # names skills — enough to pick one, useless for following one — and
    # nothing ever read `triggers`, so a procedure written for a job never
    # reached the model while it was doing that job.
    try:
        triggered = build_triggered_skills(user_message, MAX_TRIGGERED_SKILL_CHARS)
        if triggered:
            tail += f"\n\n{triggered}"
    except Exception:
        pass
    # NOTE: the self-improvement tips/session-context block used to be injected
    # here. It was template text addressed to a human developer ("Consider
    # creating shortcuts or aliases for this tool") that consumed context and
    # changed nothing about the model's behaviour. Reflection replaces it; the
    # generator code is still in self_improve.py pending deletion.

    # ── Hard cap on the total system prompt ──
    # The tail is what the model must act on *now* — the user's standing rules
    # and the procedure for the job in hand — so the cap is taken out of the
    # stable half, from the end (the skills catalogue), and never out of the
    # tail. Trimming from the end of the joined string, as this used to, made
    # the triggered skill body the first thing dropped.
    if len(stable) + len(tail) > MAX_TOTAL_SYSTEM_PROMPT:
        room = max(MIN_STABLE_CHARS, MAX_TOTAL_SYSTEM_PROMPT - len(tail))
        stable = _truncate_section(stable, room, "system prompt")
    return stable + tail

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
    records the same thing as a fact, which is what the prompt actually reads.
    Either way the user said it outright — no inference — so it goes active
    immediately rather than through the evidence gate.

    The kind decides which prompt channel it lands in, and that decision is
    what determines whether it gets followed. An unconditional rule ("always
    append the date") becomes a `directive` and is injected on every turn; a
    conditional preference ("prefers PowerShell") stays `explicit` and is
    retrieved when the topic comes up.
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
            body = f"{description}: {content}".strip(": ")
            # Classify on the whole record: the rule wording can live in either
            # half ("Never delete logs" / "see the runbook").
            kind = (learning.KIND_DIRECTIVE if learning.looks_like_directive(body)
                    else learning.KIND_EXPLICIT)
            learning.remember(kind, body,
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
        repaired = learning.repair_frontmatter_facts()
        if repaired.get("repaired"):
            print(f'  {GREEN}✦{RESET} {DIM}Repaired {repaired["repaired"]} '
                  f'learned fact(s) that had note metadata baked in{RESET}')
    except Exception:
        pass


def reflect_on_session_end(messages: list) -> None:
    """Learn from the finished session. Never raises, never blocks the user.

    Runs and persists silently — results surface on request via
    /self-improve facts and /self-improve reflect, not as unprompted
    chatter at the moment a session ends.
    """
    if not learning.is_enabled():
        return
    try:
        # Corrections first: deterministic, free, and independent of whether
        # the reflection model call succeeds. A provider outage at session end
        # should not cost the user the one signal they gave explicitly.
        learning.promote_corrections(messages)
    except Exception:
        pass
    try:
        learning.run_session_reflection(messages, call_model=_learning_call_model)
        for scope in ("global", "project"):
            learning.decay(scope)
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
    mode_now = learning.reflect_mode()
    if mode_now == "shadow":
        lines.append('')
        lines.append(f'  {DIM}Shadow mode: nothing above was written to the '
                     f'store. Unset TOMAS_REFLECT to enable (active is the '
                     f'default).{RESET}')
    elif mode_now == "off":
        lines.append('')
        lines.append(f'  {DIM}Reflection is off (TOMAS_REFLECT=off). Nothing is '
                     f'learned from finished sessions.{RESET}')
    return '\n'.join(lines)

# ---------------------------------------------------------------------------
# Context management — auto-compaction
# ---------------------------------------------------------------------------

def estimate_tool_tokens(tools: list) -> int:
    """Token cost of a tool block, counted as the JSON it is serialised to."""
    try:
        return int(sum(len(json.dumps(t)) for t in tools) / CHARS_PER_TOKEN_JSON)
    except Exception:
        return 0


def _estimate_tokens(messages: list) -> int:
    """Rough token estimate for a message list."""
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
    return total_chars // CHARS_PER_TOKEN_PROSE

def _estimate_system_prompt_tokens(system_prompt: str) -> int:
    """Estimate tokens for the system prompt string."""
    return len(system_prompt) // CHARS_PER_TOKEN_PROSE

# ── Pruning old tool results ───────────────────────────────────────────
# Measured on a real 55-call session: history was 48,655 tokens of the ~60,000
# sent per call, and 29,394 of those — 60% of the history — were tool results.
# A file read on turn 2 was still being re-sent on turn 50. The model has long
# stopped reading it; the transcript is paying for it every single turn.

#: How many of the most recent tool-result batches are kept verbatim.
#:
#: Counted in batches, not user turns. The first version counted user turns and
#: found nothing to prune in the very session that motivated the feature: it
#: made 56 tool calls inside *two* user turns, so "older than 3 turns" never
#: matched anything. Long agentic work happens *within* a turn, which is
#: exactly where the history piles up.
TOOL_RESULT_KEEP_TURNS = _env_int("TOMAS_KEEP_RESULT_BATCHES", 8, minimum=1)

#: Results smaller than this are left alone — stubbing a 300-char result costs
#: information and saves nothing.
TOOL_RESULT_STUB_OVER = _env_int("TOMAS_STUB_RESULTS_OVER", 2_000, minimum=200)

#: Only prune once there is this much to gain, in characters.
#:
#: This is what keeps pruning cache-friendly. Stubbing rewrites a message in
#: the middle of the transcript, which invalidates the prefix cache from that
#: point on — so doing it every turn would trade one saving for a permanent
#: stream of cache misses. Pruning in occasional large batches pays that cost
#: rarely. Stubbing is monotonic (a stub is never re-stubbed), so everything
#: before the pruned point stays byte-identical.
TOOL_RESULT_PRUNE_AT = _env_int("TOMAS_PRUNE_RESULTS_AT", 20_000, minimum=2_000)

_PRUNED_MARK = "[older tool result released from context"


def _is_user_turn(message: dict) -> bool:
    """True for a real user message, as opposed to a tool-result carrier.

    Both have role "user" — the tool-result ones carry a list of tool_result
    blocks and no prose, which is what separates one turn from the next.
    """
    if message.get("role") != "user":
        return False
    content = message.get("content")
    if isinstance(content, str):
        return True
    if isinstance(content, list):
        return any(isinstance(b, dict) and b.get("type") != "tool_result"
                   for b in content)
    return False


def prunable_chars(messages: list, keep_turns: int = TOOL_RESULT_KEEP_TURNS,
                   stub_over: int = TOOL_RESULT_STUB_OVER) -> int:
    """How much could be reclaimed right now, without changing anything."""
    return sum(saved for _, _, saved in _prune_targets(messages, keep_turns,
                                                       stub_over))


def _prune_targets(messages: list, keep_batches: int, stub_over: int):
    """Yield (message_index, block_index, chars_saved) for each stubbable result.

    Walks backwards counting *batches of tool results* — one message carrying
    tool_result blocks is one batch — and leaves the newest `keep_batches`
    alone. Those are what the model is still reasoning over; everything behind
    them is reference material it can re-fetch far more cheaply than it can
    carry.
    """
    batches_seen = 0
    for i in range(len(messages) - 1, -1, -1):
        content = messages[i].get("content")
        if not isinstance(content, list):
            continue
        results = [(j, b) for j, b in enumerate(content)
                   if isinstance(b, dict) and b.get("type") == "tool_result"]
        if not results:
            continue
        batches_seen += 1
        if batches_seen <= keep_batches:
            continue
        for j, block in results:
            body = block.get("content")
            if not isinstance(body, str) or body.startswith(_PRUNED_MARK):
                continue
            if len(body) > stub_over:
                yield i, j, len(body)


def prune_tool_results(messages: list,
                       keep_turns: int = TOOL_RESULT_KEEP_TURNS,
                       stub_over: int = TOOL_RESULT_STUB_OVER,
                       prune_at: int = TOOL_RESULT_PRUNE_AT) -> int:
    """Replace the body of old, large tool results with a stub, in place.

    Returns the characters reclaimed (0 if it was not worth doing yet).

    The `tool_use`/`tool_result` pairing is preserved exactly — only the body
    text is replaced — because dropping the block outright leaves a dangling
    tool_call that upstreams reject.

    The stub says what was there and how to get it back, so this is a
    *release*, not a loss: the model can re-read the file if it turns out to
    still need it, which is far cheaper than carrying it for fifty turns.
    """
    targets = list(_prune_targets(messages, keep_turns, stub_over))
    if sum(saved for _, _, saved in targets) < prune_at:
        return 0
    reclaimed = 0
    for i, j, _ in targets:
        block = messages[i]["content"][j]
        body = block["content"]
        block["content"] = (
            f"{_PRUNED_MARK} — it was {len(body):,} characters. "
            f"Re-run the tool if you need it again.]")
        reclaimed += len(body) - len(block["content"])
    return reclaimed


def maybe_prune(messages: list) -> list:
    """Prune if it is worth it, and say so. Mirrors `maybe_compact`'s shape."""
    reclaimed = prune_tool_results(messages)
    if reclaimed > 0:
        print(f'  {DIM}[context] released {reclaimed:,} chars '
              f'(~{reclaimed // 4:,} tokens) of old tool results{RESET}')
    return messages


def maybe_compact(messages: list, system_prompt: str = "") -> list:
    """Compact the conversation if it's getting too large.

    Now accounts for system_prompt + tool definitions + max_tokens in the budget.
    """
    msg_tok = _estimate_tokens(messages)
    sys_tok = _estimate_system_prompt_tokens(system_prompt) if system_prompt else 0
    # Total budget includes system prompt + tool definitions + max_tokens + messages
    #
    # Capped in absolute terms as well as proportionally. 75% of a million-token
    # window is 750,000 tokens, so on a large-context model compaction simply
    # never fired: a long session grew without limit and every turn paid to
    # re-read all of it — slower and dearer each message, for history the model
    # had long stopped using. The proportional rule still governs small windows,
    # where 75% is the real constraint.
    total_budget = min(CONTEXT_WINDOW * COMPACTION_THRESHOLD, COMPACTION_CEILING)
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
# How many times bypass mode may extend the budget without asking. Nine
# extensions plus the initial budget is 400 tool calls at the default — more
# than any observed task (the session that motivated the mode used 56) while
# still being a number, so an unattended runaway ends instead of billing on.
MAX_AUTO_CONTINUATIONS = _env_int("TOMAS_MAX_AUTO_CONTINUATIONS", 9, minimum=1)
# Ceiling on a single tool result, kept in step with core.state's fail-safe.
# Raise it when a task genuinely needs one enormous result in context.
MAX_RESULT_CHARS = _env_int("TOMAS_MAX_RESULT_CHARS", 30_000, minimum=2_000)
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


#: User turns of context tool selection reads, newest first, and how many
#: times the newest is repeated to keep it outweighing them.
TOOL_CONTEXT_TURNS = 8
TOOL_CONTEXT_WEIGHT = 3
TOOL_CONTEXT_CHARS = 2000


def _recent_user_text(messages: list, turns: int = TOOL_CONTEXT_TURNS) -> str:
    """What the user has been asking about, not just their last keystroke.

    Selecting on the newest message alone loses the task the moment the user
    answers a question about it. Observed in session 20260804_144250: the
    request naming "docx" and "pdf" selected the whole word-docs working set,
    then "зроби новий", "2" and "зроби це, все вірно" each selected none of it
    — three words carry no keywords, so selection fell back to list order.
    By the time the user said "yes, do it", the tools to do it were gone, and
    the model concluded it would have to write a text file named .docx.

    The newest message is repeated because `extract_keywords` ranks by
    frequency: the current turn should still outweigh what came before it,
    or a conversation could never change subject.
    """
    collected: list[str] = []
    for m in reversed(messages or []):
        if m.get("role") != "user":
            continue
        content = m.get("content")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            # Tool-result turns carry no user intent; keep looking back.
            text = "\n".join(b.get("text", "") for b in content
                             if isinstance(b, dict) and b.get("type") == "text")
        else:
            text = ""
        if text.strip():
            collected.append(text)
        if len(collected) >= turns:
            break
    if not collected:
        return ""
    weighted = [collected[0]] * TOOL_CONTEXT_WEIGHT + collected
    return "\n".join(weighted)[:TOOL_CONTEXT_CHARS]


def tools_for_turn(messages: list) -> tuple[list[dict], list[dict]]:
    """Pick this turn's tool payload.

    Re-selected per turn rather than per session: a user who starts with file
    edits and moves to browser automation should get browser tools when they
    ask for them, not whichever server connected first at startup.
    """
    pool = ALL_TOOLS or COMBINED_TOOLS or TOOLS
    context = _recent_user_text(messages)
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


def cache_marked_system(system_prompt: str):
    """Mark the stable prefix so the provider caches it instead of re-reading it.

    Every turn resends ~6,300 identical tokens — the system prompt plus the
    tool definitions — and re-tokenising and re-attending them is most of what
    a short turn costs. One `cache_control` breakpoint at the end of the system
    block covers the whole prefix, because Anthropic's cache hierarchy is
    tools → system → messages: marking system caches the tools ahead of it too.

    Returns the block form when caching is on, the plain string otherwise, so
    the call site stays a single assignment. OpenAI-wire providers get the
    string: they do prefix caching server-side and have no `cache_control`,
    and `anthropic_to_openai` flattens block lists anyway.
    """
    if not system_prompt:
        return system_prompt
    return [{"type": "text", "text": system_prompt,
             "cache_control": {"type": "ephemeral"}}]


#: How many `cache_control` breakpoints Anthropic accepts in one request.
#: One is spent on the system block; the rest are available to the history.
MAX_CACHE_BREAKPOINTS = 4

#: Don't spend a breakpoint on a trivial amount of history — each one has a
#: write cost, and caching 200 tokens never repays it.
MIN_CACHED_HISTORY_CHARS = 4_000


def _measure(message: dict) -> int:
    """Characters the model actually sees in one message.

    Deliberately not `json.dumps`: that escapes Cyrillic to `\\uXXXX`, six
    characters for one, and inflated a real Ukrainian transcript four-fold —
    which would make every size threshold in this module fire at a quarter of
    its intended size on exactly the sessions this project is used for.
    """
    content = message.get("content")
    if isinstance(content, str):
        return len(content)
    total = 0
    for block in content or []:
        if not isinstance(block, dict):
            total += len(str(block))
            continue
        kind = block.get("type")
        if kind == "text":
            total += len(block.get("text") or "")
        elif kind == "tool_result":
            total += len(str(block.get("content") or ""))
        elif kind == "tool_use":
            try:
                total += len(json.dumps(block.get("input") or {},
                                        ensure_ascii=False))
            except Exception:
                total += len(str(block.get("input") or ""))
        else:
            total += len(str(block))
    return total


def clear_history_cache_marks(messages: list) -> None:
    """Remove every `cache_control` this module put on the history."""
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict):
                block.pop("cache_control", None)


def mark_history_for_caching(messages: list, breakpoints: int = 3) -> int:
    """Put `cache_control` on the history so it is re-read, not re-processed.

    Only the system block was ever marked. But the system prompt is ~7,000
    tokens while the history reached 48,655 in a real session — so the part
    that dominated the bill was the part paying full price on every turn,
    because Anthropic caches a *prefix* and the prefix ended where the marks
    did.

    Mutates in place and returns how many marks were placed. In place because
    `core.loop` appends to this very list: handing back a copy would send the
    marked version to the model while the assistant's replies accumulated in
    an object nobody reads — the no-memory bug, reintroduced silently.

    Moving a breakpoint between turns is free: `cache_control` is a directive
    about where to cut, not content, so it does not change the prefix the cache
    is keyed on. That is what makes re-marking every turn safe.

    Only messages whose content is *already* a block list are marked. A string
    would have to be reshaped into blocks to carry the key, and that rewrites
    the user's own turns in the saved transcript for a few tokens of benefit.
    Tool-heavy sessions — the ones with history worth caching — are mostly
    block-list messages anyway.
    """
    clear_history_cache_marks(messages)
    if len(messages) < 2 or breakpoints < 1:
        return 0

    # Candidates are any block-list message except the last. Turn boundaries
    # are deliberately *not* used: a turn that makes 56 tool calls offers one
    # boundary and one breakpoint, leaving the bulk of its own history uncached
    # — which is the case that needs caching most.
    running, sized = 0, []
    for i, message in enumerate(messages):
        running += _measure(message)
        if (i < len(messages) - 1
                and isinstance(message.get("content"), list)
                and running >= MIN_CACHED_HISTORY_CHARS):
            sized.append(i)
    if not sized:
        return 0

    # The latest candidate matters most — it caches the longest prefix. The
    # others are spread evenly behind it so that when the tail shifts, there is
    # still a usable cached prefix further back instead of an all-or-nothing
    # miss.
    chosen = {sized[-1]}
    for n in range(1, breakpoints):
        chosen.add(sized[max(0, len(sized) * (breakpoints - n) // (breakpoints + 1))])

    placed = 0
    for i in sorted(chosen)[-breakpoints:]:
        tail = next((b for b in reversed(messages[i]["content"])
                     if isinstance(b, dict)), None)
        if tail is not None:
            tail["cache_control"] = {"type": "ephemeral"}
            placed += 1
    return placed


def build_state(system_prompt: str, messages: list, responder) -> AgentState:
    """Assemble the turn context the core needs out of this module's globals."""
    selected, withheld = tools_for_turn(messages)
    # Compacted here rather than at discovery so `ALL_TOOLS` keeps the full
    # schemas: selection scores against descriptions, and scoring clipped text
    # would quietly change which tools a message retrieves.
    selected = compact_tool_schemas(selected)
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

    # Last, so it wraps the finished text: the withheld-tools notice and the
    # text-protocol description are both appended above, and a breakpoint
    # placed before them would cache a prefix that no longer matches.
    if caps.prompt_caching and caps.system_prompt:
        system_prompt = cache_marked_system(system_prompt)
    if caps.prompt_caching:
        # The system block spends one of the four breakpoints; the history
        # gets the rest. In place — see the note in mark_history_for_caching
        # about why a copy would lose the transcript.
        mark_history_for_caching(messages,
                                 breakpoints=MAX_CACHE_BREAKPOINTS - 1)

    return AgentState(
        system_prompt=system_prompt,
        messages=messages,
        get_client=_get_client,
        get_model=_get_model,
        tools=selected if caps.tool_use else [],
        max_tokens=effective_max_tokens(caps),
        execute_tool=execute_tool,
        risk_of=risk_for,
        origin_of=_tool_origin,
        responder=responder,
        approvals=APPROVALS,
        auto_approve_low=AUTO_APPROVE_LOW,
        yolo=YOLO_MODE,
        auto_continue=BYPASS_MODE,
        max_auto_continuations=MAX_AUTO_CONTINUATIONS,
        tool_budget=MAX_TOOL_CALLS_PER_TURN,
        max_result_chars=MAX_RESULT_CHARS,
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


# ── Deterministic rule capture ─────────────────────────────────────────
# A live run exposed the other half of the problem. Told "Rule one: always end
# every reply with the date. Save that to memory", the model replied "Saved."
# and never called save_memory — three times in a row, in a session whose tool
# log shows ten calls and not one of them a write. BASE_PROMPT already forbids
# claiming an action was taken without calling the tool; a weak model ignored
# it anyway.
#
# Retrieval was only ever half the loop. If capture depends on the model
# choosing to call a tool, then on a model that does not, the user's rule is
# lost while being told it was kept — which is worse than refusing outright.
# So when the user states a rule *and* asks for it to be saved, save it here,
# deterministically, before the model gets a chance not to.
#
# Both signals are required. Either alone is too loose: "always run the tests
# first" is conversation, and "save this file" is not a rule.
_SAVE_REQUEST = re.compile(
    r"\b(?:save|remember|store|keep)\b.{0,30}?"
    r"\b(?:that|this|it|them|memory|rules?|preferences?)\b"
    # Ukrainian writes this with U+02BC (ʼ) or U+2019 (’) far more often than
    # with an ASCII quote, and matching only ' silently drops the whole
    # language — the one this agent defaults to.
    r"|запам['’ʼʹ]?ятай|\b(?:збережи|запомни|сохрани)\b"
    r"|\bfrom now on\b|\bвідтепер\b|\bотныне\b",
    re.IGNORECASE,
)


def capture_stated_rule(user_text: str) -> Optional[str]:
    """Persist a rule the user stated and asked to have saved.

    Returns the text captured, or None. Never raises — nothing in the learning
    path may break the user's turn.
    """
    text = (user_text or "").strip()
    if not text or len(text) > 600:
        return None
    if not _SAVE_REQUEST.search(text):
        return None
    try:
        if not learning.is_enabled() or not learning.looks_like_directive(text):
            return None
        learning.remember(learning.KIND_DIRECTIVE, text,
                          evidence="user stated it and asked to save it",
                          scope="global")
        return text
    except Exception:
        return None


# ── Standing-rule reinforcement ────────────────────────────────────────
# Compliance in the transcripts is bimodal *per session*, not per turn: whatever
# the model did on turn 1 it kept doing for the next 29, whether or not that
# matched the rule. That is transcript momentum, and a system prompt alone does
# not beat it — the conversation is simply the louder signal by turn 20. So the
# rules are also restated in-context, periodically and briefly.
STANDING_RULE_REMINDER_EVERY = 10
_REMINDER_OPEN = "<standing-rules>"
_REMINDER_CLOSE = "</standing-rules>"


def _reinforce_standing_rules(messages: list) -> None:
    """Every Nth user turn, restate the standing rules inside the conversation.

    Appended to the user's own message rather than sent as a separate turn, so
    it cannot be mistaken for something the user said on its own and cannot
    desynchronise the user/assistant alternation some providers require.
    """
    if not messages or messages[-1].get("role") != "user":
        return
    user_turns = sum(1 for m in messages
                     if m.get("role") == "user" and isinstance(m.get("content"), str))
    if user_turns == 0 or user_turns % STANDING_RULE_REMINDER_EVERY != 0:
        return
    try:
        rules = learning.directives_for_prompt()
    except Exception:
        return
    if not rules:
        return
    content = messages[-1].get("content")
    if not isinstance(content, str) or _REMINDER_OPEN in content:
        return
    messages[-1]["content"] = (
        f"{content}\n\n{_REMINDER_OPEN}\n"
        f"Still in force — these applied to every reply so far and apply to "
        f"this one:\n{rules}\n{_REMINDER_CLOSE}"
    )


def _text_of_message(message: dict) -> str:
    """The plain text of a message, whatever block shape it arrived in."""
    content = message.get("content")
    if isinstance(content, str):
        return content
    parts = []
    for block in content or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "\n".join(parts)


def _intercept_slash_command(messages: list) -> Optional[str]:
    """Run a `/command` that arrived as a *message* rather than as REPL input.

    `handle_slash_command` used to be reachable only from the REPL's own input
    path, so a harness, a subagent, or a pasted script that sent "/status" as a
    message got it forwarded to the model as prose. The model then either
    invented the output or tried to shell out to reimplement the command — both
    observed, repeatedly, in the session transcripts.

    Unknown commands deliberately fall through to the model: a message that
    merely begins with a path must not be swallowed. `//` escapes a literal
    leading slash.

    The name is matched against SLASH_COMMANDS *before* dispatching, rather
    than relying on the handler's return value — the handler answers an
    unrecognised command with the help screen, not with None, so dispatching
    first would turn "/usr/local/bin is where it lives" into a help dump.
    """
    if not messages or messages[-1].get("role") != "user":
        return None
    text = _text_of_message(messages[-1]).strip()
    if not text.startswith("/") or text.startswith("//"):
        return None
    # Only a single-line command — a code block that happens to open with a
    # slash is not an instruction to the REPL.
    if "\n" in text:
        return None
    name = text[1:].split(maxsplit=1)[0].lower() if len(text) > 1 else ""
    if name not in SLASH_COMMANDS:
        return None
    try:
        result = handle_slash_command(text[1:], messages)
    except Exception:
        return None
    if result is None:
        return None
    # The command consumed the turn; drop the user message so the transcript
    # does not carry a prompt that never reached the model.
    messages.pop()
    return result


def agent_loop(system_prompt: str, messages: list) -> str:
    """Shim — drives core.loop.run_turn through the terminal adapter.

    Kept so the REPL and agent_cli.py keep working unchanged. New front ends
    should drive run_turn directly with their own adapter instead.
    """
    global _streaming_disabled

    handled = _intercept_slash_command(messages)
    if handled is not None:
        return handled

    # Capture before the model call, so the rule is stored whether or not the
    # model bothers to call save_memory — and so it is already in the prompt
    # this same turn rather than only from the next one.
    if messages and messages[-1].get("role") == "user":
        captured = capture_stated_rule(_text_of_message(messages[-1]))
        if captured:
            system_prompt = build_system_prompt(_text_of_message(messages[-1]))

    _reinforce_standing_rules(messages)

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
    "rules":        {"desc": "Standing rules applied to every reply",  "icon": "📌"},
    "note":         {"desc": "Create a self-note: /note <title> <content>", "icon": "📝"},
    "notes":        {"desc": "List all self-notes",               "icon": "📒"},
    "exit":         {"desc": "Exit TOMAS",                        "icon": "✕"},
}

def _get_model() -> str:
    """Read model from environment or active provider configuration."""
    model = os.environ.get("AGENT_MODEL")
    if model:
        return model
    try:
        import provider_manager
        active = provider_manager.get_active()
        if active and active.model:
            return active.model
    except Exception:
        pass
    return "Not set"



# The keys the prompt actually binds. Kept next to the help renderer so a new
# binding cannot be added without the place users look to find it.
KEY_HELP = [
    ("Enter",        "send · accepts the highlighted /command"),
    ("Tab",          "complete a /command · otherwise cycle mode"),
    ("↑ ↓",          "history · moves the /command selection"),
    ("Esc",          "clear the line"),
    ("Ctrl+W",       "delete the last word"),
    ("Ctrl+U",       "clear the line"),
    ("Ctrl+Z",       "undo the last clear, paste or history recall"),
    ("Ctrl+Y",       "copy the line to the clipboard · ↑ first to copy an older one"),
    ("Ctrl+L",       "clear the screen, keep what is typed"),
    ("Ctrl+C",       "cancel"),
    ("⇧+Space",      "toggle auto-approve"),
    ("F5 F6 F7 F8 F9", "auto · default · strict · yolo · bypass"),
]


def _show_commands(match: str = "") -> str:
    """Build a formatted help string for matching slash commands."""
    from text_display import rule

    lines = []
    lines.append(f'  {BOLD}Available commands{RESET}')
    lines.append(f'  {DIM}{rule()}{RESET}')
    for cmd, info in sorted(SLASH_COMMANDS.items()):
        if match and not cmd.startswith(match.lower()):
            continue
        icon = info["icon"]
        desc = info["desc"]
        padded = " " * max(1, 12 - len(cmd))
        lines.append(f'    {DIM}{icon}{RESET}  {CYAN}/{cmd}{RESET}{padded}{DIM}{desc}{RESET}')

    # Only when showing everything: filtered output is an answer to a specific
    # question, and a key table underneath it is just noise.
    if not match:
        lines.append('')
        lines.append(f'  {BOLD}Keys{RESET}')
        lines.append(f'  {DIM}{rule()}{RESET}')
        width = max(len(k) for k, _ in KEY_HELP)
        for key, desc in KEY_HELP:
            lines.append(f'    {CYAN}{key}{RESET}{" " * (width - len(key) + 2)}{DIM}{desc}{RESET}')

    lines.append('')
    lines.append(f'  {DIM}Type{RESET} {CYAN}/command{RESET} {DIM}to run — or just{RESET} {CYAN}/{RESET} {DIM}to see all{RESET}')
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
    global mcp_manager, AUTO_APPROVE_LOW, YOLO_MODE, BYPASS_MODE
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
        mode = current_mode_name()
        lines = [
            f'  {BOLD}TOMAS Status{RESET}',
            f'  {DIM}{"─" * 46}{RESET}',
            f'  {CYAN}◎{RESET}  Model:     {model_status}',
            f'  {CYAN}▣{RESET}  Context:   {cw:,} tokens',
            f'  {CYAN}⚙{RESET}  Mode:      {mode_color(mode)}{mode}{RESET}',
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
        lines.append(f'  {CYAN}⌨{RESET}  Toggle:    {DIM}⇧+Space{RESET}  ·  '
                     f'{DIM}{MODE_KEYS_HINT}{RESET}')
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
            BYPASS_MODE = False
            return (
                f'  {BOLD}{RED}⚡ YOLO mode enabled!{RESET} {RED}All tools auto-approved.{RESET}'
            )
        elif arg in ("bypass", "bypass-permissions", "nonstop"):
            AUTO_APPROVE_LOW = True
            YOLO_MODE = True
            BYPASS_MODE = True
            return (
                f'  {BOLD}{RED}⇥ BYPASS mode enabled!{RESET} '
                f'{RED}All tools auto-approved and the turn will not stop to '
                f'ask whether to continue.{RESET}\n'
                f'  {DIM}Bounded at {MAX_AUTO_CONTINUATIONS} automatic budget '
                f'extensions ({MAX_TOOL_CALLS_PER_TURN * (MAX_AUTO_CONTINUATIONS + 1)} '
                f'tool calls per turn); loop detection still applies.{RESET}'
            )
        # No arg or unknown arg — show current mode
        mode = current_mode_name()
        lines = [
            f'  {BOLD}Current Mode{RESET}',
            f'  {DIM}{"─" * 46}{RESET}',
            f'  {CYAN}⚙{RESET}  Mode:        {BOLD}{mode}{RESET}',
            f'  {CYAN}✓{RESET}  Auto-approve: {"ON" if AUTO_APPROVE_LOW else "OFF"}',
            f'  {CYAN}⇥{RESET}  Auto-continue: '
            f'{"ON — up to %d extensions" % MAX_AUTO_CONTINUATIONS if BYPASS_MODE else "OFF — asks at the budget checkpoint"}',
            '',
            f'  {DIM}Quick keys:{RESET}',
            f'    {DIM}⇧+Space{RESET}  — toggle  ·  {DIM}Tab{RESET} cycles  ·  '
            f'{DIM}F5{RESET} auto  {DIM}F6{RESET} default  {DIM}F7{RESET} strict  '
            f'{DIM}F8{RESET} yolo  {DIM}F9{RESET} bypass',
            f'  {DIM}Slash commands:{RESET}',
            f'    /mode auto     — auto-approve low-risk tools',
            f'    /mode default  — ask before every tool',
            f'    /mode strict   — ask for everything, clear overrides',
            f'    /mode yolo     — {RED}auto-approve ALL tools (no prompts){RESET}',
            f'    /mode bypass   — {RED}yolo + never ask whether to continue{RESET}',
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

    # ── Standing rules ──
    if cmd == "rules":
        rest = (parts[1] if len(parts) > 1 else "").strip()
        if rest.startswith("forget"):
            target = rest.split(maxsplit=1)
            if len(target) < 2:
                return (f'  {YELLOW}Usage:{RESET} {CYAN}/rules forget <id>{RESET}')
            removed = learning.forget(target[1].strip())
            if not removed:
                return f'  {DIM}No rule with id {target[1].strip()}.{RESET}'
            # Precise about what a tombstone does: it stops *reflection*
            # inferring the rule again. Stating it yourself still brings it
            # back, which is the behaviour you want — otherwise one /forget
            # would permanently blacklist a rule you later change your mind on.
            return (f'  {GREEN}✓{RESET} Rule removed: '
                    f'{removed.get("fact", "")[:70]}\n'
                    f'  {DIM}Reflection will not infer it again. Telling me the '
                    f'rule yourself still restores it.{RESET}')

        directives = learning.load_directives()
        if not directives:
            return (f'  {DIM}No standing rules. Tell me one ("always end every '
                    f'reply with the date") and it will be saved.{RESET}')

        today = time.strftime("%Y-%m-%d")
        conflicts = learning.find_conflicts(directives)
        in_conflict = {i for pair in conflicts for i in pair}
        lines = [f'  {BOLD}Standing rules ({len(directives)}){RESET}',
                 f'  {DIM}Applied to every reply, on every turn.{RESET}',
                 f'  {DIM}{"─" * 60}{RESET}']
        for i, fact in enumerate(directives, 1):
            text = (fact.get("fact") or "").replace("\n", " ")[:96]
            fid = fact.get("id", "?")
            lines.append(f'  {i}. {text}')
            notes = []
            expired = learning.stale_dates(fact.get("fact", ""), today)
            if expired:
                notes.append(f'names {", ".join(expired)}, today is {today}')
            if fid in in_conflict:
                notes.append('conflicts with another rule')
            note = f'  {YELLOW}⚠ {"; ".join(notes)}{RESET}' if notes else ''
            lines.append(f'     {DIM}{fid}{RESET}{note}')
        lines.append('')
        lines.append(f'  {DIM}Remove one with{RESET} {CYAN}/rules forget <id>{RESET}')
        return "\n".join(lines)

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


# Past this many characters a paste is shown as a marker instead of its text.
# Small enough that a stack trace or a config block collapses, large enough
# that a sentence or a path pasted mid-thought still reads as what it is.
PASTE_COLLAPSE_CHARS = 400

IMAGE_SUFFIXES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                  ".gif": "image/gif", ".webp": "image/webp"}

# Anthropic's per-image ceiling. Bigger than this is rejected outright, so
# it is better to say so than to send it and read the 400 back.
MAX_IMAGE_BYTES = 5 * 1024 * 1024


def grab_clipboard_image(dest: Path) -> bool:
    """Save the clipboard's image to `dest` as PNG. False if there isn't one.

    Clipboard image data never reaches the console's keystroke stream — a
    paste of an image delivers no characters at all — so it cannot be picked
    up by reading input, however that reading is done. .NET's clipboard is
    already on every Windows box, which beats taking a new imaging dependency
    for one call. It needs a single-threaded apartment, hence -STA.
    """
    if sys.platform != "win32":
        return False
    script = (
        "Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
        "$i=[System.Windows.Forms.Clipboard]::GetImage();"
        "if($i -ne $null){$i.Save('%s',"
        "[System.Drawing.Imaging.ImageFormat]::Png);'ok'}else{'none'}"
    ) % str(dest).replace("'", "''")
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-Command", script],
            capture_output=True, timeout=20, text=True)
        return "ok" in (out.stdout or "") and dest.exists()
    except Exception:
        return False


def put_clipboard_text(text: str) -> bool:
    """Put `text` on the clipboard. False if it could not be done.

    `clip.exe` is on every Windows box and takes its input on stdin, so there
    is nothing to quote or escape — which matters here, because the thing being
    copied is arbitrary user text full of quotes, newlines and Cyrillic. It
    writes whatever bytes it is given, so the encoding is stated outright
    rather than left to the console code page.
    """
    if sys.platform != "win32" or not text:
        return False
    try:
        proc = subprocess.run(["clip"], input=text.encode("utf-16-le"),
                              capture_output=True, timeout=10)
        return proc.returncode == 0
    except Exception:
        return False


def _image_paths_in(text: str) -> list[Path]:
    """Existing image files named anywhere in the message.

    Dragging a file onto a terminal types its path, and the clipboard grab
    below inserts one, so path-spotting covers both without a second
    mechanism. Quotes are stripped because that is how a dragged path with a
    space arrives.
    """
    found: list[Path] = []
    pattern = r'"[^"]+"|\'[^\']+\'|\S+'
    for token in re.findall(pattern, text or ""):
        candidate = token.strip('"\'')
        if Path(candidate).suffix.lower() not in IMAGE_SUFFIXES:
            continue
        try:
            path = _resolve(candidate)
            if path.is_file() and path not in found:
                found.append(path)
        except Exception:
            continue
    return found


def build_user_content(text: str):
    """The user's turn, with any images they named attached.

    Returns the plain string when there is nothing to attach, so the ordinary
    path stays exactly as it was. Images are dropped with a printed reason
    rather than silently, and never sent to a provider whose probe says it
    cannot read them — that is a 400, not a degraded answer.
    """
    paths = _image_paths_in(text)
    if not paths:
        return text
    if not getattr(_active_capabilities(), "vision", False):
        print(f'  {YELLOW}⚠{RESET}  {_get_model()} cannot read images — '
              f'sending the message as text only.')
        return text
    blocks: list[dict] = []
    for path in paths:
        try:
            data = path.read_bytes()
            if len(data) > MAX_IMAGE_BYTES:
                print(f'  {YELLOW}⚠{RESET}  {path.name} is '
                      f'{len(data) // 1024} KB, over the {MAX_IMAGE_BYTES // 1024} KB '
                      f'limit — not attached.')
                continue
            blocks.append({
                "type": "image",
                "source": {"type": "base64",
                           "media_type": IMAGE_SUFFIXES[path.suffix.lower()],
                           "data": base64.b64encode(data).decode("ascii")},
            })
            print(f'  {DIM}🖼  attached {path.name} ({len(data) // 1024} KB){RESET}')
        except Exception as e:
            print(f'  {YELLOW}⚠{RESET}  could not attach {path.name}: {e}')
    if not blocks:
        return text
    return blocks + [{"type": "text", "text": text}]


def _shift_down() -> bool:
    """Is Shift physically held right now?

    `msvcrt.getwch` hands back a decoded character and nothing about
    modifiers, so Shift+Enter and Enter are the same `\\r` to it. Reading the
    key state settles which one it was. `GetAsyncKeyState` reports the
    physical keyboard rather than the message queue, which matters because a
    console app pumps no messages — `GetKeyState` would answer from a queue
    that never advances. Cheaper and far less invasive than replacing the
    read primitive with `ReadConsoleInput`, and it fails to False anywhere
    that is not Windows, where the caller already falls back.
    """
    try:
        import ctypes
        VK_SHIFT = 0x10
        return bool(ctypes.windll.user32.GetAsyncKeyState(VK_SHIFT) & 0x8000)
    except Exception:
        return False


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
    * ``Shift+Enter``— insert a newline instead of submitting
    * ``Esc``        — clear the input line

    The buffer is drawn across as many terminal rows as it needs, so a long
    or multi-line prompt stays readable — and selectable — instead of
    scrolling out of view. Pasted text is taken in whole rather than replayed
    as keystrokes, so line breaks inside it do not submit; anything past
    ``PASTE_COLLAPSE_CHARS`` is shown as a marker and restored on send.

    Falls back to a cross-platform input with basic slash-command completion
    when msvcrt is unavailable (Linux/macOS).
    """
    try:
        import msvcrt  # Windows-only
        import sys
    except ImportError:
        return _read_input_cross_platform(prompt)

    global _history_index   # mode switching goes through set_mode()
    base_prompt = prompt

    buffer: list[str] = []
    showing = False
    selected: int | None = None  # index of the currently highlighted suggestion
    drawn_rows = 1               # rows the input block occupied on the last draw
    pastes: dict[str, str] = {}  # placeholder -> the text it stands in for
    escape_armed = False         # an Esc on an empty line; a second one exits
    # Undo history for Ctrl+Z. Snapshots are taken before a *destructive* edit
    # only — Ctrl+U, Ctrl+W, a paste, a history recall. Typing is not
    # snapshotted per character: undoing one letter at a time is useless, and
    # the keystroke that actually needs taking back is the one that wiped a
    # long prompt in a single press.
    undo_stack: list[list[str]] = []

    # Reset history navigation to past-the-end
    _history_index = len(_input_history)

    # ── mode helpers ─────────────────────────────────────────────────┬─
    def _mode_badge() -> str:
        mode = current_mode_name()
        if mode == "bypass":
            # Distinct from YOLO on sight: this one also removes the stop that
            # would otherwise interrupt a long unattended run.
            return f'[{RED}{BOLD}BYPASS ⇥{RESET}]'
        if mode == "yolo":
            return f'[{RED}YOLO{RESET}]'
        color = GREEN if mode == "auto" else YELLOW
        return f'[{color}{mode}{RESET}]'

    def _build_prompt() -> str:
        badge = _mode_badge()
        idx = base_prompt.find('TOMAS')
        if idx >= 0:
            return base_prompt[:idx] + badge + ' ' + base_prompt[idx:]
        return badge + ' ' + base_prompt

    # Delegates to the module-level `set_mode` rather than repeating the flag
    # writes. This copy had drifted from the /mode handler's — it knew nothing
    # about read_mcp_resource, and a mode added to one was invisible to the
    # other, which is exactly how bypass would have ended up half-wired.
    _set_mode = set_mode

    # ── helpers ──────────────────────────────────────────────────────────

    def _repr() -> str:
        return ''.join(buffer)

    def _checkpoint() -> None:
        """Remember the line before something destructive happens to it."""
        if undo_stack and undo_stack[-1] == buffer:
            return                       # nothing changed since the last one
        undo_stack.append(list(buffer))
        if len(undo_stack) > 50:
            undo_stack.pop(0)

    def _flash(message: str) -> None:
        """One-line feedback under the prompt, gone on the next redraw."""
        sys.stdout.write('\033[1B\r\033[2K'
                         f'  {DIM}{message}{RESET}'
                         '\033[1A\r')
        sys.stdout.flush()

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

    def _rows() -> list[str]:
        """The physical rows prompt + buffer will occupy, as drawn.

        One column is left unused so a full row never makes the terminal
        soft-wrap on its own: every line break here is one we emitted, which
        is what makes the row count below exact and the cursor arithmetic
        safe.
        """
        from text_display import hard_wrap, term_columns
        return hard_wrap(_build_prompt() + _repr(), max(1, term_columns() - 1))

    def _refresh():
        """Redraw prompt + buffer across as many rows as it needs.

        This used to be one row: `\\r\\033[K` clears only the row the cursor is
        on, so anything longer was scrolled horizontally behind a `…` and the
        rest was simply not on screen — you could not read back what you had
        typed, and terminal selection could not copy what it could not show.
        A buffer now carries real newlines (Shift+Enter, pasted text) as well,
        which one row cannot represent at all.

        Rewinding to the top of the block and clearing to the end of the
        screen (`\\033[J`) also wipes the suggestion row below, so the caller
        redraws it after; clearing per-row would leave that one behind.
        """
        nonlocal drawn_rows
        if drawn_rows > 1:
            sys.stdout.write(f'\033[{drawn_rows - 1}A')
        sys.stdout.write('\r\033[J')
        rows = _rows()
        sys.stdout.write('\n'.join(rows))
        drawn_rows = len(rows)
        sys.stdout.flush()

    def _show():
        nonlocal showing, selected
        matches = _get_matches()
        cf = _cmd_filter()

        # Mode status string (always shown in suggestion line)
        mode = current_mode_name()
        mode_str = (f'  {DIM}mode:{RESET} {mode_color(mode)}{mode}{RESET}  '
                    f'{DIM}⇧+Space:toggle  {MODE_KEYS_HINT}{RESET}')

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

        # The hint has to fit on exactly one row. With no matches it lists
        # every slash command, far wider than any terminal; left untruncated
        # it soft-wrapped onto a second row while the rewind below moved only
        # one, so the cursor came back a row low and the next redraw drew
        # *under* the previous one.
        #
        # The block is drawn first because `_refresh` clears to the end of the
        # screen — writing the hint before it would erase the hint. Coming
        # back, the last row is re-emitted rather than just moving up: that
        # leaves the cursor after the final character, where typing continues,
        # instead of at column 0.
        from text_display import shorten, term_columns
        _refresh()
        last_row = _rows()[-1]
        sys.stdout.write('\033[1B\r\033[2K')
        sys.stdout.write(shorten(text, max(1, term_columns() - 1)))
        sys.stdout.write('\033[1A\r' + last_row)
        sys.stdout.flush()
        showing = True

    def _hide():
        nonlocal showing, selected
        if showing:
            showing = False
            selected = None
            _refresh()   # clearing to end of screen takes the hint row with it

    # ── paste ────────────────────────────────────────────────────────────
    #
    # A paste is not a distinct event here: the terminal replays it as
    # ordinary keystrokes, already queued. `kbhit()` is what separates it
    # from typing — a person cannot get the next character into the queue
    # before this one has been handled, so anything already waiting arrived
    # together. That is the whole detector, and it needs no bracketed-paste
    # support from the terminal.

    def _drain() -> str:
        """Take everything already queued, as one string.

        Newlines are normalised rather than acted on: inside a paste a
        Return is a line break, not "send". The short idle re-checks cover a
        paste arriving faster than this loop empties it.
        """
        chunk: list[str] = []
        prev_cr = False
        idle = 0
        while idle < 2:
            if not msvcrt.kbhit():
                time.sleep(0.012)
                idle += 1
                continue
            idle = 0
            c = msvcrt.getwch()
            if c in ('\xe0', '\x00'):     # extended key: consume its second half
                msvcrt.getwch()
                prev_cr = False
            elif c == '\r':
                chunk.append('\n')
                prev_cr = True
            elif c == '\n':
                if not prev_cr:            # CRLF is one break, LF alone is one
                    chunk.append('\n')
                prev_cr = False
            elif c == '\t':
                chunk.append('    ')
                prev_cr = False
            elif c.isprintable():
                chunk.append(c)
                prev_cr = False
            else:
                prev_cr = False
        return ''.join(chunk)

    def _insert(text: str) -> None:
        """Add pasted text, standing in a marker for anything long.

        A pasted file is worth sending and not worth *looking* at while you
        finish the sentence around it, so past a threshold the buffer shows
        its size instead of its content. `_expand` puts the real text back at
        send time, so the model still receives all of it.
        """
        if not text:
            return
        _checkpoint()   # a paste is one keystroke's worth of damage to undo
        if len(text) >= PASTE_COLLAPSE_CHARS:
            lines = text.count('\n') + 1
            marker = (f'[#{len(pastes) + 1} pasted {len(text)} chars'
                      f'{f", {lines} lines" if lines > 1 else ""}]')
            pastes[marker] = text
            buffer.extend(marker)
        else:
            buffer.extend(text)

    def _absorb_burst(seed: str = '') -> None:
        _insert(seed + _drain())

    def _expand(text: str) -> str:
        for marker, full in pastes.items():
            text = text.replace(marker, full)
        return text

    # ── main input loop ──────────────────────────────────────────────────

    _refresh()   # draws the prompt and establishes the row count

    try:
        while True:
            # getwch returns a decoded str, so a Ukrainian, Russian,
            # accented-Latin or CJK keystroke arrives as one character
            # instead of a high byte that the old ASCII test discarded.
            ch = msvcrt.getwch()

            # Any other key disarms the exit: Esc, second thoughts, typing,
            # Esc again should not quit on the strength of the first one.
            if ch != '\x1b':
                escape_armed = False

            # ── Enter ──────────────────────────────────────────────────────
            if ch == '\r':
                # Three ways a Return can arrive, and only one of them means
                # "send". Shift+Enter is a deliberate newline. A Return in the
                # middle of a burst is a line break inside pasted text — that
                # one used to submit, so pasting three paragraphs sent three
                # half-written messages.
                if _shift_down() or msvcrt.kbhit():
                    if msvcrt.kbhit():
                        _absorb_burst()
                    else:
                        buffer.append('\n')
                    selected = None
                    _refresh()
                    _hide()
                    continue
                chosen = selected  # save before _hide() clears it
                _hide()
                sys.stdout.write('\n')
                sys.stdout.flush()
                # If a suggestion was highlighted, accept it
                if chosen is not None and _is_slash():
                    matches = _get_matches()
                    if chosen < len(matches):
                        buffer = ['/', matches[chosen]]
                result = _expand(_repr())
                # Save non-empty, non-command input to history (max 100)
                if result.strip() and not result.startswith('/'):
                    if not _input_history or _input_history[-1] != result:
                        _input_history.append(result)
                        if len(_input_history) > 100:
                            _input_history.pop(0)
                return result

            # ── Ctrl+C — copy, not quit ───────────────────────────────────
            # The terminal handles Ctrl+C as copy whenever a selection
            # exists, and only forwards \x03 here when there is none. Ending
            # the session on it made the ordinary copy reflex a coin flip:
            # miss the selection and the session was over. Esc Esc quits
            # instead, and clearing the line is the useful reading of a
            # keystroke the user meant as "copy".
            if ch == '\x03':
                if buffer:
                    _checkpoint()
                    buffer.clear()
                    selected = None
                    _hide()
                    _refresh()
                continue

            # ── Arrow / function keys ─────────────────────────────────────
            if ch in ('\xe0', '\x00'):
                # One prefix branch for both extended-key families: getwch
                # reports '\xe0' for arrows and '\x00' for function keys, and
                # the second call gives the key itself.
                ext = msvcrt.getwch()

                # F5–F9 — quick mode switch
                if ext in ('\x3f', '\x40', '\x41', '\x42', '\x43'):
                    if ext == '\x3f':      # F5 — toggle auto/default
                        _set_mode("default" if AUTO_APPROVE_LOW else "auto")
                    elif ext == '\x40':    # F6 — default mode
                        _set_mode("default")
                    elif ext == '\x41':    # F7 — strict mode
                        _set_mode("strict")
                    elif ext == '\x42':    # F8 — YOLO mode
                        _set_mode("yolo")
                    else:                  # F9 — bypass mode
                        _set_mode("bypass")
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
                        _checkpoint()   # recall replaces whatever was typed
                        if _history_index > 0:
                            _history_index -= 1
                        buffer[:] = list(_input_history[_history_index])
                        _refresh()
                elif ext == 'P':  # ↓ Down arrow — history forward
                    _hide()
                    _checkpoint()
                    if _history_index < len(_input_history) - 1:
                        _history_index += 1
                        buffer[:] = list(_input_history[_history_index])
                    else:
                        _history_index = len(_input_history)
                        buffer.clear()
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
                    # Tab outside a slash command cycles the modes, in the one
                    # order MODE_CYCLE defines — indexing it rather than
                    # branching on the flags means a new mode joins the cycle
                    # by being added to the tuple, not by editing this if/else.
                    try:
                        nxt = (MODE_CYCLE.index(current_mode_name()) + 1) % len(MODE_CYCLE)
                    except ValueError:
                        nxt = 0          # e.g. strict, which is not in the cycle
                    _set_mode(MODE_CYCLE[nxt])
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

            # ── Ctrl+W — delete the word before the cursor ────────────────
            # This buffer has no intra-line cursor, so backspace is the only
            # way to correct a long prompt one character at a time. Ctrl+W and
            # Ctrl+U are the two readline keys that pay off without one.
            if ch == '\x17':
                _checkpoint()
                while buffer and buffer[-1].isspace():
                    buffer.pop()
                while buffer and not buffer[-1].isspace():
                    buffer.pop()
                selected = None
                _refresh()
                _show() if _is_slash() else _hide()
                continue

            # ── Ctrl+U — clear the input line ─────────────────────────────
            if ch == '\x15':
                if buffer:
                    _checkpoint()
                    buffer.clear()
                    selected = None
                    _hide()
                    _refresh()
                continue

            # ── Ctrl+Z — undo the last destructive edit ───────────────────
            # Ctrl+U and Ctrl+W each destroy work in one keystroke and there
            # was no way back: a long prompt cleared by a mistyped Ctrl+U had
            # to be typed again from scratch. This restores the line as it was
            # before that press, repeatedly, back to the start of the turn.
            if ch == '\x1a':
                if undo_stack:
                    buffer[:] = undo_stack.pop()
                    selected = None
                    _hide()
                    _refresh()
                    if _is_slash():
                        _show()
                else:
                    _flash('nothing to undo')
                continue

            # ── Ctrl+Y — copy the current line to the clipboard ───────────
            # Ctrl+C cannot do this: the terminal intercepts it whenever a
            # selection exists and only forwards it here when there is none,
            # so binding copy to it would work about half the time. Paired
            # with ↑, this copies *any* earlier prompt — recall it, press
            # Ctrl+Y — which beats reselecting it with the mouse across a
            # block that may have scrolled.
            if ch == '\x19':
                text = _expand(_repr())
                if not text.strip():
                    _flash('nothing to copy')
                elif put_clipboard_text(text):
                    lines = text.count('\n') + 1
                    _flash(f'copied {len(text)} chars'
                           f'{f" · {lines} lines" if lines > 1 else ""} to clipboard')
                else:
                    _flash('could not reach the clipboard')
                continue

            # ── Ctrl+G — attach the image on the clipboard ─────────────────
            # Ctrl+V cannot carry this: the terminal owns that key and, with
            # an image on the clipboard, delivers no characters at all. The
            # grab writes a file and types its path, so the image travels the
            # same route as one dragged onto the window.
            if ch == '\x07':
                shot = Path(tempfile.gettempdir()) / f'tomas-clip-{int(time.time())}.png'
                if grab_clipboard_image(shot):
                    if buffer and not _repr().endswith(' '):
                        buffer.append(' ')
                    buffer.extend(str(shot))
                else:
                    _hide()
                    sys.stdout.write('\033[1B\r\033[2K'
                                     f'  {DIM}no image on the clipboard{RESET}'
                                     '\033[1A\r')
                selected = None
                _refresh()
                _hide()
                continue

            # ── Ctrl+L — clear the screen, keep what was typed ─────────────
            # Standard everywhere, and the honest way out of a terminal that
            # some other program has left dirty. `\033[3J` drops the scrollback
            # too, so the chat restarts from a genuinely clean screen.
            if ch == '\x0c':
                _hide()
                sys.stdout.write('\033[H\033[2J\033[3J')
                _refresh()
                if _is_slash():
                    _show()
                continue

            # ── Escape — clear the line, then quit ────────────────────────
            # Two presses so leaving is deliberate: the first press has
            # something to do (drop what is typed) whenever the line is not
            # empty, and only an Esc on an already-empty line arms the exit.
            if ch == '\x1b':
                if buffer:
                    buffer.clear()
                    selected = None
                    escape_armed = False
                    _hide()
                    _refresh()
                    continue
                if escape_armed:
                    _hide()
                    sys.stdout.write('\n')
                    sys.stdout.flush()
                    raise KeyboardInterrupt
                escape_armed = True
                _hide()
                _refresh()
                sys.stdout.write(f'\033[1B\r\033[2K  {DIM}press Esc again to '
                                 f'exit{RESET}\033[1A\r' + _rows()[-1])
                sys.stdout.flush()
                continue

            # ── Printable — any script ────────────────────────────────────
            # `ch.isprintable()` is true for 'a', 'П', 'ї', 'é', '日' and an
            # emoji, and false for control characters. The old test was
            # `32 <= ch[0] < 127` against a *byte*, which silently discarded
            # every Cyrillic keystroke — you could not type Ukrainian or
            # Russian into the prompt at all.
            if ch.isprintable():
                # Anything already queued behind this character arrived with
                # it. Taking the whole burst at once is also what keeps a big
                # paste from redrawing the screen once per character.
                if msvcrt.kbhit():
                    _absorb_burst(ch)
                else:
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
    mode_name = current_mode_name()
    if mode_name == "bypass":
        mode_display = f'{RED}{BOLD}BYPASS ⇥{RESET}'
    elif mode_name == "yolo":
        mode_display = f'{RED}{BOLD}YOLO ⚡{RESET}'
    else:
        mode_display = f'{mode_color(mode_name)}{mode_name}{RESET}'
    print(f'  {DIM}Mode:{RESET}      {mode_display}')
    if mode_name == "bypass":
        print(f'  {DIM}           all tools auto-approved · never asks to '
              f'continue · up to '
              f'{MAX_TOOL_CALLS_PER_TURN * (MAX_AUTO_CONTINUATIONS + 1)} '
              f'tool calls per turn{RESET}')
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
        TOOL_TOKENS = estimate_tool_tokens(COMBINED_TOOLS)
    except Exception as e:
        print(f'  {RED}⚠{RESET}  MCP initialization failed: {e}')
        mcp_manager = MCPManager()
        ALL_TOOLS = list(TOOLS)
        COMBINED_TOOLS = TOOLS
        MCP_TOOL_NAME_MAP = {}
        TOOL_TOKENS = estimate_tool_tokens(COMBINED_TOOLS)

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

    print(f'  {DIM}─── Type {RESET}{BOLD}quit{RESET}{DIM} or {RESET}{BOLD}exit{RESET}{DIM} to leave · {RESET}{BOLD}/help{RESET}{DIM} for commands · {RESET}{BOLD}Esc Esc{RESET}{DIM} also exits ───{RESET}')
    print(f'  {DIM}    {RESET}{BOLD}Shift+Enter{RESET}{DIM} newline · paste keeps its line breaks · '
          f'{RESET}{BOLD}Ctrl+G{RESET}{DIM} attach clipboard image{RESET}')
    print(f'  {DIM}    {RESET}{BOLD}Ctrl+Y{RESET}{DIM} copy the line ({RESET}{BOLD}↑{RESET}{DIM} first for an '
          f'earlier one) · {RESET}{BOLD}Ctrl+Z{RESET}{DIM} undo a clear or paste{RESET}')
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
                # Regular user message, plus any images it names.
                messages.append({"role": "user",
                                 "content": build_user_content(user_input)})
            # Release old tool results before considering compaction: pruning
            # is cheap and reversible (the model can re-read a file), while
            # compaction spends a model call and rewrites the whole transcript.
            # Doing it in this order means a session often no longer needs to
            # compact at all.
            messages = maybe_prune(messages)
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