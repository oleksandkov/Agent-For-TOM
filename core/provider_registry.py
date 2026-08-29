"""One record per provider. Everything else derives from it.

A provider used to be six edit sites, two of which matched *by list position*:
`PROVIDER_TYPES`, `OPENAI_WIRE_TYPES` and `KNOWN_TOOL_CEILINGS` in
`provider_manager`, an ordered substring chain in `detect_type`, and in
`agent_cli` both a `PROVIDER_TYPE_TO_DETECT`/`PROVIDER_LABELS` pair and the
parallel `provider_names` / `provider_name_types` lists an `elif idx == N`
chain indexes into.

Getting five of the six is not hypothetical. `groq` was a menu row, a label, a
detect-map entry, a live model picker and a save branch — and was absent from
`PROVIDER_TYPES`, so `Provider(type='groq').speaks_openai_wire` was False, the
OpenAI adapter refused it, and every Groq call fell through to the Anthropic
SDK to post Anthropic-shaped bodies at `api.groq.com`. In the same file
`_provider_model_entries` called `provider_manager.openrouter_catalog`, which
does not exist.

So a provider is data here, and the six sites read it. Same reasoning as
`core/features.py` — *the TUI renders FEATURES rather than keeping its own
list, so a switch cannot be added to the file and forgotten in the menu* — and
the same purity contract: no I/O, no network, no TUI, importable on its own.
Catalogue fetchers are named here as strings and resolved by the layer that is
allowed to make network calls.

Two rules worth stating because they are load-bearing:

**Detection matches on host, never on substrings of the whole URL.**
`detect_type('https://api.groq.com/openai/v1')` returned `'openai'`, because
`"openai"` appears in Groq's own path. A host is a fact; a substring is a
coincidence. `HOSTS` is built from the specs themselves, so a new provider
cannot be added to the list and forgotten in detection.

**A key env name belongs to the provider.** Everything used to be crammed into
`ANTHROPIC_API_KEY`, which works right up until two providers are configured
at once.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

#: Menu groups, in the order a first-run picker should show them. "free" first
#: because a user with no key and no opinion needs somewhere to land.
GROUPS = ("free", "local", "major", "aggregator", "enterprise", "other")

#: The two wire formats. `anthropic` goes through the Anthropic SDK,
#: `openai` through `openai_adapter`.
WIRES = ("openai", "anthropic")

#: How a key is carried. `query_key` is Gemini's *native* listing endpoint,
#: which 401s on a bearer token — "Expected OAuth 2 access token" — and is why
#: this is a field rather than an assumption.
AUTH_STYLES = ("bearer", "x-api-key", "query_key", "none")


@dataclass(frozen=True)
class ProviderSpec:
    """What one provider is. Frozen: specs are read, never edited at runtime."""

    id: str
    label: str
    group: str = "other"
    wire: str = "openai"

    #: Default endpoint. Empty means the user supplies it (custom), or the
    #: runtime resolves it (zen, whose stored base is conventionally a local
    #: proxy port that only ever meant "reach Zen").
    base_url: str = ""
    base_url_editable: bool = False

    api_key_env: str = ""
    #: Other env names that hold the same key. Read on first run so a
    #: developer who already exports GOOGLE_API_KEY is offered Google without
    #: typing anything.
    key_aliases: tuple = ()
    #: Shown in the prompt so a pasted key can be sanity-checked before a
    #: round trip. Never used to decide behaviour.
    key_hint: str = ""
    signup_url: str = ""

    auth: str = "bearer"
    #: Headers every request to this endpoint needs. Groq is behind
    #: Cloudflare and answers a bare bearer with 403 error 1010 — measured —
    #: so a User-Agent here is not decoration, it is the difference between
    #: working and not.
    extra_headers: dict = field(default_factory=dict)

    #: Name of the catalogue fetcher, resolved by `provider_manager`. Not a
    #: callable: this module must stay importable without the network layer.
    #: "" means the endpoint has no listing worth reading.
    models: str = "openai_list"
    static_models: tuple = ()

    tool_ceiling: int = 128
    #: Declared, endpoint-level quirks. Per-*model* quirks are learned from
    #: the endpoint's own 4xx and persisted into Capabilities instead — a name
    #: table would rot the way MODEL_CONTEXT_MAP did.
    quirks: frozenset = frozenset()

    #: True when a key with no card attached can call it. Says nothing about
    #: how much: free-tier limits change monthly, so the amount is learned
    #: from the upstream's own 429, never typed here.
    free_tier: bool = False
    local: bool = False
    prompt_caching: bool = False

    #: How far this spec has been proven, and therefore how much the menu is
    #: entitled to claim for it.
    #:
    #:   "live"  — catalogue read, capabilities probed, a tool round trip and
    #:             a stream completed against the real endpoint, with a real
    #:             key, on the date in `notes`.
    #:   "stub"  — the request it builds has the right URL, auth carrier and
    #:             headers (L1, offline). Everything reaches at least here,
    #:             because the L0/L1 tests run over every spec.
    #:
    #: A row that is not "live" says so. That is the whole difference between
    #: this and the OpenAI entry that sat in the menu for months unable to
    #: make a call, with a yellow disclaimer standing in for the fix — the
    #: label was confident and the behaviour was broken. Here the uncertainty
    #: is on the row, and being honest about it is what lets the provider be
    #: offered at all instead of hidden until someone has a key.
    verified: str = "stub"

    notes: str = ""

    @property
    def host(self) -> str:
        """The host:port `detect_id` matches on. Empty when there is no fixed one.

        The port is part of the identity, and for the local runtimes it is
        the *whole* of it: Ollama, LM Studio, llama.cpp, vLLM and Jan are all
        `localhost`, distinguished only by 11434 / 1234 / 8080 / 8000 / 1337.
        Dropping it — which `urlparse().hostname` does — collapsed all six
        into one and made detection a coin toss.

        A remote spec has no explicit port, so this is just its hostname and
        nothing changes for it.
        """
        if not self.base_url:
            return ""
        try:
            parsed = urlparse(self.base_url)
            hostname = (parsed.hostname or "").lower()
            if not hostname:
                return ""
            return f"{hostname}:{parsed.port}" if parsed.port else hostname
        except ValueError:
            return ""

    @property
    def needs_key(self) -> bool:
        return bool(self.api_key_env) and not self.local

    @property
    def key_env_names(self) -> tuple:
        """Every env name that could hold this provider's key, best first."""
        return tuple(n for n in (self.api_key_env, *self.key_aliases) if n)


# ══════════════════════════════════════════════════════════════════════
#  The specs
# ══════════════════════════════════════════════════════════════════════
#
# Only providers that are wired and verified live here. A spec is a promise
# that the menu row leads somewhere, so an unverified endpoint is added when
# its conformance run passes, not when its URL is guessed.

SPECS: tuple = (
    ProviderSpec(
        id="zen",
        verified="live",
        label="OpenCode Zen",
        group="free",
        base_url="",                     # resolved by provider_manager.probe_base_url
        api_key_env="",                  # a blank key works
        signup_url="https://opencode.ai/docs/zen",
        auth="none",
        models="zen_catalog",
        tool_ceiling=32,                 # free-tier payload limit, measured
        free_tier=True,
        notes="Dynamic x-opencode-* headers are built per request by "
              "openai_adapter.build_from_active, not stored here.",
    ),
    ProviderSpec(
        id="ollama",
        verified="live",
        label="Ollama (local)",
        group="local",
        base_url="http://localhost:11434/v1",
        base_url_editable=True,
        api_key_env="OLLAMA_API_KEY",
        key_hint="not required",
        signup_url="https://ollama.com",
        models="ollama_catalog",
        tool_ceiling=64,
        free_tier=True,
        local=True,
    ),
    ProviderSpec(
        id="google",
        verified="live",
        label="Google AI (Gemini)",
        group="free",
        # The OpenAI-compatible surface. The *native* host is what
        # google_model_catalog reads for per-model windows — only it reports
        # inputTokenLimit — and it takes the key as ?key=, not as a bearer.
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        api_key_env="GEMINI_API_KEY",
        key_aliases=("GOOGLE_API_KEY",),
        key_hint="AIza… or AQ.…",
        signup_url="https://aistudio.google.com/apikey",
        models="google_catalog",
        free_tier=True,
        notes="Requires its tool-call extra_content (thought_signature) echoed "
              "back — see OpenAICompatAdapter.preserve_tool_extras.",
    ),
    ProviderSpec(
        id="groq",
        verified="live",
        label="Groq",
        group="free",
        base_url="https://api.groq.com/openai/v1",
        api_key_env="GROQ_API_KEY",
        key_hint="gsk_…",
        signup_url="https://console.groq.com/keys",
        # Measured 2026-08-29: a bare `Authorization: Bearer …` to
        # /openai/v1/models answers 403 "error code: 1010" — Cloudflare, the
        # same block Zen sits behind. With a User-Agent it answers 14 models.
        # Without this header Groq does not work at all.
        extra_headers={"User-Agent": "tomas/1.0"},
        models="groq_catalog",
        free_tier=True,
        # Groq answers *with* a `reasoning` field and refuses to be given one
        # back. Measured 2026-08-29 against groq/compound: the reply carries
        # `reasoning`, and replaying it on the assistant turn — under either
        # spelling — is rejected with
        #
        #   400 invalid_request_error — "'messages.2' : for 'role:assistant'
        #   the following must be satisfied[('messages.2' : property
        #   'reasoning_content' is unsupported)]"
        #
        # That is the exact opposite of what DeepSeek and Zen require, and
        # `zen_proxy.anthropic_to_openai` replayed it for every OpenAI-wire
        # provider. So turn 1 answered, turn 2 posted the reasoning turn 1
        # produced, and every turn from there was a 400 — a Groq session was
        # one question long.
        quirks=frozenset({"no_reasoning_replay"}),
    ),
    ProviderSpec(
        id="openrouter",
        verified="live",
        label="OpenRouter",
        group="aggregator",
        base_url="https://openrouter.ai/api/v1",
        api_key_env="OPENROUTER_API_KEY",
        key_hint="sk-or-v1-…",
        signup_url="https://openrouter.ai/keys",
        # Optional upstream attribution. Harmless when unset, and OpenRouter
        # ranks apps by it, so it is worth sending.
        extra_headers={"HTTP-Referer": "https://github.com/tomas-agent",
                       "X-Title": "TOMAS"},
        models="openrouter_catalog",
        free_tier=True,
        notes="Free is decided by pricing.prompt == 0, never by the ':free' "
              "suffix. Measured: 396 models, 21 free by price.",
    ),
    ProviderSpec(
        id="anthropic",
        label="Anthropic Direct",
        group="major",
        wire="anthropic",
        base_url="https://api.anthropic.com",
        api_key_env="ANTHROPIC_API_KEY",
        key_hint="sk-ant-…",
        signup_url="https://console.anthropic.com/settings/keys",
        auth="x-api-key",
        models="static",
        static_models=(
            "claude-opus-4-5", "claude-sonnet-4-5", "claude-haiku-4-5",
            "claude-opus-4", "claude-sonnet-4",
            "claude-3-5-sonnet-20241022", "claude-3-5-haiku-20241022",
        ),
        prompt_caching=True,
    ),
    ProviderSpec(
        id="openai",
        label="OpenAI",
        group="major",
        base_url="https://api.openai.com/v1",
        api_key_env="OPENAI_API_KEY",
        key_hint="sk-…",
        signup_url="https://platform.openai.com/api-keys",
        models="openai_list",
    ),
    # ── Tier 1: direct, OpenAI-wire ────────────────────────────────────
    ProviderSpec(
        id="xai",
        label="xAI (Grok)",
        group="major",
        base_url="https://api.x.ai/v1",
        api_key_env="XAI_API_KEY",
        key_aliases=("GROK_API_KEY",),
        key_hint="xai-…",
        signup_url="https://console.x.ai",
        models="openai_list",
    ),
    ProviderSpec(
        id="deepseek",
        label="DeepSeek",
        group="major",
        base_url="https://api.deepseek.com/v1",
        api_key_env="DEEPSEEK_API_KEY",
        key_hint="sk-…",
        signup_url="https://platform.deepseek.com/api_keys",
        models="openai_list",
        notes="The reasoner model rejects `temperature`; that is learned from "
              "its own 400 rather than declared per model name.",
    ),
    ProviderSpec(
        id="mistral",
        label="Mistral",
        group="major",
        base_url="https://api.mistral.ai/v1",
        api_key_env="MISTRAL_API_KEY",
        signup_url="https://console.mistral.ai/api-keys",
        models="openai_list",
        free_tier=True,
        notes="Free 'experiment' tier. Known to reject `stream_options`.",
    ),
    ProviderSpec(
        id="cerebras",
        label="Cerebras",
        group="free",
        base_url="https://api.cerebras.ai/v1",
        api_key_env="CEREBRAS_API_KEY",
        key_hint="csk-…",
        signup_url="https://cloud.cerebras.ai",
        models="openai_list",
        free_tier=True,
    ),
    ProviderSpec(
        id="together",
        label="Together AI",
        group="aggregator",
        base_url="https://api.together.xyz/v1",
        api_key_env="TOGETHER_API_KEY",
        signup_url="https://api.together.ai/settings/api-keys",
        models="openai_list",
    ),
    ProviderSpec(
        id="fireworks",
        label="Fireworks AI",
        group="aggregator",
        base_url="https://api.fireworks.ai/inference/v1",
        api_key_env="FIREWORKS_API_KEY",
        key_hint="fw_…",
        signup_url="https://fireworks.ai/account/api-keys",
        models="openai_list",
    ),

    # ── Tier 2: free tiers and aggregators ─────────────────────────────
    ProviderSpec(
        id="github_models",
        label="GitHub Models",
        group="free",
        base_url="https://models.github.ai/inference",
        api_key_env="GITHUB_TOKEN",
        key_aliases=("GH_TOKEN", "GITHUB_MODELS_TOKEN"),
        key_hint="ghp_… or github_pat_…",
        signup_url="https://github.com/settings/tokens",
        models="openai_list",
        free_tier=True,
        notes="Free with any GitHub PAT. The host moved once (from "
              "models.inference.ai.azure.com) — worth re-checking if it 404s.",
    ),
    ProviderSpec(
        id="nvidia",
        label="NVIDIA NIM",
        group="free",
        base_url="https://integrate.api.nvidia.com/v1",
        api_key_env="NVIDIA_API_KEY",
        key_hint="nvapi-…",
        signup_url="https://build.nvidia.com",
        models="openai_list",
        free_tier=True,
    ),
    ProviderSpec(
        id="huggingface",
        label="Hugging Face",
        group="aggregator",
        base_url="https://router.huggingface.co/v1",
        api_key_env="HF_TOKEN",
        key_aliases=("HUGGINGFACE_API_KEY", "HUGGING_FACE_HUB_TOKEN"),
        key_hint="hf_…",
        signup_url="https://huggingface.co/settings/tokens",
        # Not `openai_list`: HF's router answers `/v1/models` with real
        # context windows, tool support and a routed-provider list per model
        # — `openai_list`'s bare-id reading threw all of it away, and the
        # menu's guessed-name fallback then offered ids the router does not
        # serve at all (`qwen/qwen-2.5-72b-instruct` — an OpenRouter slug,
        # not one of HF's, which are `Qwen/Qwen2.5-...` when present). See
        # `provider_manager.huggingface_catalog`.
        models="huggingface_catalog",
        free_tier=True,
        notes="Some models on the router are free-tier; `is_free` is read "
              "per model, not assumed account-wide the way it is for Groq.",
    ),
    ProviderSpec(
        id="cohere",
        label="Cohere",
        group="major",
        # The compatibility endpoint, not the native API. The native one
        # speaks neither wire format this agent knows.
        base_url="https://api.cohere.ai/compatibility/v1",
        api_key_env="COHERE_API_KEY",
        signup_url="https://dashboard.cohere.com/api-keys",
        models="openai_list",
    ),
    ProviderSpec(
        id="perplexity",
        label="Perplexity",
        group="major",
        base_url="https://api.perplexity.ai",
        api_key_env="PERPLEXITY_API_KEY",
        key_hint="pplx-…",
        signup_url="https://www.perplexity.ai/settings/api",
        models="static",
        static_models=("sonar", "sonar-pro", "sonar-reasoning"),
        # Declared, not learned: Perplexity has no tool-calling surface at
        # all, so the first call should already know rather than discovering
        # it with a 400. This is what `quirks` is for — an endpoint-level
        # fact, as against the per-model spread Groq has.
        quirks=frozenset({"no_tools"}),
        notes="No tool calling. The agent runs on its text tool protocol "
              "here, which is slower and less reliable.",
    ),
    ProviderSpec(
        id="zai",
        label="Z.ai (GLM)",
        group="major",
        base_url="https://api.z.ai/api/paas/v4",
        api_key_env="ZAI_API_KEY",
        key_aliases=("ZHIPU_API_KEY", "GLM_API_KEY"),
        signup_url="https://z.ai/manage-apikey/apikey-list",
        models="openai_list",
    ),
    ProviderSpec(
        id="moonshot",
        label="Moonshot (Kimi)",
        group="major",
        base_url="https://api.moonshot.ai/v1",
        api_key_env="MOONSHOT_API_KEY",
        key_hint="sk-…",
        signup_url="https://platform.moonshot.ai/console/api-keys",
        models="openai_list",
    ),

    # ── Tier 3: local runtimes ─────────────────────────────────────────
    #
    # All OpenAI-compatible, so each is a row plus the reachability probe
    # Ollama already has. None needs a key, none can be rate-limited, and
    # none costs anything — which is why they are the floor of the Auto Free
    # ladder rather than peers on it.
    ProviderSpec(
        id="lmstudio",
        label="LM Studio",
        group="local",
        base_url="http://localhost:1234/v1",
        base_url_editable=True,
        api_key_env="LMSTUDIO_API_KEY",
        key_hint="not required",
        signup_url="https://lmstudio.ai",
        models="openai_list",
        tool_ceiling=64,
        free_tier=True,
        local=True,
    ),
    ProviderSpec(
        id="llamacpp",
        label="llama.cpp server",
        group="local",
        base_url="http://localhost:8080/v1",
        base_url_editable=True,
        api_key_env="LLAMACPP_API_KEY",
        key_hint="not required",
        signup_url="https://github.com/ggml-org/llama.cpp",
        models="openai_list",
        tool_ceiling=64,
        free_tier=True,
        local=True,
    ),
    ProviderSpec(
        id="vllm",
        label="vLLM",
        group="local",
        base_url="http://localhost:8000/v1",
        base_url_editable=True,
        api_key_env="VLLM_API_KEY",
        key_hint="not required",
        signup_url="https://docs.vllm.ai",
        models="openai_list",
        free_tier=True,
        local=True,
    ),
    ProviderSpec(
        id="jan",
        label="Jan",
        group="local",
        base_url="http://localhost:1337/v1",
        base_url_editable=True,
        api_key_env="JAN_API_KEY",
        key_hint="not required",
        signup_url="https://jan.ai",
        models="openai_list",
        tool_ceiling=64,
        free_tier=True,
        local=True,
    ),
    ProviderSpec(
        id="localai",
        label="LocalAI",
        group="local",
        # 8080 really is LocalAI's default, and it really does collide with
        # llama.cpp's. Moving it to 8081 to make detection tidy would be a
        # lie about the default, which costs a user a failed connection to
        # save a test an exception. `HOSTS` resolves the collision
        # first-wins, and the cost of being wrong is nil: both are
        # OpenAI-wire with the same fetcher and the same ceiling, and the
        # type is written down at configure time regardless.
        base_url="http://localhost:8080/v1",
        base_url_editable=True,
        api_key_env="LOCALAI_API_KEY",
        key_hint="not required",
        signup_url="https://localai.io",
        models="openai_list",
        tool_ceiling=64,
        free_tier=True,
        local=True,
    ),
    ProviderSpec(
        id="custom",
        label="Custom / Other",
        group="other",
        base_url="",
        base_url_editable=True,
        # Assigned when the user names the provider (`<NAME>_API_KEY`), so
        # there is no default to declare. It must not fall back to
        # ANTHROPIC_API_KEY: that is how every provider ended up sharing one
        # env var, which works right up until two are configured at once.
        api_key_env="",
        models="openai_list",
        notes="An unrecognised OpenAI-compatible endpoint is a working "
              "configuration, not a degraded one.",
    ),
)

SPEC_BY_ID: dict = {s.id: s for s in SPECS}


# ══════════════════════════════════════════════════════════════════════
#  Derivations — the six edit sites, now one
# ══════════════════════════════════════════════════════════════════════

def spec(provider_id: str) -> Optional[ProviderSpec]:
    return SPEC_BY_ID.get(provider_id)


def provider_types() -> tuple:
    return tuple(s.id for s in SPECS)


def openai_wire_types() -> tuple:
    return tuple(s.id for s in SPECS if s.wire == "openai")


def tool_ceilings() -> dict:
    return {s.id: s.tool_ceiling for s in SPECS}


def labels() -> dict:
    return {s.id: s.label for s in SPECS}


def by_group() -> dict:
    """Specs grouped for a menu, groups in GROUPS order, empty ones dropped."""
    out: dict = {}
    for group in GROUPS:
        members = [s for s in SPECS if s.group == group]
        if members:
            out[group] = members
    return out


def free_tier_types() -> tuple:
    """Providers a key with no card can call — the Auto Free candidate set."""
    return tuple(s.id for s in SPECS if s.free_tier)


#: host:port → provider id, built from the specs so detection cannot fall
#: behind the list. Specs with no fixed host (zen, custom) are absent by
#: construction and handled by `detect_id`'s special cases.
#:
#: First declared wins, which matters for exactly one pair: llama.cpp and
#: LocalAI both default to `localhost:8080`. See the note on the localai spec
#: — the ambiguity is real, and resolving it by moving a port would be a lie
#: about the default rather than a fix.
HOSTS: dict = {}
for _spec in SPECS:
    if _spec.host:
        HOSTS.setdefault(_spec.host, _spec.id)
del _spec

#: host:port values more than one local runtime legitimately defaults to.
#: Declared rather than discovered so that a *new* collision — which would be
#: a mistake — still fails the registry tests, while this one does not.
SHARED_LOCAL_PORTS: frozenset = frozenset({"localhost:8080"})


def detect_id(base_url: str, model: str = "") -> str:
    """Best guess at a provider id from its URL, matched on **host**.

    Used when adding a provider that did not declare a type. The result is
    written down immediately; nothing reads it back by sniffing at runtime.

    Host, not substring. The old chain tested `"openai" in base` and so
    classified `https://api.groq.com/openai/v1` as OpenAI — a coincidence in
    Groq's path deciding a provider's identity. It happened to work only
    because both types speak the same wire format; with Groq's required
    User-Agent it would not have.

    An unknown endpoint is "custom", which is a working configuration.
    """
    base = (base_url or "").strip().lower()
    if not base:
        return "anthropic"          # no URL has always meant Anthropic direct

    try:
        parsed = urlparse(base if "://" in base else f"http://{base}")
        hostname = (parsed.hostname or "").lower()
        host = f"{hostname}:{parsed.port}" if parsed.port else hostname
    except ValueError:
        hostname, host = "", ""

    # Conventions that predate the registry and still appear in stored
    # configs: a zen provider points at a local proxy port, and Ollama is
    # identified by its port on any host (a LAN box running `ollama serve`).
    if ":6446" in base or "opencode" in hostname:
        return "zen"
    if ":11434" in base or "ollama" in hostname:
        return "ollama"

    if host:
        if host in HOSTS:
            return HOSTS[host]
        # A port that matches a known local runtime on *any* host: a box on
        # the LAN serving LM Studio is still LM Studio.
        if ":" in host:
            port = host.rsplit(":", 1)[1]
            for known, known_id in HOSTS.items():
                if known.endswith(f":{port}"):
                    return known_id
        # Longest suffix wins, so `eu.api.mistral.ai` finds `api.mistral.ai`
        # before a shorter accidental match. Port-bearing entries are skipped
        # here — a remote host never matches a local runtime's identity.
        for known in sorted((h for h in HOSTS if ":" not in h),
                            key=len, reverse=True):
            if hostname == known or hostname.endswith("." + known):
                return HOSTS[known]

    return "custom"


def key_env_lookup() -> dict:
    """env name → provider id, for the first-run "you already have a key" scan.

    Ordered so a provider's primary name wins over another's alias. Specs that
    share `ANTHROPIC_API_KEY` (anthropic, custom) resolve to the first one
    declaring it as primary.
    """
    out: dict = {}
    for s in SPECS:
        if s.api_key_env:
            out.setdefault(s.api_key_env, s.id)
    for s in SPECS:
        for alias in s.key_aliases:
            out.setdefault(alias, s.id)
    return out
