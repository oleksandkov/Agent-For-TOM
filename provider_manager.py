"""
Provider management — UI-free.

Everything about *which* model endpoint the agent talks to and *what that
endpoint can actually do* lives here. It was previously spread across eight
functions inside `agent_cli.py`, which meant `agent.py` could not switch
providers, a headless run could not, and none of it was testable without the
terminal UI.

Two ideas carry the module:

1. **Capabilities are probed, not guessed.** The old code decided the tool
   ceiling from whether the model name contained the substring "free", and the
   provider type from whether the URL contained "openrouter". A model called
   `my-free-model` lost 75% of its tool budget for its name. Sniffing is now a
   last resort used at first configuration, never at runtime.

2. **A capability the provider lacks costs a feature, never the session.**
   Capabilities are data. "This endpoint cannot stream" is a field to read and
   route around, not an exception to propagate.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from core import provider_registry as registry

TOMAS_DIR = Path.home() / ".tomas"
PROVIDERS_CONFIG_PATH = TOMAS_DIR / "providers.json"
ENV_FILE = TOMAS_DIR / ".env"

# Types we know how to talk to. "custom" is a first-class answer, not a
# failure: an unrecognised OpenAI-compatible endpoint should work.
#
# Derived, not typed. These three tuples and `detect_type` used to be four
# independent hand-maintained lists, and `groq` was in the menu, the labels,
# the detect map and the model picker while being absent from every one of
# them — so `speaks_openai_wire` was False and every Groq call went to the
# Anthropic SDK. One list now, in core/provider_registry.py, and the six
# places that used to need teaching read it instead.
PROVIDER_TYPES = registry.provider_types()

#: What the menus offer: every spec in the registry.
#:
#: This was a hand-maintained tuple, and the honesty it was protecting is now
#: carried per row by `ProviderSpec.verified` instead. That is a better place
#: for it. Hiding a provider until someone has a key to prove it with means a
#: user cannot discover the provider they would need the key *for* — and the
#: five that were hidden this way were not broken, merely unwitnessed.
#:
#: The gate itself still exists and still matters. Every spec passes L0 and
#: L1 offline before it can appear at all, and a row that has not completed a
#: live tool round trip says `unverified` on its face. What must never happen
#: again is a confident label over a provider that cannot make a call — which
#: is what the OpenAI row was for months, with a yellow disclaimer standing in
#: for the fix.
#:
#: Kept as a name because `agent_cli` and the session menus read it, and a
#: future working set — a `--only` flag, an enterprise build — has somewhere
#: to go.
VISIBLE_PROVIDER_TYPES = registry.provider_types()

# Endpoints that speak OpenAI wire format rather than Anthropic's.
#: Google is on this list because it publishes an OpenAI-compatible endpoint
#: (`/v1beta/openai/chat/completions`) that speaks the same wire format as the
#: rest — verified against it directly: native `tool_calls` come back, not
#: prose. Before this it was a provider you could *configure* and not use. The
#: setup page said so in as many words ("Google AI is saved but the agent uses
#: the ANTHROPIC_* env vars for API calls"), which is a menu entry that leads
#: nowhere wearing a disclaimer.
OPENAI_WIRE_TYPES = registry.openai_wire_types()

#: Google's OpenAI-compatible surface. Not the same host path as its native
#: API (`/v1beta/models`), which is still what `google_model_catalog` reads for
#: per-model context windows — that one reports `inputTokenLimit` and the
#: OpenAI-shaped one does not.
GOOGLE_OPENAI_BASE = "https://generativelanguage.googleapis.com/v1beta/openai"
GOOGLE_NATIVE_BASE = "https://generativelanguage.googleapis.com/v1beta"

OLLAMA_DEFAULT_URL = "http://localhost:11434/v1"

# Ollama serves this many tokens of context unless OLLAMA_CONTEXT_LENGTH says
# otherwise. Measured on 0.30.6: qwen3-vl:2b advertises a 262,144-token window
# and still loads with `num_ctx=32768` (`/api/ps`), as does gemma3:4b at
# 131,072. The OpenAI shim exposes no `num_ctx` parameter, so a model's own
# maximum is a ceiling the server will not actually allocate — reporting it
# would promise the agent a window it cannot spend.
OLLAMA_DEFAULT_NUM_CTX = 32768


# ══════════════════════════════════════════════════════════════════════
#  .env persistence (shared with agent_cli — one implementation)
# ══════════════════════════════════════════════════════════════════════

def set_env_key(path: Path, key: str, value: str) -> None:
    """Write key=value into a .env file, replacing any existing entry."""
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = content.splitlines()
    for i, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def drop_env_key(path: Path, key: str) -> bool:
    """Remove a key from a .env file. Returns True if anything was removed."""
    if not path.exists():
        return False
    lines = path.read_text(encoding="utf-8").splitlines()
    kept = [ln for ln in lines if not ln.startswith(f"{key}=")]
    if len(kept) == len(lines):
        return False
    path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
    return True


def apply_env(key: str, value: str) -> None:
    """Persist a config key to ~/.tomas/.env and apply it to this process."""
    set_env_key(ENV_FILE, key, value)
    os.environ[key] = value


def clear_env(key: str) -> None:
    """Remove a config key from ~/.tomas/.env and from this process.

    The counterpart to `apply_env`, and both halves matter: dropping the key
    from `os.environ` alone lets the next launch read the stale value straight
    back out of the file.
    """
    drop_env_key(ENV_FILE, key)
    os.environ.pop(key, None)


def env_file_keys(path: Path) -> set:
    """The keys a .env file carries, ignoring comments and blank lines."""
    if not path.exists():
        return set()
    keys = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        keys.add(line.split("=", 1)[0].strip())
    return keys


# ══════════════════════════════════════════════════════════════════════
#  Capabilities
# ══════════════════════════════════════════════════════════════════════

@dataclass
class Capabilities:
    """What a provider can actually do.

    Defaults are the optimistic case; probing only ever narrows them, and
    every field degrades to something that still works (see agent.py).
    """
    streaming: bool = True
    tool_use: bool = True
    parallel_tool_calls: bool = True
    system_prompt: bool = True
    prompt_caching: bool = False
    vision: bool = False
    context_window: int = 200_000  # standard Claude tier; probing narrows this per-model
    max_tools: int = 128
    # 32768, not 8192. Nothing probes this field — grep it — so the default is
    # what every provider actually runs with, and `agent.py` applies it as
    # `min(reserve, max_output_tokens)`, i.e. a hard ceiling ahead of whatever
    # the context budget would otherwise grant. It was 4096, then 8192 (that
    # change is the comment this replaced): a pessimistic unmeasured guess
    # capped every provider before a reasoning model got a chance to answer —
    # it spends the budget on internal reasoning, gets truncated before
    # emitting content, and the turn comes back empty or is billed twice via
    # `_escalate`'s retry. 8192 was itself still that same mistake at a
    # smaller scale: every OpenCode Zen free-tier session in a `deepseek`
    # sweep hit "No reply within 8192 output tokens" and needed the escalation
    # round-trip to reach 32768 — a number `_escalate` was already trusting as
    # safe for these providers before this default did. `core.loop`'s
    # `MAX_OUTPUT_CEILING` moved to 65536 alongside this so escalation still
    # has somewhere to go — measured live, deepseek-v4-flash-free accepts
    # max_tokens up to at least that.
    max_output_tokens: int = 32_768
    probed_at: float = 0.0

    @property
    def probed(self) -> bool:
        return self.probed_at > 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "Capabilities":
        if not data:
            return cls()
        known = {f for f in cls().to_dict()}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class Provider:
    name: str
    type: str = "custom"
    base_url: str = ""
    api_key_env: str = "ANTHROPIC_API_KEY"
    model: str = ""
    env: dict = field(default_factory=dict)
    extra_headers: dict = field(default_factory=dict)
    capabilities: Capabilities = field(default_factory=Capabilities)

    @property
    def api_key(self) -> str:
        return self.env.get(self.api_key_env) or os.environ.get(self.api_key_env, "")

    @property
    def speaks_openai_wire(self) -> bool:
        return self.type in OPENAI_WIRE_TYPES

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "base_url": self.base_url,
            "api_key_env": self.api_key_env,
            "model": self.model,
            "env": dict(self.env),
            "extra_headers": dict(self.extra_headers),
            "capabilities": self.capabilities.to_dict(),
        }

    @classmethod
    def from_dict(cls, name: str, data: dict) -> "Provider":
        env = dict(data.get("env") or {})
        base_url = data.get("base_url") or env.get("ANTHROPIC_BASE_URL", "")
        return cls(
            name=name,
            type=data.get("type") or detect_type(base_url, data.get("model", "")),
            base_url=base_url,
            api_key_env=data.get("api_key_env", "ANTHROPIC_API_KEY"),
            model=data.get("model", ""),
            env=env,
            extra_headers=dict(data.get("extra_headers") or {}),
            capabilities=Capabilities.from_dict(data.get("capabilities")),
        )


# ══════════════════════════════════════════════════════════════════════
#  Config file
# ══════════════════════════════════════════════════════════════════════

EMPTY_CONFIG = {"active": None, "providers": {}}


def load_config() -> dict:
    """Read the provider config, preserving anything unreadable.

    This used to swallow every exception and return an empty config, which is
    indistinguishable from "no providers configured". The next `save_config`
    then wrote that emptiness over the real file — one unreadable byte and
    every configured provider was gone, silently. A file that fails to parse is
    now moved aside with its contents intact, so the loss is recoverable and
    visible instead of total and quiet.
    """
    if not PROVIDERS_CONFIG_PATH.exists():
        return dict(EMPTY_CONFIG)
    try:
        raw = PROVIDERS_CONFIG_PATH.read_text(encoding="utf-8")
    except OSError:
        # Cannot read it *right now* (locked, permissions). Do not treat a
        # transient failure as "the user has no providers".
        return dict(EMPTY_CONFIG)
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        try:
            quarantine = PROVIDERS_CONFIG_PATH.with_name(
                f"providers.json.corrupt-{int(time.time())}")
            PROVIDERS_CONFIG_PATH.replace(quarantine)
        except OSError:
            pass
        return dict(EMPTY_CONFIG)
    return parsed if isinstance(parsed, dict) else dict(EMPTY_CONFIG)


def save_config(config: dict) -> None:
    """Write the provider config atomically, keeping one generation back.

    A direct `write_text` truncates the target before the new bytes land, so an
    interrupted write leaves nothing at all. This writes a sibling temp file and
    replaces, which on Windows and POSIX alike is atomic — the file is either
    the old config or the new one, never a hole.
    """
    PROVIDERS_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(config, indent=2, ensure_ascii=False)

    # Keep the previous generation. Cheap insurance against a caller that
    # passes an empty config by mistake.
    if PROVIDERS_CONFIG_PATH.exists():
        try:
            backup = PROVIDERS_CONFIG_PATH.with_name("providers.json.bak")
            backup.write_text(
                PROVIDERS_CONFIG_PATH.read_text(encoding="utf-8"),
                encoding="utf-8")
        except OSError:
            pass

    tmp = PROVIDERS_CONFIG_PATH.with_name(f"providers.json.tmp{os.getpid()}")
    try:
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(PROVIDERS_CONFIG_PATH)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def list_providers() -> list[Provider]:
    config = load_config()
    return [Provider.from_dict(name, data)
            for name, data in (config.get("providers") or {}).items()]


def visible_providers() -> list[Provider]:
    """Configured providers whose type is in the current working set.

    A presentation-only filter — `list_providers()` stays complete, so a
    provider configured outside `VISIBLE_PROVIDER_TYPES` (e.g. from before
    this build, or a hand-edited config) keeps working via `get()`/
    `get_active()`/`activate()`; it just does not resurface in a menu.
    """
    return [p for p in list_providers() if p.type in VISIBLE_PROVIDER_TYPES]


def get(name: str) -> Optional[Provider]:
    config = load_config()
    data = (config.get("providers") or {}).get(name)
    return Provider.from_dict(name, data) if data is not None else None


def get_active() -> Optional[Provider]:
    config = load_config()
    active = config.get("active")
    return get(active) if active else None


def save(provider: Provider, activate_it: bool = True) -> None:
    """Persist one provider, optionally making it the active one."""
    config = load_config()
    config.setdefault("providers", {})[provider.name] = provider.to_dict()
    if activate_it:
        config["active"] = provider.name
    save_config(config)


def remove(name: str) -> bool:
    config = load_config()
    providers = config.get("providers") or {}
    if name not in providers:
        return False
    del providers[name]
    if config.get("active") == name:
        config["active"] = None
    save_config(config)
    return True


def persist_capabilities(provider: Provider) -> None:
    """Write back what we learned about a provider mid-session.

    This is the other half of 'degrade, never fail': the first time a
    provider refuses to stream we route around it *and remember*, so the next
    session does not pay for the same discovery.
    """
    config = load_config()
    entry = (config.get("providers") or {}).get(provider.name)
    if entry is None:
        return
    entry["capabilities"] = provider.capabilities.to_dict()
    save_config(config)


# ══════════════════════════════════════════════════════════════════════
#  Type detection — first configuration only, never at runtime
# ══════════════════════════════════════════════════════════════════════

def detect_type(base_url: str, model: str = "") -> str:
    """Best guess at a provider type from its URL.

    Used when adding a provider that did not declare a type. The result is
    written to providers.json immediately; nothing reads it back by sniffing
    at runtime. An unknown endpoint is "custom" — a working configuration,
    not a degraded one.

    Matched on **host**, by the registry. The chain that used to live here
    tested `"openai" in base` and so classified
    `https://api.groq.com/openai/v1` as OpenAI — a coincidence in Groq's own
    path deciding a provider's identity.
    """
    return registry.detect_id(base_url, model)


# ══════════════════════════════════════════════════════════════════════
#  Probing
# ══════════════════════════════════════════════════════════════════════

# Ceilings that are a documented property of the endpoint rather than
# something worth spending a probe on.
KNOWN_TOOL_CEILINGS = registry.tool_ceilings()

_PROBE_TIMEOUT = 8

# A local model has to be read off disk and into VRAM before it answers, and
# that load is counted against the request that triggered it. Measured against
# Ollama 0.30.6: a plain streamed request to gemma3:4b took 15.7 s cold, and an
# image request took 14.9 s *even fully warmed*. Both blew through the 8 s
# budget, and `_probe_feature` turns a timeout into its `optimistic` default —
# so streaming was recorded as "yes" without a stream ever being seen, and
# vision as "no" for every vision-capable model on the machine. A local
# endpoint gets a budget that fits what it actually costs; a remote one keeps
# the short timeout, where a slow answer really is a sick endpoint.
_PROBE_TIMEOUT_LOCAL = 90

_LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1", "0.0.0.0")


def _is_local(url: str) -> bool:
    from urllib.parse import urlparse
    try:
        host = urlparse(url if "://" in url else f"http://{url}").hostname
    except ValueError:
        return False
    return (host or "") in _LOCAL_HOSTS


def _timeout_for(provider: Provider) -> int:
    return (_PROBE_TIMEOUT_LOCAL if _is_local(probe_base_url(provider))
            else _PROBE_TIMEOUT)


def _headers_for(provider: Provider) -> dict:
    headers = {"Content-Type": "application/json"}

    # Zen sits behind Cloudflare, which rejects a bare Authorization header
    # with 403 (error 1010) — it wants the opencode User-Agent and x-opencode-*
    # set. Probing without them answered every feature question with "no", so a
    # provider that streams, calls tools and takes a system prompt was recorded
    # as capable of none of the three, and the agent silently degraded to the
    # text tool protocol with the system prompt stuffed into a user message.
    # `openai_adapter.build_from_active` already carries these on the runtime
    # path; the probe has to agree with it or the two disagree about the same
    # endpoint.
    if provider.type == "zen":
        try:
            from zen_proxy import _oc_id, _zen_headers
            headers.update(_zen_headers(_oc_id("ses"), _oc_id("req")))
        except Exception:
            pass
        headers.update(provider.extra_headers or {})
        return headers

    # Headers the *endpoint* requires, from its spec. Groq is the reason this
    # is not optional: behind Cloudflare, a bare `Authorization: Bearer …` to
    # api.groq.com answers 403 "error code: 1010" — measured — and with a
    # User-Agent it answers normally. Applied before the key so a probe and a
    # real call cannot disagree about them, which is the same rule the zen
    # branch above already follows for its x-opencode-* headers.
    spec = registry.spec(provider.type)
    if spec:
        headers.update(spec.extra_headers)

    key = provider.api_key
    if not key:
        headers.update(provider.extra_headers or {})
        return headers
    if provider.speaks_openai_wire:
        headers["Authorization"] = f"Bearer {key}"
    else:
        headers["x-api-key"] = key
        headers["anthropic-version"] = "2023-06-01"
    headers.update(provider.extra_headers or {})
    return headers


def _reachable(url: str) -> bool:
    """Cheap gate before any HTTP call to a *local* endpoint.

    A local provider that is not installed (the common case for Ollama) used
    to cost the full `_PROBE_TIMEOUT` per attempt, and `list_models` makes
    three attempts — 12.3 s before the provider menu could draw a single row.
    Remote hosts are not gated; their reachability is the HTTP layer's job.
    """
    from net_probe import url_port_open
    return url_port_open(url)


def _get_json(url: str, headers: dict, timeout: int = _PROBE_TIMEOUT) -> Any:
    if not _reachable(url):
        raise OSError("nothing listening")
    req = urllib.request.Request(url, method="GET")
    for k, v in headers.items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def _post_json(url: str, headers: dict, body: dict,
               timeout: int = _PROBE_TIMEOUT) -> Any:
    if not _reachable(url):
        raise OSError("nothing listening")
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), method="POST")
    for k, v in headers.items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def list_models(provider: Provider) -> list[str]:
    """Model ids the endpoint advertises. Empty list if it does not say."""
    base = probe_base_url(provider)
    if not base:
        return []
    headers = _headers_for(provider)
    # Ollama's native endpoint is richer than its OpenAI shim.
    if provider.type == "ollama":
        try:
            root = base[:-3] if base.endswith("/v1") else base
            data = _get_json(f"{root}/api/tags", headers)
            names = [m.get("name") for m in data.get("models", []) if m.get("name")]
            if names:
                return names
        except Exception:
            pass
    for suffix in ("/v1/models", "/models"):
        try:
            data = _get_json(f"{base}{suffix}", headers)
            items = data if isinstance(data, list) else data.get("data", [])
            names = [m.get("id") for m in items if isinstance(m, dict) and m.get("id")]
            if names:
                return names
        except Exception:
            continue
    return []


def _ollama_root(provider: Provider) -> str:
    """The native API root behind Ollama's OpenAI shim."""
    base = probe_base_url(provider).rstrip("/")
    return base[:-3].rstrip("/") if base.endswith("/v1") else base


def ollama_model_facts(provider: Provider) -> dict:
    """What Ollama itself reports about `provider.model`.

    Ollama's OpenAI shim answers `/v1/models` with `{id, object, created,
    owned_by}` and nothing else — no context length, no capability list. So the
    generic probe learned nothing from it, fell back to a hardcoded 8192, and
    stamped that guess `probed=True`; every local model on this machine, from a
    32,768-token coder to a 262,144-token Qwen, was run at a quarter of its
    smallest real window.

    The native endpoints do say. `/api/show` returns `capabilities`
    (`tools`, `vision`, `thinking`, …) and a `model_info` map carrying
    `<architecture>.context_length`. One call, ~2 s, and it answers by
    declaration what `_probe_feature` was trying to establish by experiment in
    15 s a feature — and getting wrong.

    Returns `{}` when the endpoint does not answer, so every caller keeps its
    existing fallback rather than inheriting a hole.
    """
    if not provider.model:
        return {}
    root = _ollama_root(provider)
    if not root:
        return {}
    try:
        show = _post_json(f"{root}/api/show", _headers_for(provider),
                          {"model": provider.model},
                          timeout=_timeout_for(provider))
    except Exception:
        return {}
    if not isinstance(show, dict):
        return {}

    ctx = 0
    for key, value in (show.get("model_info") or {}).items():
        # Keyed by architecture — `gemma3.context_length`, `qwen3vl.context_length`
        # — so it is matched by suffix rather than by a table of known families
        # that a new model would fall out of.
        if key.endswith(".context_length") and value:
            ctx = int(value)
            break

    # `parameters` is `PARAMETER` lines from this pull's Modelfile — a raw
    # newline-separated "key value" string, not structured JSON:
    #
    #   temperature   0.7
    #   num_ctx       32684
    #   repeat_penalty 1.05
    #
    # It is the model's *own* declared window, and it can differ from both
    # `model_info`'s architectural ceiling and this module's flat
    # `OLLAMA_DEFAULT_NUM_CTX`. Measured: a `qwen3moe` pull whose architecture
    # supports 262,144 had `num_ctx 32684` baked into its Modelfile — 84
    # tokens off the 32,768 this code was reporting, which was a coincidence
    # of two unrelated numbers being close, not a measurement. A pull with,
    # say, `num_ctx 8192` would have been reported as 32,768: four times too
    # large, in the direction that produces a truncated request instead of a
    # visible error.
    declared_num_ctx = 0
    for line in str(show.get("parameters") or "").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "num_ctx":
            try:
                declared_num_ctx = int(parts[1])
            except ValueError:
                pass
            break

    return {
        "context_window": ctx,
        "declared_num_ctx": declared_num_ctx,
        "capabilities": [c for c in (show.get("capabilities") or [])
                         if isinstance(c, str)],
    }


def _ollama_served_default(declared_num_ctx: int = 0) -> int:
    """The window this server hands out when nothing asks for more.

    `OLLAMA_CONTEXT_LENGTH` is the user's own explicit setting and wins
    outright, same as before. Absent that, `declared_num_ctx` — this specific
    model's own `PARAMETER num_ctx` from its Modelfile, read by
    `ollama_model_facts` — is a fact about *that* model and is preferred over
    `OLLAMA_DEFAULT_NUM_CTX`, which is one number applied to every model
    regardless of what it actually asks to be loaded with.
    """
    try:
        configured = int(os.environ.get("OLLAMA_CONTEXT_LENGTH", "") or 0)
    except ValueError:
        configured = 0
    return configured or declared_num_ctx or OLLAMA_DEFAULT_NUM_CTX


def _ollama_remote_models(provider: Provider) -> set:
    """Names Ollama routes to its cloud rather than running on this machine.

    The local `num_ctx` ceiling is a property of *this* server's memory, so it
    has no business capping a model executing somewhere else — capping
    nemotron-3-super:cloud at 32,768 would hide 229,376 tokens of a window that
    costs this machine nothing. `/api/tags` marks these with `remote_host`;
    `/api/show` does not report it, which is why the fact is fetched here and
    not read off the model's own record.

    Deliberately not sniffed from the `:cloud` suffix — this module does not
    infer behaviour from substrings in a name (see the module docstring).
    """
    try:
        data = _get_json(f"{_ollama_root(provider)}/api/tags",
                         _headers_for(provider), timeout=_PROBE_TIMEOUT)
    except Exception:
        return set()
    return {m.get("name") for m in (data or {}).get("models") or []
            if m.get("remote_host") and m.get("name")}


def _ollama_served_context(provider: Provider, model_max: int,
                           remote: bool = False,
                           declared_num_ctx: int = 0) -> int:
    """How much of `model_max` this server will actually hand out.

    A loaded model reports its allocated `context_length` on `/api/ps`, which
    is ground truth and needs no guessing. For one that is not loaded, the
    server's default applies — configurable through OLLAMA_CONTEXT_LENGTH, else
    this model's own declared `num_ctx` (see `_ollama_served_default`), else
    the measured `OLLAMA_DEFAULT_NUM_CTX`. A cloud-routed model is not subject
    to any of these and keeps its own window.
    """
    if remote and model_max:
        return model_max
    try:
        ps = _get_json(f"{_ollama_root(provider)}/api/ps",
                       _headers_for(provider), timeout=_PROBE_TIMEOUT)
        for entry in (ps or {}).get("models") or []:
            if entry.get("name") == provider.model and entry.get("context_length"):
                return int(entry["context_length"])
    except Exception:
        pass

    cap = _ollama_served_default(declared_num_ctx)
    return min(model_max, cap) if model_max else cap


def _probe_context_window(provider: Provider) -> int:
    """Ask the endpoint for the model's real context window."""
    base = probe_base_url(provider)
    if not base or not provider.model:
        return 0
    headers = _headers_for(provider)
    for suffix in ("/v1/models", "/models"):
        try:
            data = _get_json(f"{base}{suffix}", headers)
        except Exception:
            continue
        items = data if isinstance(data, list) else data.get("data", [])
        for m in items:
            if not isinstance(m, dict) or m.get("id") != provider.model:
                continue
            for key in ("context_window", "context_length", "context",
                        "max_context_length"):
                if m.get(key):
                    return int(m[key])
            top = m.get("top_provider") or {}
            if isinstance(top, dict) and top.get("context_length"):
                return int(top["context_length"])

    # Zen's `/v1/models` is `{id, object, created, owned_by}` and nothing else,
    # so the loop above always falls through for it and every Zen model
    # inherited the 200,000 default. That is not a rounding error in either
    # direction: `hy3-free` is 190,000 and `nemotron-3-ultra-free` is
    # 1,000,000, and `core.budget` sizes the tool ceiling and output reserve
    # as *shares of the window* — so the wrong window mis-sizes every section
    # of the prompt. The published catalogue does carry the number.
    if provider.type == "zen":
        try:
            import zen_catalog
            entry = zen_catalog.catalog().get(provider.model)
            if entry and entry.context_known:
                return entry.context
        except Exception:
            pass

    # Google's OpenAI-shaped `/models` carries id and display name and nothing
    # else, so the loop above falls through for it too. Its native listing has
    # the number, and the spread is too wide to default: 8,192 for the TTS
    # models against 1,048,576 for gemini-3.6-flash, in the same catalogue.
    if provider.type == "google":
        try:
            window = _google_context_window(provider)
            if window:
                return window
        except Exception:
            pass
    return 0


# A 1x1 transparent PNG. Small enough to cost nothing, real enough that an
# endpoint which decodes images accepts it and one which does not rejects it.
_PIXEL_PNG = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
              "z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def _tiny_request(provider: Provider, stream: bool = False,
                  with_tools: bool = False, with_system: bool = True,
                  with_image: bool = False) -> dict:
    """The smallest request that still exercises the feature being probed."""
    if provider.speaks_openai_wire:
        user_content: Any = "ok"
        if with_image:
            user_content = [
                {"type": "text", "text": "ok"},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{_PIXEL_PNG}"}},
            ]
        body: dict = {
            "model": provider.model,
            "messages": ([{"role": "system", "content": "reply with ok"}]
                         if with_system else [])
                        + [{"role": "user", "content": user_content}],
            "max_tokens": 8,
        }
        if stream:
            body["stream"] = True
        if with_tools:
            body["tools"] = [{
                "type": "function",
                "function": {"name": "ping", "description": "ping",
                             "parameters": {"type": "object", "properties": {}}},
            }]
    else:
        content: Any = "ok"
        if with_image:
            content = [
                {"type": "text", "text": "ok"},
                {"type": "image",
                 "source": {"type": "base64", "media_type": "image/png",
                            "data": _PIXEL_PNG}},
            ]
        body = {
            "model": provider.model,
            "max_tokens": 8,
            "messages": [{"role": "user", "content": content}],
        }
        if with_system:
            body["system"] = "reply with ok"
        if stream:
            body["stream"] = True
        if with_tools:
            body["tools"] = [{"name": "ping", "description": "ping",
                              "input_schema": {"type": "object", "properties": {}}}]
    return body


def probe_base_url(provider: Provider) -> str:
    """Where a probe should actually go for this provider.

    Only zen differs from `provider.base_url`, and it differs for a historical
    reason: a zen provider is conventionally configured against the local proxy
    port, which has only ever meant "reach Zen". `openai_adapter` resolves that
    to the real host at runtime; the probe has to resolve it the same way or it
    measures a port with nothing listening on it and reports the endpoint as
    incapable of everything.
    """
    base = (provider.base_url or "").rstrip("/")
    if provider.type == "zen" and (not base or ":6446" in base):
        try:
            from zen_proxy import ZEN_API_HOST
        except Exception:
            ZEN_API_HOST = "opencode.ai"
        return os.environ.get("ZEN_UPSTREAM_URL",
                              f"https://{ZEN_API_HOST}/zen/v1").rstrip("/")
    if provider.type == "google":
        # A google provider is conventionally saved with no base_url at all
        # (the setup page only ever asked for a key), or with the *native*
        # host. Both mean "reach Gemini", and only the OpenAI-compatible path
        # can be spoken by the adapter.
        if not base or "generativelanguage" in base:
            return GOOGLE_OPENAI_BASE
    return base


def _completions_url(provider: Provider) -> str:
    base = probe_base_url(provider)
    if provider.speaks_openai_wire:
        return f"{base}/chat/completions" if base.endswith("/v1") else f"{base}/v1/chat/completions"
    return f"{base}/v1/messages"


def _probe_feature(provider: Provider, optimistic: bool = True, **kw) -> bool:
    """True if a minimal request using this feature is accepted.

    A transport failure (no network, endpoint down) is not evidence that the
    feature is missing, so it returns `optimistic` — True for the features
    whose absence merely degrades the reply, and where the runtime path
    handles being wrong. Pass False where guessing wrong costs the user a
    hard rejection instead (vision: a text-only model 400s on an image).
    """
    url = _completions_url(provider)
    body = _tiny_request(provider, **kw)
    timeout = _timeout_for(provider)
    try:
        if kw.get("stream"):
            req = urllib.request.Request(
                url, data=json.dumps(body).encode("utf-8"), method="POST")
            for k, v in _headers_for(provider).items():
                req.add_header(k, v)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                chunk = resp.read(64)
                # A real stream announces itself; a JSON blob is not a stream.
                ctype = resp.headers.get("Content-Type", "")
                return "event-stream" in ctype or chunk.lstrip().startswith(b"data:")
        _post_json(url, _headers_for(provider), body, timeout=timeout)
        return True
    except urllib.error.HTTPError as e:
        # 4xx means the endpoint understood us and refused the feature.
        # 5xx / anything else is the endpoint having a bad day.
        return not (400 <= e.code < 500)
    except Exception:
        return optimistic


def probe(provider: Provider, quick: bool = False) -> Capabilities:
    """Discover what this provider can actually do. Cache the result.

    Every check degrades to the safe default on failure, so a probe against
    an unreachable endpoint returns usable optimistic capabilities rather
    than blocking configuration.
    """
    caps = Capabilities()
    caps.max_tools = KNOWN_TOOL_CEILINGS.get(provider.type, 128)

    # Ollama is asked rather than experimented on. Its native API declares the
    # context length and the capability list outright, which is both faster
    # than four HTTP round trips and correct where they were not — see
    # `ollama_model_facts`. This runs for `quick` too: it *is* the quick path.
    if provider.type == "ollama":
        facts = ollama_model_facts(provider)
        if facts:
            declared = facts.get("capabilities") or []
            caps.context_window = _ollama_served_context(
                provider, facts.get("context_window") or 0,
                remote=provider.model in _ollama_remote_models(provider),
                declared_num_ctx=facts.get("declared_num_ctx") or 0)
            caps.tool_use = "tools" in declared
            caps.parallel_tool_calls = caps.tool_use
            caps.vision = "vision" in declared
            # Not declared, because they are properties of the server rather
            # than the model: every Ollama build streams and takes a system
            # role. Verified against 0.30.6 on both the shim and the native API.
            caps.streaming = True
            caps.system_prompt = True
            caps.prompt_caching = False
            caps.probed_at = time.time()
            return caps
        # The server did not answer — it is probably not running. Fall back to
        # what it *would* serve rather than to the old hardcoded 8192, which
        # was four times too small for every model on this machine and, being
        # stamped `probed=True`, outranked every other source in
        # `agent.resolve_context_window`.
        caps.context_window = _ollama_served_context(provider, 0)

    cw = _probe_context_window(provider)
    if cw:
        caps.context_window = cw

    if not quick and provider.base_url and provider.model:
        caps.streaming = _probe_feature(provider, stream=True)
        caps.tool_use = _probe_feature(provider, with_tools=True)
        if not caps.tool_use:
            caps.parallel_tool_calls = False
        caps.system_prompt = _probe_feature(provider, with_system=True)
        # Vision is the one capability that was never measured: the field
        # defaulted to False and no code path ever set it, so anything asking
        # "can this model read an image?" got the same answer whatever the
        # model. A 1x1 PNG settles it for the price of one 8-token call.
        # Note the default here is the opposite of the others — an
        # unreachable endpoint means "assume not", because sending an image
        # to a text-only model is a hard 400, not a degraded reply.
        caps.vision = _probe_feature(provider, with_image=True, optimistic=False)

    caps.prompt_caching = provider.type == "anthropic"
    caps.probed_at = time.time()
    return caps


def probe_and_persist(provider: Provider) -> Capabilities:
    provider.capabilities = probe(provider)
    persist_capabilities(provider)
    return provider.capabilities


def _tool_support_is_unknown(provider: Provider) -> bool:
    """True when this provider's catalogue cannot say whether the model
    accepts tools — so the answer has to be measured rather than carried.

    False when the catalogue does say (OpenRouter publishes
    `supported_parameters`, Zen and Ollama publish a capability list), and
    false when there is no catalogue at all, because then there is nothing
    new to learn and a probe would only cost latency.
    """
    try:
        for entry in catalog_for(provider.type):
            if entry["id"] == provider.model:
                return not entry["tool_call_known"]
    except Exception:
        pass
    return False


def refresh_for_model(provider: Provider) -> Capabilities:
    """Re-measure what changes when the *model* changes, and persist it.

    Switching model used to keep the previous model's capabilities wholesale.
    `/model <name>` writes the new name onto the provider *before* refreshing,
    so `agent._probed_capability_window` found `active.model == model`, decided
    the stored measurement applied, and handed back the old model's window.
    Between an Ollama 32k coder and a 262k Qwen that is not a rounding error.

    What is re-measured is what belongs to the model: the context window, and —
    for Ollama, where it costs one declaration rather than four experiments —
    tool use and vision. What belongs to the *endpoint* (streaming, system
    role, caching, the tool ceiling) is carried over rather than thrown away,
    because re-establishing it means another round of slow calls to learn
    something that did not change.
    """
    old = provider.capabilities
    if provider.type == "ollama":
        fresh = probe(provider)
    else:
        fresh = Capabilities(**old.to_dict())
        window = _probe_context_window(provider)
        # No answer means no measurement. Clearing `probed_at` is what lets
        # `resolve_context_window` fall through to its known-value table
        # instead of trusting a number measured for a different model.
        fresh.context_window = window or old.context_window
        fresh.probed_at = time.time() if window else 0.0

        # Tool support belongs to the *model* wherever the catalogue cannot
        # establish it, so carrying the previous model's answer is the same
        # mistake as carrying its context window. Groq is the case: its
        # /v1/models has no tool field, and five of its ten chat models
        # answer `400 "tool calling" is not supported with this model` while
        # the other five call tools normally. Switching from gpt-oss-120b to
        # groq/compound would otherwise keep tool_use=True and fail the first
        # turn. One probe, and only when the catalogue has already said it
        # does not know.
        if _tool_support_is_unknown(provider):
            fresh.tool_use = _probe_feature(provider, with_tools=True)
            fresh.parallel_tool_calls = fresh.tool_use
    fresh.max_tools = old.max_tools
    provider.capabilities = fresh
    persist_capabilities(provider)
    return fresh


def capabilities_for_active(default: Optional[Capabilities] = None) -> Capabilities:
    """Capabilities of the active provider, or optimistic defaults.

    Never probes — callers on the hot path must not pay network latency. An
    unprobed provider still gets the documented ceiling for its type, which
    is the one thing worth knowing before the first probe runs.
    """
    provider = get_active()
    if provider is None:
        return default or Capabilities()
    caps = provider.capabilities
    if not caps.probed:
        caps.max_tools = KNOWN_TOOL_CEILINGS.get(provider.type, caps.max_tools)
    return caps


# ══════════════════════════════════════════════════════════════════════
#  Activation
# ══════════════════════════════════════════════════════════════════════

def activate(name: str, start_proxy_if_needed: bool = True) -> bool:
    """Switch to a configured provider. Returns True on success."""
    provider = get(name)
    if provider is None:
        return False

    if provider.type == "zen" and start_proxy_if_needed and _use_standalone_proxy():
        try:
            from zen_proxy import check_status, start_proxy
            if not check_status(6446):
                start_proxy(6446, daemon=True)
        except Exception:
            pass

    for key, value in (provider.env or {}).items():
        apply_env(key, value)
    # Activation defines the whole connection, so it must also *unset* what
    # this provider does not define. It only ever wrote before, and a provider
    # with no base_url of its own — Zen stores `base_url: ""` — therefore
    # inherited whatever the previously activated one left in ~/.tomas/.env,
    # which is read into os.environ at import. A Zen session ran with
    # `ANTHROPIC_BASE_URL=http://localhost:11434/v1` still live, and
    # `agent._get_client`'s Anthropic-SDK fallback dialled an Ollama that was
    # not running: "[WinError 10061] ... actively refused it", every turn.
    if "ANTHROPIC_BASE_URL" not in (provider.env or {}):
        if provider.base_url:
            apply_env("ANTHROPIC_BASE_URL", provider.base_url)
        else:
            clear_env("ANTHROPIC_BASE_URL")
    if provider.model:
        apply_env("AGENT_MODEL", provider.model)
    if provider.extra_headers:
        apply_env("ANTHROPIC_EXTRA_HEADERS", json.dumps(provider.extra_headers))
    elif "ANTHROPIC_EXTRA_HEADERS" not in (provider.env or {}):
        clear_env("ANTHROPIC_EXTRA_HEADERS")

    config = load_config()
    config["active"] = name
    save_config(config)

    try:
        from agent import reinit_client
        reinit_client()
    except Exception:
        pass
    return True


#: The keys that together say *where a request goes, and as what*. They are
#: written by `activate()` and read by `agent._get_client` / `agent._get_model`.
CONNECTION_ENV_KEYS = ("ANTHROPIC_BASE_URL", "AGENT_MODEL",
                       "ANTHROPIC_EXTRA_HEADERS")


def sync_env_to_active() -> Optional[str]:
    """Reconcile this process's environment with the active provider, once.

    Activation state lives in two files and startup loaded only one of them:
    `~/.tomas/.env` is read into `os.environ` at import, `providers.json`
    decides which provider is active, and nothing brought the two into line —
    `activate()` runs when the *user switches*, never on the way in. A session
    therefore opened with the endpoint, model name and headers of whichever
    provider was activated last, while the menus and `openai_adapter` read the
    active one. That is the split behind a `/config` panel reading

        Provider   OpenCode Zen (opencode.ai)
        Model      gemma4:31b-cloud

    and a first turn that died on a dead localhost port.

    A value present in the real environment but absent from `~/.tomas/.env` is
    a deliberate pre-launch override (`AGENT_MODEL=x python agent.py`, which
    `tests/labwork_sim.py` sweeps models with). It outranks the stored
    provider, so it is restored in-process — and never written to the file,
    because an override is for this run only.

    Returns the active provider's name, or None when none is configured.
    """
    config = load_config()
    name = config.get("active")
    if not name or get(name) is None:
        return None
    stored = env_file_keys(ENV_FILE)
    overrides = {k: os.environ[k] for k in CONNECTION_ENV_KEYS
                 if k in os.environ and k not in stored}
    activate(name, start_proxy_if_needed=False)
    for key, value in overrides.items():
        os.environ[key] = value
    return name


def _use_standalone_proxy() -> bool:
    """The daemon is opt-in now that translation runs in-process.

    Set TOMAS_ZEN_PROXY=1 to get the old behaviour — genuinely useful when
    pointing *other* tools at Zen, unnecessary for the agent itself.
    """
    return os.environ.get("TOMAS_ZEN_PROXY", "") == "1"


# ══════════════════════════════════════════════════════════════════════
#  Local models (Ollama)
# ══════════════════════════════════════════════════════════════════════

def detect_ollama(base_url: str = OLLAMA_DEFAULT_URL) -> Optional[Provider]:
    """Return a ready-to-save Ollama provider if one is running locally."""
    probe_provider = Provider(name="Ollama (local)", type="ollama",
                              base_url=base_url, api_key_env="OLLAMA_API_KEY")
    models = list_models(probe_provider)
    if not models:
        return None
    probe_provider.model = models[0]
    probe_provider.env = {
        "ANTHROPIC_BASE_URL": base_url,
        "ANTHROPIC_API_KEY": "ollama",   # the shim wants a non-empty key
    }
    probe_provider.capabilities = probe(probe_provider, quick=True)
    return probe_provider


def available_models(name: str) -> list[str]:
    provider = get(name)
    return list_models(provider) if provider else []


def google_model_catalog(api_key: str = "") -> list[dict]:
    """Every Gemini model this key can call, with its real context window.

    Read from the *native* `/v1beta/models`, not the OpenAI-shaped one: only
    the native listing carries `inputTokenLimit`, and that number is the whole
    point. Gemini windows range from 8,192 (the TTS models) to 1,048,576 in one
    catalogue, so a provider-wide default cannot be right for more than a
    handful of them — and `core.budget` sizes the tool ceiling, the output
    reserve and the compaction trigger as shares of it.

    Filtered to models that can actually hold a conversation:
    `generateContent` is the method the adapter uses, and an embedding or
    image-only model appearing in the picker is an entry that fails on use.
    """
    key = (api_key or os.environ.get("GOOGLE_API_KEY", "")
           or os.environ.get("GEMINI_API_KEY", "")).strip()
    if not key:
        return []
    try:
        # The key goes in the query string. Gemini's native API rejects it as
        # a bearer token outright — measured: 401 API_KEY_SERVICE_BLOCKED,
        # "Expected OAuth 2 access token".
        data = _get_json(f"{GOOGLE_NATIVE_BASE}/models?key={key}&pageSize=200",
                         {}, timeout=_PROBE_TIMEOUT)
    except Exception:
        return []

    out: list[dict] = []
    for entry in (data or {}).get("models") or []:
        name = (entry.get("name") or "").replace("models/", "")
        methods = entry.get("supportedGenerationMethods") or []
        if not name or "generateContent" not in methods:
            continue
        out.append({
            "name": name,
            "label": entry.get("displayName") or name,
            "context_window": int(entry.get("inputTokenLimit") or 0),
            "max_output": int(entry.get("outputTokenLimit") or 0),
        })
    out.sort(key=lambda m: (-m["context_window"], m["name"]))
    return out


def _google_context_window(provider: Provider) -> int:
    """The active Gemini model's window, or 0 if it cannot be established."""
    if not provider.model:
        return 0
    wanted = provider.model.replace("models/", "")
    for entry in google_model_catalog(provider.api_key):
        if entry["name"] == wanted:
            return entry["context_window"]
    return 0


def ollama_catalog(base_url: str = "") -> list[dict]:
    """Every locally installed model with the facts a picker should show.

    `/api/tags` carries `capabilities` and `details.context_length` alongside
    the name, so one call answers "which models are here, which of them can
    call tools, and how big is each" — the three things the model menu was
    guessing at. `context_window` is the *served* figure, not the model's
    advertised maximum, for the reason in `OLLAMA_DEFAULT_NUM_CTX`.

    Older manifests predate `details.context_length` (both gemma3 tags on this
    machine), so a missing window is reported as 0 and left for the caller to
    render as unknown rather than filled in with a guess.
    """
    provider = Provider(name="Ollama (local)", type="ollama",
                        base_url=base_url or OLLAMA_DEFAULT_URL,
                        api_key_env="OLLAMA_API_KEY")
    try:
        data = _get_json(f"{_ollama_root(provider)}/api/tags",
                         _headers_for(provider), timeout=_PROBE_TIMEOUT)
    except Exception:
        return []

    # One `/api/ps`, not one per model: this runs while a menu is being drawn.
    loaded: dict[str, int] = {}
    try:
        ps = _get_json(f"{_ollama_root(provider)}/api/ps",
                       _headers_for(provider), timeout=_PROBE_TIMEOUT)
        for entry in (ps or {}).get("models") or []:
            if entry.get("name") and entry.get("context_length"):
                loaded[entry["name"]] = int(entry["context_length"])
    except Exception:
        pass
    default_ctx = _ollama_served_default()

    out: list[dict] = []
    for entry in (data or {}).get("models") or []:
        name = entry.get("name")
        if not name:
            continue
        details = entry.get("details") or {}
        declared = [c for c in (entry.get("capabilities") or [])
                    if isinstance(c, str)]
        model_max = int(details.get("context_length") or 0)
        if entry.get("remote_host"):
            window = loaded.get(name, model_max)
        elif model_max:
            window = loaded.get(name, min(model_max, default_ctx))
        else:
            # Manifests written before `details.context_length` existed say
            # nothing (both gemma3 tags here). The server's own ceiling still
            # applies, so it is reported as an upper bound rather than as a
            # measurement — `exact: False` is what lets the menu say so.
            window = loaded.get(name, default_ctx)
        out.append({
            "name": name,
            "context_window": window,
            "exact": bool(loaded.get(name) or model_max),
            "tools": "tools" in declared,
            "vision": "vision" in declared,
            "params": details.get("parameter_size") or "",
        })
    return out


def openrouter_catalog(api_key: str = "") -> list[dict]:
    """Every model OpenRouter serves, with price, window and tool support.

    This function was *called* by `agent_cli._provider_model_entries` and
    `_format_openrouter_entries` and did not exist — opening the OpenRouter
    model picker raised AttributeError inside `net_probe.cached`. It lives
    here rather than in the TUI because the free-model pool needs the same
    answer and neither should have its own copy.

    The listing is public: no key is needed to read it, so the picker works
    before a key is entered. Measured 2026-08-29: 396 models, 21 free by
    price, 18 of those able to call tools.

    **Free is `pricing.prompt == 0 and pricing.completion == 0`, never the
    `:free` suffix.** The suffix is usually right on OpenRouter, and that is
    exactly what makes trusting it dangerous — `zen_catalog` already carries
    this scar, where a first-run path announced the free tier and then
    selected a model that bills. Prices arrive as decimal *strings*
    ("0.000000834"), so they are compared as floats, never as text.
    """
    headers = {"Content-Type": "application/json"}
    spec = registry.spec("openrouter")
    if spec:
        headers.update(spec.extra_headers)
    key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
    if key:
        headers["Authorization"] = f"Bearer {key}"
    try:
        data = _get_json("https://openrouter.ai/api/v1/models", headers,
                         timeout=_PROBE_TIMEOUT)
    except Exception:
        return []

    def _price(entry: dict, field_name: str) -> float:
        raw = (entry.get("pricing") or {}).get(field_name)
        try:
            return float(raw)
        except (TypeError, ValueError):
            # No price is not a price of zero. An entry whose cost cannot be
            # established must never reach a free pool, so it is reported as
            # expensive rather than as unknown.
            return float("inf")

    out: list[dict] = []
    for entry in (data or {}).get("data") or []:
        model_id = entry.get("id")
        if not model_id:
            continue
        arch = entry.get("architecture") or {}
        params = entry.get("supported_parameters") or []
        prompt_cost = _price(entry, "prompt")
        completion_cost = _price(entry, "completion")
        priced = prompt_cost != float("inf") and completion_cost != float("inf")
        out.append({
            # `id`, `tool_call` and the two cost fields are the shape
            # `agent_cli._format_openrouter_entries` already documents and
            # renders. It was written against this function before this
            # function existed, so its expectations are the specification.
            "id": model_id,
            "name": model_id,
            "label": entry.get("name") or model_id,
            "context_window": int(entry.get("context_length") or 0),
            "priced": priced,
            "free": priced and prompt_cost == 0.0 and completion_cost == 0.0,
            "prompt_cost": prompt_cost if priced else 0.0,
            "completion_cost": completion_cost if priced else 0.0,
            "tool_call": "tools" in params,
            "vision": "image" in (arch.get("input_modalities") or []),
        })
    out.sort(key=lambda m: (not m["free"], -m["context_window"], m["id"]))
    return out


#: Groq output modalities that mean "this is a chat model". Its `/v1/models`
#: lists transcription and speech models alongside the chat ones, and a picker
#: offering `whisper-large-v3` as an agent model is an entry that fails on use
#: — the same defect as offering an embedding model for Google, which
#: `google_model_catalog` already filters out via `generateContent`.
_GROQ_CHAT_OUTPUTS = ("text",)


def groq_catalog(api_key: str = "") -> list[dict]:
    """Every Groq model this key can call, with its real window.

    Two facts the generic `/v1/models` path could not have known, both
    measured against the live endpoint on 2026-08-29:

    Groq is behind Cloudflare and answers a bare `Authorization: Bearer …`
    with `403 error code: 1010` — the same block Zen sits behind. With a
    User-Agent it answers. That header is carried in the spec's
    `extra_headers`, so the probe and the real call cannot disagree about it.

    Its listing is *richer* than OpenAI's: `context_window`,
    `max_completion_tokens` and `input_modalities` all come back, so the
    window and vision support are read rather than assumed.

    Unlike OpenRouter and Zen it publishes no pricing at all — every model on
    the account is reachable the same way — so there is no `free` field here.
    Whether these cost anything is a property of the account, which is what
    `ProviderSpec.free_tier` records.
    """
    key = api_key or os.environ.get("GROQ_API_KEY", "")
    if not key:
        return []
    spec = registry.spec("groq")
    headers = {"Content-Type": "application/json",
               "Authorization": f"Bearer {key}"}
    if spec:
        headers.update(spec.extra_headers)
    base = spec.base_url if spec else "https://api.groq.com/openai/v1"
    try:
        data = _get_json(f"{base}/models", headers, timeout=_PROBE_TIMEOUT)
    except Exception:
        return []

    out: list[dict] = []
    for entry in (data or {}).get("data") or []:
        model_id = entry.get("id")
        if not model_id or entry.get("active") is False:
            continue
        outputs = entry.get("output_modalities") or ["text"]
        if not any(o in _GROQ_CHAT_OUTPUTS for o in outputs):
            continue
        inputs = entry.get("input_modalities") or ["text"]
        out.append({
            "id": model_id,
            "name": model_id,
            "label": model_id,
            "context_window": int(entry.get("context_window")
                                  or entry.get("context_length") or 0),
            "max_output": int(entry.get("max_completion_tokens") or 0),
            "vision": "image" in inputs,
            # Groq's listing carries no tool field, and it is not a detail
            # that can be assumed either way. Measured across all ten chat
            # models on 2026-08-29: five call tools, and five answer
            # `400 "tool calling" is not supported with this model` —
            # groq/compound and compound-mini (they run their own tools),
            # allam-2-7b, and both llama-prompt-guard classifiers. Claiming
            # support here would put a model that cannot drive the agent at
            # the top of a free pool. `probe()` settles it per model and
            # persists the answer; until then it is unknown, not true.
            "tool_call_known": False,
        })
    out.sort(key=lambda m: (-m["context_window"], m["id"]))
    return out


def huggingface_catalog(api_key: str = "") -> list[dict]:
    """Every model HF's Inference Providers router can currently serve.

    Went through the generic `openai_list` fetcher until 2026-08-29, which
    reads only `{id}` off `/v1/models` — the same thin shape used for
    endpoints that genuinely have nothing richer. HF's router is not one of
    those: its `/v1/models` entry for one model is

        {"id": "...", "architecture": {"input_modalities": [...],
         "output_modalities": [...]},
         "providers": [{"provider": "novita", "status": "live",
                        "context_length": 1048576, "supports_tools": true,
                        "is_free": false, ...}, ...]}

    — a model routed through several backing providers, each with its own
    window, price and tool support. Reading only `id` out of that and then
    falling to the generic-fallback's guessed OpenRouter-shaped names (see
    `agent_cli._provider_model_entries`) is how `qwen/qwen-2.5-72b-instruct`
    ended up offered for this endpoint: a real OpenRouter slug, and not one
    of the 136 ids `router.huggingface.co` actually serves — HF's own naming
    is `Qwen/Qwen2.5-72B-Instruct` when it appears at all, case included.

    One model can list several providers at different windows; the largest
    live one is reported, on the theory that a menu picking a model is
    choosing capability, not routing — the request itself does not name a
    backing provider and HF picks one internally.
    """
    key = api_key or os.environ.get("HF_TOKEN", "")
    if not key:
        return []
    spec = registry.spec("huggingface")
    headers = {"Content-Type": "application/json",
               "Authorization": f"Bearer {key}"}
    if spec:
        headers.update(spec.extra_headers)
    base = spec.base_url if spec else "https://router.huggingface.co/v1"
    try:
        data = _get_json(f"{base}/models", headers, timeout=_PROBE_TIMEOUT)
    except Exception:
        return []

    out: list[dict] = []
    for entry in (data or {}).get("data") or []:
        model_id = entry.get("id")
        if not model_id:
            continue
        arch = entry.get("architecture") or {}
        outputs = arch.get("output_modalities") or ["text"]
        if "text" not in outputs:
            continue
        live = [p for p in (entry.get("providers") or [])
                if p.get("status") == "live"]
        if not live:
            continue
        out.append({
            "id": model_id,
            "name": model_id,
            "label": model_id,
            "context_window": max((p.get("context_length") or 0)
                                  for p in live),
            "vision": "image" in (arch.get("input_modalities") or []),
            # Unlike Groq, every live model declares this explicitly — it is
            # `tool_call_known=True` whichever way it comes out, never a
            # default the picker has to guess past.
            "tool_call": any(p.get("supports_tools") for p in live),
            "tool_call_known": True,
            "free": any(p.get("is_free") for p in live),
        })
    out.sort(key=lambda m: (-m["context_window"], m["id"]))
    return out


def zen_model_catalog(api_key: str = "") -> list[dict]:
    """`zen_catalog` in the shape every other fetcher returns."""
    try:
        import zen_catalog
    except Exception:
        return []
    return [{"name": m.id, "label": m.label, "context_window": m.context,
             "priced": True, "free": m.free, "tools": m.tool_call,
             "vision": m.vision, "served": m.served}
            for m in zen_catalog.catalog().models]


def _openai_list_catalog(api_key: str = "") -> list[dict]:
    """Generic `/v1/models` for endpoints with nothing richer to offer.

    Deliberately thin: ids and nothing else. An endpoint that reports only ids
    genuinely knows only ids, and inventing a context window here is how
    `MODEL_CONTEXT_MAP` got its wrong numbers.
    """
    provider = get_active()
    if provider is None:
        return []
    return [{"name": m, "label": m, "context_window": 0}
            for m in list_models(provider)]


def _static_catalog(api_key: str = "") -> list[dict]:
    """The spec's own list, for endpoints that publish no listing."""
    provider = get_active()
    spec = registry.spec(provider.type) if provider else None
    if spec is None:
        return []
    return [{"name": m, "label": m, "context_window": 0}
            for m in spec.static_models]


def _ollama_list_catalog(api_key: str = "") -> list[dict]:
    return ollama_catalog()


#: `ProviderSpec.models` names one of these. Resolved here rather than in the
#: registry because the registry is pure and these make network calls — and
#: resolved by name *at import* rather than by getattr at menu time, so a spec
#: naming a fetcher that does not exist is a startup failure instead of an
#: AttributeError the user meets on opening a menu. Which is precisely how the
#: missing `openrouter_catalog` stayed missing.
CATALOG_FETCHERS = {
    "openai_list": _openai_list_catalog,
    "static": _static_catalog,
    "zen_catalog": zen_model_catalog,
    "google_catalog": google_model_catalog,
    "groq_catalog": groq_catalog,
    "huggingface_catalog": huggingface_catalog,
    "openrouter_catalog": openrouter_catalog,
    "ollama_catalog": _ollama_list_catalog,
}


#: What `catalog_for` guarantees, whatever the fetcher underneath returned.
#:
#: The fetchers keep their own shapes on purpose — `google_model_catalog` and
#: `ollama_catalog` have callers in the TUI that read `name`, and
#: `openrouter_catalog` has one that reads `id`, `tool_call` and the cost
#: fields. Normalising *here* rather than rewriting them means the existing
#: pickers keep working while everything new — the free pool above all — has
#: one shape to read.
#:
#: `priced` and `tool_call_known` are not decoration. Each separates "the
#: catalogue says no" from "the catalogue does not say", and the free pool
#: must never collapse the two: an unknown price is not zero, and an unknown
#: tool capability is not support.
#:
#: Both distinctions were paid for. Groq's listing has no tool field, so
#: defaulting `tool_call` to True picked `groq/compound` as its best model and
#: the round trip died on `400 "tool calling" is not supported with this
#: model`. Five of its ten chat models answer that way.
CANONICAL_CATALOG_KEYS = ("id", "label", "context_window", "priced", "free",
                          "tool_call", "tool_call_known", "vision")


def _normalise_catalog_entry(entry: dict, spec) -> dict:
    model_id = entry.get("id") or entry.get("name") or ""
    priced = entry.get("priced")
    if priced is None:
        # A catalogue that publishes no prices at all — Groq, Ollama — has not
        # said the model is free, it has said nothing. The account-level fact
        # is `spec.free_tier`, and that is what the pool weighs; it is not the
        # same evidence as a published zero and is not recorded as if it were.
        priced = False
    free = entry.get("free")
    if free is None:
        free = bool(spec.free_tier) and not priced

    # "The catalogue did not say" is its own answer. `tool_call` still gets an
    # optimistic value so a picker has something to show, but `tool_call_known`
    # is what anything making a decision must read — the free pool admits on
    # established support, never on this default.
    raw_tools = entry.get("tool_call", entry.get("tools"))
    known = entry.get("tool_call_known")
    if known is None:
        known = raw_tools is not None
    return {
        "id": model_id,
        "label": entry.get("label") or model_id,
        "context_window": int(entry.get("context_window") or 0),
        "priced": bool(priced),
        "free": bool(free),
        "tool_call": bool(raw_tools) if raw_tools is not None else True,
        "tool_call_known": bool(known),
        "vision": bool(entry.get("vision", False)),
    }


def catalog_for(provider_id: str, api_key: str = "") -> list[dict]:
    """The model catalogue for one provider id, in CANONICAL_CATALOG_KEYS.

    Never raises: a menu being drawn must not die because an endpoint is
    down. An empty list is the caller's cue to say so — which is a gap the
    user can see, rather than a traceback.
    """
    spec = registry.spec(provider_id)
    if spec is None:
        return []
    fetcher = CATALOG_FETCHERS.get(spec.models)
    if fetcher is None:
        return []
    try:
        raw = fetcher(api_key) or []
    except Exception:
        return []
    return [_normalise_catalog_entry(e, spec) for e in raw
            if isinstance(e, dict) and (e.get("id") or e.get("name"))]
