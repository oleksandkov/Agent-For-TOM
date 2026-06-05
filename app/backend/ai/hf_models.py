"""
Model catalog for HuggingFace Inference Providers.

Holds a curated FALLBACK list per provider (so the UI is never empty) and a
live `list_available_models()` that hits `HfApi().list_models(inference_provider=…)`
and caches the result for 5 minutes.

Cerebras in 2026 has only two chat models exposed via the router, so the
fallback list is the practical source of truth on that provider.
"""

from __future__ import annotations

import time
from typing import Optional

DEFAULT_PROVIDER = "cerebras"

# (provider, model_id) -> human-readable note for the UI.
# Curated. Keep at least 3-4 chat-capable models per provider.
FALLBACK_MODELS: dict[str, list[dict]] = {
    "cerebras": [
        {"id": "openai/gpt-oss-120b", "provider": "cerebras",
         "note": "Найпопулярніша безкоштовна модель на Cerebras (120B MoE, open)."},
        {"id": "zai-org/GLM-4.7", "provider": "cerebras",
         "note": "GLM 4.7 (MoE, open). Добра якість української."},
    ],
    "novita": [
        {"id": "deepseek-ai/DeepSeek-V4-Pro", "provider": "novita",
         "note": "DeepSeek V4 Pro — топова модель для reasoning."},
        {"id": "deepseek-ai/DeepSeek-V4-Flash", "provider": "novita",
         "note": "Швидка версія DeepSeek V4."},
        {"id": "meta-llama/Llama-3.3-70B-Instruct", "provider": "novita",
         "note": "Llama 3.3 70B (потребує прийняття ліцензії)."},
        {"id": "meta-llama/Llama-3.1-8B-Instruct", "provider": "novita",
         "note": "Llama 3.1 8B — компактна, швидка."},
        {"id": "openai/gpt-oss-120b", "provider": "novita",
         "note": "OpenAI gpt-oss-120B (open)."},
        {"id": "openai/gpt-oss-20b", "provider": "novita",
         "note": "OpenAI gpt-oss-20B (open)."},
        {"id": "MiniMaxAI/MiniMax-M2.7", "provider": "novita",
         "note": "MiniMax-M2.7 (open)."},
        {"id": "Qwen/Qwen3-Coder-Next", "provider": "novita",
         "note": "Qwen3 Coder Next — добрий для технічних текстів."},
        {"id": "deepseek-ai/DeepSeek-R1", "provider": "novita",
         "note": "DeepSeek R1 — для складних reasoning-задач."},
    ],
    "together": [
        {"id": "deepseek-ai/DeepSeek-V4-Pro", "provider": "together",
         "note": "DeepSeek V4 Pro на Together."},
        {"id": "openai/gpt-oss-120b", "provider": "together",
         "note": "OpenAI gpt-oss-120B (open)."},
        {"id": "openai/gpt-oss-20b", "provider": "together",
         "note": "OpenAI gpt-oss-20B (open)."},
        {"id": "meta-llama/Llama-3.3-70B-Instruct", "provider": "together",
         "note": "Llama 3.3 70B."},
        {"id": "Qwen/Qwen2.5-7B-Instruct", "provider": "together",
         "note": "Qwen 2.5 7B — компактна."},
        {"id": "MiniMaxAI/MiniMax-M2.7", "provider": "together",
         "note": "MiniMax-M2.7 (open)."},
        {"id": "meta-llama/Meta-Llama-3-8B-Instruct", "provider": "together",
         "note": "Llama 3 8B — класика."},
        {"id": "Qwen/Qwen3-235B-A22B-Instruct-2507", "provider": "together",
         "note": "Qwen3 235B MoE."},
        {"id": "zai-org/GLM-5.1", "provider": "together",
         "note": "GLM 5.1 — нова модель Zhipu."},
    ],
    "hf-inference": [
        {"id": "meta-llama/Llama-3.1-8B-Instruct", "provider": "hf-inference",
         "note": "Llama 3.1 8B (офіційний HF inference)."},
        {"id": "meta-llama/Meta-Llama-3-8B-Instruct", "provider": "hf-inference",
         "note": "Llama 3 8B."},
    ],
}


def get_default_for_provider(provider: str) -> str:
    """Return the recommended default model id for a provider."""
    p = (provider or "").lower()
    if p == "cerebras":
        return "openai/gpt-oss-120b"
    if p == "novita":
        return "deepseek-ai/DeepSeek-V4-Pro"
    if p == "together":
        return "deepseek-ai/DeepSeek-V4-Pro"
    if p == "hf-inference":
        return "meta-llama/Llama-3.1-8B-Instruct"
    return "openai/gpt-oss-120b"


def get_fallback_models(provider: str) -> list[dict]:
    """Return curated list for a provider. Always works (no API call)."""
    return list(FALLBACK_MODELS.get((provider or "").lower(), []))


_CACHE: dict[tuple, dict] = {}
_CACHE_TTL = 300  # 5 minutes


def _cache_key(provider: str, api_key: Optional[str], chat_only: bool, limit: int) -> tuple:
    return (
        (provider or "").lower(),
        (api_key or "")[-6:] if api_key else "",
        bool(chat_only),
        int(limit),
    )


def list_available_models(
    api_key: Optional[str],
    provider: str = DEFAULT_PROVIDER,
    limit: int = 30,
    chat_only: bool = True,
    force_refresh: bool = False,
) -> dict:
    """Return a list of available chat models for a provider.

    Tries the live HF API first; on any failure (no token, network, auth)
    returns the curated FALLBACK list so the UI is never empty.

    Returns: {"provider": str, "default": str, "models": [...], "source": "api"|"fallback", "error": str|None}
    """
    p = (provider or DEFAULT_PROVIDER).lower()
    default = get_default_for_provider(p)
    key = _cache_key(p, api_key, chat_only, limit)
    now = time.time()
    cached = _CACHE.get(key)
    if not force_refresh and cached and now - cached["ts"] < _CACHE_TTL:
        return {
            "provider": p,
            "default": default,
            "models": cached["models"],
            "source": "cache",
            "error": None,
        }

    if not api_key:
        return {
            "provider": p,
            "default": default,
            "models": get_fallback_models(p),
            "source": "fallback",
            "error": "no_token",
        }

    try:
        from huggingface_hub import HfApi
        api = HfApi(token=api_key)
        raw = list(api.list_models(inference_provider=p, limit=limit))
    except Exception as e:
        return {
            "provider": p,
            "default": default,
            "models": get_fallback_models(p),
            "source": "fallback",
            "error": f"list_models failed: {e}",
        }

    models: list[dict] = []
    for m in raw:
        pipeline = getattr(m, "pipeline_tag", None)
        if chat_only and pipeline not in ("text-generation", "conversational"):
            continue
        models.append({
            "id": m.id,
            "provider": p,
            "pipeline_tag": pipeline,
            "downloads": int(getattr(m, "downloads", 0) or 0),
            "likes": int(getattr(m, "likes", 0) or 0),
        })
    models.sort(key=lambda x: (x["downloads"], x["likes"]), reverse=True)

    if not models:
        # Live query returned nothing usable (e.g. provider has no text-gen
        # models). Fall back so the UI is still populated.
        return {
            "provider": p,
            "default": default,
            "models": get_fallback_models(p),
            "source": "fallback",
            "error": "no_chat_models",
        }

    # Make sure the default is in the list.
    if not any(m["id"] == default for m in models):
        models.insert(0, {"id": default, "provider": p, "pipeline_tag": "text-generation",
                          "downloads": 0, "likes": 0, "is_default": True})

    _CACHE[key] = {"ts": now, "models": models}
    return {
        "provider": p,
        "default": default,
        "models": models,
        "source": "api",
        "error": None,
    }
