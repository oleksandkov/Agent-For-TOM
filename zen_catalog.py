"""
What OpenCode Zen actually serves, right now, and which of it is free.

`zen_proxy.ZEN_MODELS` is a list someone typed out and dated "as of July 2026".
Checked against the upstream on 2026-08-13 it was wrong in seven places:

    gone:      claude-opus-4-1, ling-3.0-flash-free, north-mini-code-free
    missing:   grok-4.6, hy3-free, ling-3.0-tiny-free,
               nemotron-3.5-lightning-free

Three models the picker offered no longer exist, and four that do were
invisible. That is what a hardcoded catalogue does between edits, and it gets
worse, never better — so the list is fetched.

The heading was wrong too, and more expensively. All sixty entries were shown
under "── Zen free-tier models ──" while **eight** of them are free; the other
fifty-three bill. The first-run path says "No provider configured — using the
OpenCode Zen free tier" and then selects `ZEN_MODELS[0]`, which is
`claude-fable-5` — a paid model, chosen for a user who was told they were on
the free tier. `free_models()` and `default_free_model()` exist for that.

**Two sources, because one cannot answer both questions.**

* `opencode.ai/zen/v1/models` — 5 KB, no auth, ~0.3 s — says what is
  *available*. It is the only authority on that, and it carries nothing but
  ids.
* `models.dev/api.json` — the catalogue OpenCode itself publishes — says what
  each model *is*: context window, output limit, price, tool support. It lists
  89 models under `opencode`, including deprecated and withdrawn ones, so it
  cannot be used for availability: 28 of its entries are not served.

Availability comes from the first, description from the second, and a model
missing from models.dev is still offered — with unknown metadata — because
being unable to describe something is not a reason to hide it.

**Free is read from price, never from the name.** Seven of the eight free
models end in `-free` and `big-pickle` does not, so a suffix test would have
missed it; `minimax-m2.1` and `minimax-m2.1-free` are different models at
different prices, so the suffix is not decorative either. `cost.input == 0 and
cost.output == 0` is the actual question. This is the same rule
`provider_manager` follows for capabilities: nothing infers behaviour from
substrings in a name.

Nothing here raises. Every fetch degrades to the disk cache, then to
`zen_proxy`'s static list, so an offline TUI shows a stale catalogue rather
than an empty menu — clearly marked, via `Catalog.source`.
"""

from __future__ import annotations

import gzip
import io
import json
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

ZEN_MODELS_URL = "https://opencode.ai/zen/v1/models"
MODELS_DEV_URL = "https://models.dev/api.json"
MODELS_DEV_PROVIDER = "opencode"

CACHE_PATH = Path.home() / ".tomas" / "zen_catalog.json"

#: How long a fetched catalogue is trusted on disk. Zen adds and retires
#: models over days, not minutes, and this is read while a menu is being drawn.
CACHE_TTL = 6 * 3600

#: The availability call is small and on the critical path of a menu redraw.
#: The metadata call is 309 KB gzipped and only makes the labels better, so it
#: gets a tighter budget and is dropped without complaint.
_AVAILABILITY_TIMEOUT = 8.0
_METADATA_TIMEOUT = 12.0

#: Assumed when models.dev has never been reached. The same number
#: `zen_proxy` defaulted to, and wrong for plenty of models — which is why it
#: is reported as `context_known=False` rather than shown as a fact.
DEFAULT_CONTEXT = 128_000


@dataclass
class ZenModel:
    id: str
    name: str = ""
    context: int = DEFAULT_CONTEXT
    output: int = 0
    free: bool = False
    tool_call: bool = True
    vision: bool = False
    reasoning: bool = False
    #: False when the window is the assumed default rather than a looked-up
    #: one, so a caller can render "?" instead of a plausible wrong number.
    context_known: bool = False
    #: False once a request to this model has actually been refused — see
    #: `mark_unavailable`. Being listed is not being served, and the listing is
    #: the only thing the availability endpoint can tell us.
    served: bool = True

    @property
    def label(self) -> str:
        """One line for a menu: what it costs, how big it is, what it can do."""
        window = f"{self.context:,} ctx" if self.context_known else "? ctx"
        marks = [window]
        if self.free:
            marks.append("free")
        if not self.tool_call:
            marks.append("no tools")
        if self.vision:
            marks.append("vision")
        # Last and unmissable: a menu line whose most important word is buried
        # after the context size has failed at its one job.
        if not self.served:
            marks.append("UNAVAILABLE")
        return f"{self.id} ({' · '.join(marks)})"


@dataclass
class Catalog:
    models: list[ZenModel] = field(default_factory=list)
    #: "live", "cache" or "static" — what the caller is actually looking at.
    #: A stale list shown as if it were current is how the hardcoded one
    #: survived three model retirements without anyone noticing.
    source: str = "static"
    fetched_at: float = 0.0
    #: True when models.dev answered, i.e. free/context are measured rather
    #: than assumed.
    described: bool = False

    def __bool__(self) -> bool:
        return bool(self.models)

    @property
    def age_seconds(self) -> float:
        return max(0.0, time.time() - self.fetched_at) if self.fetched_at else 0.0

    @property
    def freshness(self) -> str:
        """How much to trust this list, in words a menu can print.

        `source` alone is not it: "cache" reads like a degradation and a cache
        minutes old is the normal fast path. Only the static list, and a cache
        past its TTL, are worth qualifying.
        """
        if self.source == "static":
            return "built-in list — offline, may be out of date"
        if self.source == "cache":
            hours = int(self.age_seconds // 3600)
            return f"cached {hours}h ago" if hours else "cached just now"
        return "live from opencode.ai"

    def free(self) -> list[ZenModel]:
        return [m for m in self.models if m.free]

    def paid(self) -> list[ZenModel]:
        return [m for m in self.models if not m.free]

    def usable(self) -> list[ZenModel]:
        """Served, and able to call tools — what an agent can actually run on.

        Kept separate from `free()`/`paid()` rather than filtering them: a
        picker should still *show* a model that refused, marked as such, so the
        user can see why it vanished. Only automatic selection needs it gone.
        """
        return [m for m in self.models if m.served and m.tool_call]

    def get(self, model_id: str) -> Optional[ZenModel]:
        return next((m for m in self.models if m.id == model_id), None)


# ── fetching ──────────────────────────────────────────────────────────

def _get_json(url: str, timeout: float) -> Optional[dict]:
    """GET and decode JSON, gzip included. None on any failure.

    `Accept-Encoding: gzip` is not decorative here: models.dev is 3.6 MB of
    JSON that compresses to 309 KB, and urllib neither asks for nor unwraps
    compression on its own.
    """
    request = urllib.request.Request(url, headers={
        "User-Agent": "TOMAS/1.0",
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            if response.headers.get("Content-Encoding", "") == "gzip":
                raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
        return json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        return None


def _available_ids(timeout: float = _AVAILABILITY_TIMEOUT) -> Optional[list[str]]:
    """The ids Zen serves, in the order it lists them."""
    data = _get_json(ZEN_MODELS_URL, timeout)
    if not isinstance(data, dict):
        return None
    ids = [entry.get("id") for entry in data.get("data") or []
           if isinstance(entry, dict) and entry.get("id")]
    return ids or None


def _describe(timeout: float = _METADATA_TIMEOUT) -> dict[str, dict]:
    """models.dev's entry for every opencode model, keyed by id. {} on failure."""
    data = _get_json(MODELS_DEV_URL, timeout)
    if not isinstance(data, dict):
        return {}
    provider = data.get(MODELS_DEV_PROVIDER)
    if not isinstance(provider, dict):
        return {}
    models = provider.get("models")
    return models if isinstance(models, dict) else {}


def _is_free(entry: dict) -> bool:
    """Zero in *and* zero out. See the module docstring on why not the name.

    A model with no cost block at all is not assumed free: an unpriced entry
    means models.dev has not described it, and guessing "free" there would put
    a billing model under a free heading — the exact error being fixed.
    """
    cost = entry.get("cost")
    if not isinstance(cost, dict):
        return False
    try:
        return float(cost.get("input", 1) or 0) == 0 and \
               float(cost.get("output", 1) or 0) == 0
    except (TypeError, ValueError):
        return False


def _model_from(model_id: str, entry: Optional[dict]) -> ZenModel:
    if not isinstance(entry, dict):
        return ZenModel(id=model_id, name=model_id)
    limit = entry.get("limit") if isinstance(entry.get("limit"), dict) else {}
    modalities = entry.get("modalities") or {}
    inputs = modalities.get("input") or [] if isinstance(modalities, dict) else []
    context = limit.get("context")
    return ZenModel(
        id=model_id,
        name=entry.get("name") or model_id,
        context=int(context) if context else DEFAULT_CONTEXT,
        output=int(limit.get("output") or 0),
        free=_is_free(entry),
        # Absent means "not stated", and every Zen model that states it says
        # true. Defaulting to False would mark the whole catalogue "no tools".
        tool_call=bool(entry.get("tool_call", True)),
        vision="image" in inputs,
        reasoning=bool(entry.get("reasoning", False)),
        context_known=bool(context),
    )


# ── cache ─────────────────────────────────────────────────────────────

def _read_cache() -> Optional[Catalog]:
    try:
        raw = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(raw, dict) or not isinstance(raw.get("models"), list):
        return None
    models = []
    for item in raw["models"]:
        if isinstance(item, dict) and item.get("id"):
            models.append(ZenModel(**{k: v for k, v in item.items()
                                      if k in ZenModel.__dataclass_fields__}))
    if not models:
        return None
    return Catalog(models=models, source="cache",
                   fetched_at=float(raw.get("fetched_at") or 0),
                   described=bool(raw.get("described")))


def _write_cache(catalog: Catalog) -> None:
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps({
            "fetched_at": catalog.fetched_at,
            "described": catalog.described,
            "models": [vars(m) for m in catalog.models],
        }, indent=1), encoding="utf-8")
    except Exception:
        pass       # a catalogue that cannot be cached still works


def _static() -> Catalog:
    """The last resort: the list compiled into `zen_proxy`.

    Known stale — that is the whole reason this module exists — so it is
    labelled `static` and callers say so rather than presenting it as current.
    """
    try:
        from zen_proxy import ZEN_MODELS, MODEL_CONTEXT_WINDOWS
    except Exception:
        return Catalog()
    models = []
    for model_id in ZEN_MODELS:
        window = MODEL_CONTEXT_WINDOWS.get(model_id)
        models.append(ZenModel(
            id=model_id, name=model_id,
            context=int(window or DEFAULT_CONTEXT),
            free=model_id.endswith("-free"),   # the only signal a static list has
            context_known=bool(window)))
    return Catalog(models=models, source="static")


# ── verified-unavailable models ───────────────────────────────────────
#
# Availability comes from `opencode.ai/zen/v1/models`, and that endpoint is
# wrong: on 2026-08-13 it listed `ling-3.0-tiny-free`, which answers
# `401 ModelError: Model ling-3.0-tiny-free is not supported` to every request.
# The picker offered it, the agent selected it, and the session died on turn 1.
#
# So a *refusal actually observed* outranks the listing. `mark_unavailable` is
# called by the host when `core.loop` reports one, and the record expires:
# models come back, and a permanent blacklist written from one bad afternoon is
# a worse failure than the one it prevents. Nothing here raises — an
# unwritable cache means the next session rediscovers the refusal, which is
# exactly today's behaviour.

UNAVAILABLE_PATH = Path.home() / ".tomas" / "zen_unavailable.json"

#: How long an observed refusal is trusted. A day: long enough to stop the
#: picker re-offering a dead model all session, short enough that a model
#: restored upstream is offered again the next day without anyone editing JSON.
UNAVAILABLE_TTL = 24 * 3600


def _read_unavailable() -> dict[str, dict]:
    try:
        raw = json.loads(UNAVAILABLE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    now = time.time()
    return {k: v for k, v in raw.items()
            if isinstance(v, dict)
            and now - float(v.get("at") or 0) < UNAVAILABLE_TTL}


def unavailable() -> dict[str, dict]:
    """Models observed refusing, within the TTL. `{id: {"at", "reason"}}`."""
    return _read_unavailable()


def mark_unavailable(model_id: str, reason: str = "") -> None:
    """Record that `model_id` refused. Silent on any failure."""
    if not model_id:
        return
    try:
        current = _read_unavailable()
        current[model_id] = {"at": time.time(), "reason": (reason or "")[:200]}
        UNAVAILABLE_PATH.parent.mkdir(parents=True, exist_ok=True)
        UNAVAILABLE_PATH.write_text(json.dumps(current, indent=1),
                                    encoding="utf-8")
    except Exception:
        pass


def clear_unavailable(model_id: str = "") -> None:
    """Forget one refusal, or all of them. For `/model` retries and tests."""
    try:
        if not model_id:
            UNAVAILABLE_PATH.unlink(missing_ok=True)
            return
        current = _read_unavailable()
        current.pop(model_id, None)
        UNAVAILABLE_PATH.write_text(json.dumps(current, indent=1),
                                    encoding="utf-8")
    except Exception:
        pass


# ── public surface ────────────────────────────────────────────────────

def fetch(timeout: float = _AVAILABILITY_TIMEOUT) -> Optional[Catalog]:
    """Ask upstream. None when availability could not be established.

    Metadata failing is survivable and does not make this return None: a list
    of the right models with unknown windows beats a stale list of the wrong
    ones.
    """
    ids = _available_ids(timeout)
    if not ids:
        return None
    described = _describe()
    catalog = Catalog(
        models=[_model_from(model_id, described.get(model_id)) for model_id in ids],
        source="live", fetched_at=time.time(), described=bool(described))
    _write_cache(catalog)
    return catalog


def catalog(refresh: bool = False, allow_network: bool = True) -> Catalog:
    """The best catalogue available, cheapest first.

    Fresh disk cache → network → stale disk cache → static list. The stale
    cache outranks the static list even when it is days old, because a
    catalogue that was once true is closer to the truth than one that was
    typed by hand.
    """
    cached = _read_cache()
    if not refresh and cached and cached.age_seconds < CACHE_TTL:
        return _stamp_served(cached)
    if allow_network:
        fresh = fetch()
        if fresh:
            return _stamp_served(fresh)
    if cached:
        return _stamp_served(cached)
    return _stamp_served(_static())


def _stamp_served(catalog: Catalog) -> Catalog:
    """Apply observed refusals to whatever list we ended up with.

    Applied here rather than at fetch time so it survives every source — a
    refusal observed five minutes ago must still be known when the answer came
    from the disk cache or the static list, which is exactly when a stale
    listing is most likely to be wrong.
    """
    refused = _read_unavailable()
    if refused:
        for model in catalog.models:
            if model.id in refused:
                model.served = False
    return catalog


def free_models(**kw) -> list[ZenModel]:
    """Only what costs nothing. The list the free-tier prompts should show."""
    return catalog(**kw).free()


def context_window(model_id: str, **kw) -> int:
    """The model's window, or the assumed default when nothing knows it."""
    entry = catalog(**kw).get(model_id)
    return entry.context if entry else DEFAULT_CONTEXT


def default_free_model(**kw) -> str:
    """A free model to start on, for the paths that promise the free tier.

    Ranked by what makes an agent work rather than by what is biggest: a model
    that cannot call tools is not a candidate at all here, and among those that
    can, more context is better. Falls back to `zen_proxy`'s first entry only
    when nothing free can be established — a wrong default beats no default at
    the point where the alternative is an unconfigured agent.
    """
    free = [m for m in catalog(**kw).free() if m.tool_call and m.served]
    if free:
        return max(free, key=lambda m: (m.context_known, m.context)).id
    try:
        from zen_proxy import ZEN_MODELS
        return ZEN_MODELS[0]
    except Exception:
        return ""


def model_ids(**kw) -> list[str]:
    """Every served id, upstream's order. Drop-in for `ZEN_MODELS`."""
    return [m.id for m in catalog(**kw).models]
