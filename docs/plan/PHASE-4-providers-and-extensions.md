# Phase 4 — Providers and Extensions

> **Status: implemented (2026-08-03).** All 9 acceptance criteria pass. Test
> count 184 -> 258 (`tests/test_providers.py` 55 new, `tests/test_skills_format.py`
> 19 new); `python test_agent.py` 39/39; `python -m tests.simulate checks --offline`
> 41/41 with 0 warnings. Verified against **live OpenCode Zen** end to end:
> blocking, incremental streaming (12 deltas over 100ms), and a tool round-trip,
> all through `OpenAICompatAdapter` with no daemon. Implementation notes follow
> each item under **Implemented**.

**Goal:** connect almost any provider reliably, and make MCP + skills a single coherent extension story.
**Effort:** 1-2 weeks.
**Depends on:** Phase 2 (provider logic must live outside the TUI).

---

## Part A — Providers

### P4-1 · Provider logic is trapped in the TUI

**Severity: HIGH (architectural)**

Every provider function lives in `agent_cli.py`: `_load_providers_config` (`:304`), `_save_providers_config` (`:315`), `_activate_provider` (`:338`), `_save_provider_config` (`:373`), `_detect_provider` (`:1523`), `_detect_provider_from_config` (`:1556`), `_provider_model_entries` (`:1577`), `_update_provider_model` (`:1738`).

Consequences: `agent.py` cannot switch providers; a headless run cannot; the desktop app would have to reimplement all of it; and none of it is testable without the TUI.

**Fix — extract `provider_manager.py`:**

```python
# provider_manager.py
@dataclass
class Provider:
    name: str
    type: str                      # anthropic | openai | openrouter | zen | google | ollama | custom
    base_url: str
    api_key_env: str = "ANTHROPIC_API_KEY"
    model: str = ""
    extra_headers: dict = field(default_factory=dict)
    capabilities: "Capabilities" = field(default_factory=lambda: Capabilities())


def list_providers() -> list[Provider]: ...
def get_active() -> Provider | None: ...
def activate(name: str) -> bool: ...        # moved from agent_cli._activate_provider
def save(provider: Provider) -> None: ...
def detect_type(base_url: str, model: str) -> str: ...
def probe(provider: Provider) -> "Capabilities": ...
```

`agent_cli.py` keeps only the *pages* — the menus that call these functions. Everything above is UI-free and importable from `agent.py`, a test, or the desktop app.

**Implemented.** `provider_manager.py` — `Provider`/`Capabilities` dataclasses, `list_providers`, `get`, `get_active`, `save`, `remove`, `activate`, `detect_type`, `probe`, `probe_and_persist`, `persist_capabilities`, `list_models`, `detect_ollama`. No `agent_cli` import, no `msvcrt`. The `.env` primitives moved here too and `agent_cli` re-exports them, so config persistence has one implementation rather than two.

Note the config path move (`~/.tomas/providers.json`) is Phase 1, P1-3 — that should already be done.

### P4-2 · Capabilities are guessed by string-matching

**Severity: HIGH — this is the root cause of the Phase 0 streaming disaster**

Today the agent infers provider behaviour from substrings:

```python
# agent.py:2307 — the tool-count ceiling, decided by a substring in the model name
is_free_tier = ("free" in model_name
                or "openrouter" in os.environ.get("ANTHROPIC_BASE_URL", "").lower()
                or "127.0.0.1" in os.environ.get("ANTHROPIC_BASE_URL", ""))
max_allowed = 32 if is_free_tier else 128
```

```python
# agent_cli.py:1523 — the provider type, decided by substrings in the URL
if "openrouter" in base: return "openrouter"
if "opencode" in base or "127.0.0.1:6446" in base: return "zen"
```

Any self-hosted or unrecognised endpoint falls through to `"other"` and loses model lists, context windows and quirk handling. A model named `my-free-model` gets its tool budget cut by 75% because of the word "free".

The differences that actually break agents are not in the URL:

```python
@dataclass
class Capabilities:
    streaming: bool = True
    tool_use: bool = True
    parallel_tool_calls: bool = True
    system_prompt: bool = True
    prompt_caching: bool = False
    vision: bool = False
    context_window: int = 128_000
    max_tools: int = 128
    max_output_tokens: int = 4096
    probed_at: float = 0.0
```

**Fix:**

1. Store `type` explicitly in `providers.json` (the field already exists — `_detect_provider` checks it first at `:1526-1531`, then falls back to sniffing). Make sniffing a **last resort only**, used at first configuration, never at runtime.
2. Store `Capabilities` per provider, persisted.
3. **Probe on first connect.** You already have most of this logic scattered around — `_fetch_model_context_window` (`agent.py:182`) queries `/v1/models`. Consolidate it:

```python
def probe(provider: Provider) -> Capabilities:
    """Discover what this provider can actually do. Runs once, then cached.
    Every check degrades to the safe default on failure."""
    caps = Capabilities()
    caps.context_window = _probe_context_window(provider) or caps.context_window
    caps.streaming = _probe_streaming(provider)      # one tiny streamed request
    caps.tool_use = _probe_tool_use(provider)        # one tiny tool round-trip
    caps.max_tools = _probe_tool_ceiling(provider)   # binary search, or known table
    caps.probed_at = time.time()
    return caps
```

A ~5-second probe at provider setup replaces every string-matching heuristic in the codebase — and `_probe_streaming` would have caught the Phase 0 bug at configuration time instead of in front of the user.

**Implemented.** `probe()` measures context window (`/v1/models`, several field spellings), streaming (it checks the response really is an event stream), tool use, and system-prompt support. Each check degrades to the optimistic default on transport failure, so probing a dead endpoint returns usable capabilities instead of blocking setup. `agent.tool_ceiling()` replaced `is_free_tier_model()` on the runtime path — the old function survives only because older tests pin its behaviour. An unprobed provider still gets the documented ceiling for its type. Sniffing now happens once, in the Custom/Other setup page, and the answer is written to `providers.json`.

### P4-3 · Degrade, never fail

**Severity: HIGH (design principle)**

The Phase 0 streaming disaster was exactly this failure: a provider could not stream, so the agent *died* rather than falling back. With capability records, "this provider cannot stream" is **data**, not an exception:

```python
def call_model(state, **kw):
    caps = state.provider.capabilities
    if caps.streaming and state.want_streaming:
        try:
            return stream_call(state, **kw)
        except (NotImplementedError, ProviderStreamError):
            caps.streaming = False       # learn it, persist it
            persist_capabilities(state.provider)
    return blocking_call(state, **kw)
```

Apply the same pattern to every capability: no tool use → describe tools in the system prompt and parse a text protocol; no system-prompt support → prepend it as the first user message; tool ceiling exceeded → select fewer tools (Part B). **A capability the provider lacks should cost the user a feature, never the session.**

**Implemented.** `agent.degrade_capability(field)` narrows the capability *and persists it*, so the discovery is paid once rather than every session; the streaming fallback already in `core/loop.py` now calls it. `build_state` routes all three gaps: no streaming -> blocking; no tool use -> `describe_tools_as_text()` plus a ```tool_call protocol driven by `_run_text_protocol` (permission-gated and logged like native calls); no system role -> the prompt is prepended as the first user message.

### P4-4 · Fold `zen_proxy` into the provider layer

**Severity: MED**

`zen_proxy.py` is 645 lines running a **separate HTTP daemon** on port 6446 to translate Anthropic↔OpenAI. It works, but the shape is wrong: a background process with its own lifecycle, its own failure modes, its own port conflicts, auto-started from `agent.py:160-179`, and zero test coverage despite producing the most production failures found in the QA pass.

The translation logic itself (`anthropic_to_openai` `:250`, `openai_to_anthropic` `:331`) is good and worth keeping. What is unnecessary is the HTTP hop.

**Fix:** make it an in-process adapter:

```python
class OpenAICompatAdapter:
    """Speaks the agent's Anthropic-shaped interface, talks OpenAI wire format.
    Same translation as zen_proxy, minus the daemon."""
    def create(self, **kw) -> AnthropicShapedResponse:
        oai = anthropic_to_openai(kw)
        raw = self._post(oai)
        return openai_to_anthropic(raw, kw["model"], estimate_tokens(kw))

    def stream(self, **kw) -> Iterator[AgentEvent]:
        """True incremental streaming — read upstream SSE line by line and
        re-emit, instead of Phase 0's replay-after-completion stand-in."""
```

This also delivers **real** streaming: Phase 0 synthesises SSE frames from a completed response (a deliberate stopgap); an in-process adapter can forward tokens as they arrive.

Keep the standalone proxy available as an optional mode — it is genuinely useful for pointing *other* tools at Zen — but stop making the agent depend on it.

**Implemented.** `openai_adapter.py` presents `client.messages.create/stream` and speaks OpenAI wire format underneath, reusing `zen_proxy`'s translation with no daemon. Streaming is genuinely incremental — upstream SSE is read line by line, and fragmented tool-call arguments are reassembled across frames. A non-streaming endpoint raises `ProviderStreamError`, which the core already treats as recoverable. `_get_client()` picks the adapter for OpenAI-wire providers; `_ensure_zen_proxy()` is now opt-in behind `TOMAS_ZEN_PROXY=1`.

### P4-5 · First-class local models

**Severity: MED — strategically important**

Ollama and llama.cpp are OpenAI-compatible, free, local, and the natural default for a desktop app's offline mode. With `OpenAICompatAdapter` in place this is mostly configuration:

- Detect a local Ollama at `http://localhost:11434/v1` during provider setup and offer it.
- Populate the model list from `/api/tags`.
- Probe capabilities — many local models have no tool support, which is exactly what P4-3's degradation path is for.
- Expect small context windows; make sure compaction respects the probed value rather than a default.

**Implemented.** `detect_ollama()` plus a first-class *Ollama (local)* entry in the provider menu that says whether a server is running and how many models it found, lists them from `/api/tags`, probes on selection, and reports context window / tool use / streaming before the first turn. An unprobed Ollama provider defaults to an 8192-token window rather than the cloud default.

### P4-6 · Test against real providers

The QA pass found provider bugs that no mock would have caught. Add a matrix that runs **one tool round-trip per configured provider**:

```python
@pytest.mark.parametrize("provider", configured_providers())
def test_provider_completes_a_tool_round_trip(provider):
    """The single most valuable test in the suite: it is exactly what
    was broken for every provider before Phase 0."""
    reply = run_one_turn(provider, "Use list_files on the current directory.")
    assert reply and not reply.startswith("I'm sorry")
```

Mark it `slow`, skip providers without credentials, run it before every release.

**Implemented.** `tests/test_providers.py::TestRealProviders` runs a real tool round-trip against every configured provider, gated behind `TOMAS_TEST_PROVIDERS=1` and skipped otherwise.

Running it for real found three defects that every stub had passed:

1. **The request body carried no `model`.** `anthropic_to_openai` returns only
   `messages` — the proxy always set `model` and `max_tokens` itself. Live Zen
   answered `401 Model {{model}} is not supported`. Fixed in `_build_body()`; the
   stubs now reject a body without a model, so this cannot pass again.
2. **Response blocks were attribute-only.** The loop appends `response.content`
   into `messages`, so the *next* request re-translates those blocks with
   `.get()`. The turn after any tool call died with `'_Block' object has no
   attribute 'get'`. `_Block` is now a dict subclass, readable both ways.
3. **A 429 was recorded as a missing capability.** Hitting the free-tier rate
   limit during a streamed call wrote `streaming: false` into the provider
   config — permanently disabling streaming over a temporary condition.
   `AgentState.streaming_error_retryable` now separates "the endpoint was busy"
   from "the endpoint cannot stream", and only the latter is persisted.
   `/provider probe` re-measures on demand.

A rate-limited provider now **skips** with the reason attached rather than
failing the gate — being throttled says nothing about whether the integration
works. `AgentState.last_error` carries the reason out of the core so the record
reads `turn_error: HTTP 429 ...` instead of a bare `empty_reply`. `tests/stub_provider.py` supplies endpoints that reproduce *misbehaviour* — refuses to stream, rejects tools, streams fragmented tool arguments — since that is the class of bug no mock caught before.

---

## Part B — Extensions

### P4-7 · Do not build a plugin system

There are **zero** occurrences of "plugin" anywhere in the code or docs today, and that is the right number. MCP *is* the plugin system: it is already supported, it has an ecosystem, and it is a standard the big agents share.

A bespoke plugin API would mean a second extension mechanism to document, secure, sandbox, version and maintain — precisely the kind of thing that turns a small agent into a large one. If someone asks for "plugins", the answer is: **write an MCP server, or write a skill.** Two mechanisms, both already supported, both portable to other agents.

The work is not building a third mechanism. It is making the two you have work properly.

**Implemented — by not doing it.** No plugin API was added. A search for "plugin" across the source still returns nothing but `calculator_plugins.py`, which is unrelated sample code.

### P4-8 · Tool selection instead of arbitrary truncation

**Severity: HIGH**

```python
# agent.py:2310-2316
if len(combined) > max_allowed:
    keep = max(0, max_allowed - n_builtin)
    COMBINED_TOOLS = TOOLS + mcp_tools[:keep]      # ← whatever happened to be first
```

Measured in the QA pass: **88 of 110 MCP tools dropped**, selected by list order — which is really server connection order. The model is told nothing; only a console warning is printed (`:2315`). Whether the agent can browse the web today depends on which MCP server happened to connect first.

**Fix — relevance-based selection, reusing Phase 3's retrieval:**

```python
def select_tools(all_tools: list[dict], context: str, budget: int) -> list[dict]:
    """Always keep built-ins; fill the remaining budget with the MCP tools
    most relevant to what the user is actually doing."""
    builtins = [t for t in all_tools if t["name"] in BUILTIN_NAMES]
    mcp = [t for t in all_tools if t["name"] not in BUILTIN_NAMES]
    remaining = max(0, budget - len(builtins))

    scored = [(relevance(f"{t['name']} {t.get('description','')}", context), t)
              for t in mcp]
    chosen = [t for _, t in sorted(scored, key=lambda x: -x[0])[:remaining]]
    return builtins + chosen
```

`context` is the current user message plus the session purpose. Same scoring function as `recall()` — **one mechanism, two uses**.

Two refinements worth having:

- **Re-select per turn, not per session.** A user who starts with file edits and moves to browser automation should get browser tools when they ask for them.
- **Tell the model what it does not have.** One line in the system prompt — *"Additional tools from servers X, Y are available but not currently loaded; say so if you need them"* — turns a silent capability gap into a recoverable one.

**Implemented.** `select_tools()` scores MCP tools against the turn's keywords with `tool_relevance()` (name matches weighted double), reusing `learning.text`; ties break on original order so a contextless turn is stable rather than arbitrary. `ALL_TOOLS` holds everything discovered, and `tools_for_turn()` re-selects per turn from the last real user message plus the session purpose. `withheld_tools_notice()` appends the gap notice, naming the servers.

### P4-9 · MCP: resources and prompts, not just tools

`mcp_manager` surfaces tools only. The MCP spec also defines **resources** (files, DB rows, docs the server exposes for reading) and **prompts** (server-provided templates). A lot of the ecosystem's value is there, and both are cheap additions on top of the existing JSON-RPC plumbing (`_stdio_call` `:291`, `_http_call` `:345`):

- `resources/list` + `resources/read` → expose as a `read_mcp_resource` tool, or inject small resources directly into context.
- `prompts/list` + `prompts/get` → surface as slash commands, the same way skills already are.

Also fix the latent collision noted in the QA pass: `MCPManager.call_tool` (`:398`) dispatches to the **first server** whose tool list contains the name. Two servers exposing `search` will silently shadow each other. Prefix or qualify names per server, keeping a display alias.

**Implemented.** `MCPServer.resources` / `.prompts` are discovered on connect via `resources/list` and `prompts/list` (both optional in the spec, so a server without them still connects). `read_resource` / `get_prompt` reach them, and `_result_text()` handles all three result shapes (`content`, `contents`, `messages`) in one extractor. Surfaced as the `read_mcp_resource` tool — call it with no arguments to list — and the `/mcp-prompt` and `/mcp-resources` commands. An ambiguous URI or prompt name is reported rather than silently resolved to whichever server connected first.

The cross-server collision itself was fixed earlier, in Phase 6 (P6-7): `MCPManager._owner` maps exposed name -> (server, original), the second claimant of a name is exposed as `mcp_<server>_<tool>`, and the payload can no longer contain duplicate names.

### P4-10 · One skill format everywhere

After Phase 1, skills come from four directories including the learned one. Make the contract uniform:

```markdown
---
name: ps-file-ops
description: How this user prefers file operations on Windows
triggers: ["file", "directory", "powershell"]      # used by retrieval
source: learned | user | bundled
version: 2
---

Body — concrete, specific guidance.
```

With this, three things become the same code path:

- the user installs a skill,
- the agent generates a skill (Phase 3),
- the agent **improves an existing skill** — bump `version`, append to the body, keep provenance.

That last one is the "enhance already existing skills from interaction with the user" requirement, and it costs almost nothing once the format is uniform and the learned directory is discoverable.

Two guards: `discover_skills` must not crash on a malformed frontmatter block from a hand-written skill (validate, skip, warn), and skills must be listed by name/description in the prompt with **bodies loaded on demand** — never all bodies at once (`load_skills_content`, deleted in Phase 1, did exactly that).

**Implemented.** `validate_frontmatter()` normalises `triggers`/`source`/`version` and *returns* problems instead of raising, so a broken hand-written skill is reported and still usable. `render_frontmatter()` / `write_skill()` are the one writer. `Skill` is a dict whose `content` reads from disk on first access, so discovery reads only the first 4 KB of each file. `improve_skill()` appends under a dated `## Learned` heading, bumps `version`, and keeps `source` and `triggers`; `learning/reflect.write_learned_skill` routes through it, so a second lesson for an existing skill extends it rather than overwriting it. The display name is the declared name or the file stem, never the raw filename.

---

## Verification

```powershell
# capability probe against every configured provider
.venv\Scripts\python.exe -c "import provider_manager as pm; [print(p.name, pm.probe(p)) for p in pm.list_providers()]"

# tool round-trip matrix
.venv\Scripts\python.exe -m pytest tests/test_providers.py -m slow
```

Manual checks:

1. Configure a provider that cannot stream → the agent must fall back on the **first** attempt and persist `streaming=False`.
2. Configure a local Ollama model with no tool support → the agent degrades to text mode instead of erroring.
3. With 110 MCP tools loaded and a 32-tool budget, ask a browser question → browser tools must be among those selected.
4. Simulate an update → provider config and probed capabilities both survive.

## Acceptance criteria

- [ ] `provider_manager.py` exists; `agent.py` and tests can switch providers without importing `agent_cli`.
- [ ] No runtime behaviour depends on substring-matching a URL or model name.
- [ ] Capabilities are probed once, persisted, and used for streaming, tool use, context window and tool ceiling.
- [ ] Every capability gap degrades to a working fallback; none raises to the user.
- [ ] Tool selection is relevance-based and re-evaluated per turn; the model is told when tools are withheld.
- [ ] MCP resources and prompts are reachable.
- [ ] Cross-server MCP tool-name collisions are impossible.
- [ ] One skill format across bundled, user-installed and learned skills.
- [ ] A local Ollama model works end to end.

## Next

Phase 5 — [the desktop app](PHASE-5-desktop-app.md).
