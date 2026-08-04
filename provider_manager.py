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
OPENAI_WIRE_TYPES = ("openai", "openrouter", "zen", "ollama", "custom")

OLLAMA_DEFAULT_URL = "http://localhost:11434/v1"


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
    max_output_tokens: int = 4096
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

def load_config() -> dict:
    try:
        if PROVIDERS_CONFIG_PATH.exists():
            return json.loads(PROVIDERS_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"active": None, "providers": {}}


def save_config(config: dict) -> None:
    PROVIDERS_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROVIDERS_CONFIG_PATH.write_text(
        json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")


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


def _headers_for(provider: Provider) -> dict:
    headers = {"Content-Type": "application/json"}
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
    base = (provider.base_url or "").rstrip("/")
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


def _probe_context_window(provider: Provider) -> int:
    """Ask the endpoint for the model's real context window."""
    base = (provider.base_url or "").rstrip("/")
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


def _completions_url(provider: Provider) -> str:
    base = (provider.base_url or "").rstrip("/")
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
    try:
        if kw.get("stream"):
            req = urllib.request.Request(
                url, data=json.dumps(body).encode("utf-8"), method="POST")
            for k, v in _headers_for(provider).items():
                req.add_header(k, v)
            with urllib.request.urlopen(req, timeout=_PROBE_TIMEOUT) as resp:
                chunk = resp.read(64)
                # A real stream announces itself; a JSON blob is not a stream.
                ctype = resp.headers.get("Content-Type", "")
                return "event-stream" in ctype or chunk.lstrip().startswith(b"data:")
        _post_json(url, _headers_for(provider), body)
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

    cw = _probe_context_window(provider)
    if cw:
        caps.context_window = cw
    elif provider.type == "ollama":
        # Local models are usually far smaller than the cloud default, and
        # compaction must respect the real number.
        caps.context_window = 8192

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
