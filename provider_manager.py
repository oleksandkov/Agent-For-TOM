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

TOMAS_DIR = Path.home() / ".tomas"
PROVIDERS_CONFIG_PATH = TOMAS_DIR / "providers.json"
ENV_FILE = TOMAS_DIR / ".env"

# Types we know how to talk to. "custom" is a first-class answer, not a
# failure: an unrecognised OpenAI-compatible endpoint should work.
PROVIDER_TYPES = (
    "anthropic", "openai", "openrouter", "zen", "google", "ollama", "custom",
)

# Endpoints that speak OpenAI wire format rather than Anthropic's.
#: Google is on this list because it publishes an OpenAI-compatible endpoint
#: (`/v1beta/openai/chat/completions`) that speaks the same wire format as the
#: rest — verified against it directly: native `tool_calls` come back, not
#: prose. Before this it was a provider you could *configure* and not use. The
#: setup page said so in as many words ("Google AI is saved but the agent uses
#: the ANTHROPIC_* env vars for API calls"), which is a menu entry that leads
#: nowhere wearing a disclaimer.
OPENAI_WIRE_TYPES = ("openai", "openrouter", "zen", "google", "ollama", "custom")

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
    # 8192, not 4096. Nothing probes this field — grep it — so the default is
    # the value every provider actually runs with, and `agent.py` applies it as
    # `min(MAX_TOKENS, max_output_tokens)`, i.e. a hard ceiling. A pessimistic
    # unmeasured guess therefore capped *every* provider at 4096 output tokens
    # forever, which breaks reasoning models outright: they spend the budget on
    # internal reasoning, get truncated before emitting any content or tool
    # call, and the turn comes back empty. That is this class's documented
    # contract violated by its own default — "defaults are the optimistic case;
    # probing only ever narrows them".
    max_output_tokens: int = 8192
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
    """
    base = (base_url or "").lower()
    if not base:
        return "anthropic"
    if "openrouter" in base:
        return "openrouter"
    if "opencode" in base or ":6446" in base:
        return "zen"
    if "11434" in base or "ollama" in base:
        return "ollama"
    if "api.anthropic.com" in base or "anthropic" in base:
        return "anthropic"
    if "generativelanguage" in base or "googleapis" in base:
        return "google"
    if "api.openai.com" in base or "openai" in base:
        return "openai"
    return "custom"


# ══════════════════════════════════════════════════════════════════════
#  Probing
# ══════════════════════════════════════════════════════════════════════

# Ceilings that are a documented property of the endpoint rather than
# something worth spending a probe on.
KNOWN_TOOL_CEILINGS = {
    "anthropic": 128,
    "openai": 128,
    "openrouter": 128,
    "zen": 32,        # free-tier payload limit, measured
    "ollama": 64,
    "google": 128,
    "custom": 128,
}

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

    key = provider.api_key
    if not key:
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
    if provider.base_url and "ANTHROPIC_BASE_URL" not in (provider.env or {}):
        apply_env("ANTHROPIC_BASE_URL", provider.base_url)
    if provider.model:
        apply_env("AGENT_MODEL", provider.model)
    if provider.extra_headers:
        apply_env("ANTHROPIC_EXTRA_HEADERS", json.dumps(provider.extra_headers))

    config = load_config()
    config["active"] = name
    save_config(config)

    try:
        from agent import reinit_client
        reinit_client()
    except Exception:
        pass
    return True


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
