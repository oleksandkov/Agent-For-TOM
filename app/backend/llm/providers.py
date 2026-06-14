"""Thin HTTP wrappers for the remote LLM synthesis cascade."""
from __future__ import annotations

import time
from typing import Any

from app.backend.llm.synthesizer import (
    SCHEMA,
    SYSTEM_PROMPT,
    _extract_json_object,
    _validate_against_schema,
)
from app.backend.pipeline.utils import (
    get_env,
    get_gemini_api_key,
    get_gemini_model,
    get_groq_api_key,
    get_groq_model,
    get_openrouter_api_key,
    get_openrouter_model,
)

SOURCE_GEMINI = "remote_gemini"
SOURCE_OPENROUTER = "remote_openrouter"
SOURCE_GROQ = "remote_groq"

_DEFAULT_CASCADE = "gemini,openrouter,groq"


def _metrics(source: str, model: str) -> dict[str, Any]:
    return {"source": source, "model": model}


def _parse_json_response(text: str, metrics: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    metrics["raw_response"] = text
    obj = _extract_json_object(text)
    if obj is None:
        metrics["error"] = "JSON parse failed"
        return None, metrics
    schema_errors = _validate_against_schema(obj)
    if schema_errors:
        metrics["error"] = "schema validation failed: " + "; ".join(schema_errors)
        return None, metrics
    return obj, metrics


def _call_gemini_json_llm(prompt: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    api_key = get_gemini_api_key()
    model = get_gemini_model()
    metrics = _metrics(SOURCE_GEMINI, model)
    if not api_key:
        metrics["error"] = "no GOOGLE_API_KEY"
        return None, metrics

    try:
        import httpx

        response = httpx.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            params={"key": api_key},
            json={
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
                "generationConfig": {
                    "temperature": 0.2,
                    "maxOutputTokens": 8000,
                    "responseMimeType": "application/json",
                },
            },
            timeout=120.0,
        )
        response.raise_for_status()
        data = response.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        usage = data.get("usageMetadata") or {}
        metrics["prompt_tokens"] = int(usage.get("promptTokenCount", 0) or 0)
        metrics["output_tokens"] = int(usage.get("candidatesTokenCount", 0) or 0)
    except Exception as exc:  # noqa: BLE001
        metrics["error"] = f"{type(exc).__name__}: {exc}"
        return None, metrics

    return _parse_json_response(text, metrics)


def _call_openrouter_json_llm(prompt: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    api_key = get_openrouter_api_key()
    model = get_openrouter_model()
    metrics = _metrics(SOURCE_OPENROUTER, model)
    if not api_key:
        metrics["error"] = "no OPENROUTER_API_KEY"
        return None, metrics

    models_to_try = [model]
    if "free" in model and model != "openrouter/free":
        models_to_try.append("openrouter/free")

    last_exc = None
    for current_model in models_to_try:
        metrics["model"] = current_model
        try:
            import httpx

            response = httpx.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "HTTP-Referer": "https://github.com/agent-for-tom",
                    "X-Title": "Agent-For-TOM",
                },
                json={
                    "model": current_model,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": 8000,
                    "temperature": 0.2,
                },
                timeout=120.0,
            )
            response.raise_for_status()
            data = response.json()
            if "error" in data:
                raise RuntimeError(str(data["error"]))
            text = data["choices"][0]["message"]["content"] or ""
            usage = data.get("usage") or {}
            metrics["prompt_tokens"] = int(usage.get("prompt_tokens", 0) or 0)
            metrics["output_tokens"] = int(usage.get("completion_tokens", 0) or 0)

            obj, res_metrics = _parse_json_response(text, metrics)
            if obj is not None:
                return obj, res_metrics
            last_exc = RuntimeError(res_metrics.get("error", "JSON parse/validation failed"))
        except Exception as exc:  # noqa: BLE001
            last_exc = exc

    metrics["error"] = f"{type(last_exc).__name__}: {last_exc}"
    return None, metrics


def _call_groq_json_llm(prompt: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    api_key = get_groq_api_key()
    model = get_groq_model()
    metrics = _metrics(SOURCE_GROQ, model)
    if not api_key:
        metrics["error"] = "no GROQ_API_KEY"
        return None, metrics

    try:
        from groq import Groq

        client = Groq(api_key=api_key, timeout=120.0)
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            max_tokens=8000,
            temperature=0.2,
        )
        text = response.choices[0].message.content or ""
        usage = getattr(response, "usage", None)
        if usage is not None:
            metrics["prompt_tokens"] = int(getattr(usage, "prompt_tokens", 0) or 0)
            metrics["output_tokens"] = int(getattr(usage, "completion_tokens", 0) or 0)
    except Exception as exc:  # noqa: BLE001
        metrics["error"] = f"{type(exc).__name__}: {exc}"
        return None, metrics

    return _parse_json_response(text, metrics)


def _cascade_order() -> list[str]:
    raw = get_env("REMOTE_LLM_PROVIDER") or _DEFAULT_CASCADE
    allowed = {"gemini", "openrouter", "groq"}
    return [p.strip().lower() for p in raw.split(",") if p.strip().lower() in allowed]


def _call_remote_cascade(prompt: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    dispatch = {
        "gemini": _call_gemini_json_llm,
        "openrouter": _call_openrouter_json_llm,
        "groq": _call_groq_json_llm,
    }
    order = _cascade_order()
    errors: list[str] = []
    last_metrics: dict[str, Any] = {}

    for name in order:
        provider_call = dispatch[name]
        t0 = time.perf_counter()
        obj, metrics = provider_call(prompt)
        metrics["duration_ms"] = round((time.perf_counter() - t0) * 1000.0, 1)
        last_metrics = metrics
        if obj is not None:
            metrics["cascade_order"] = order
            metrics["providers_tried"] = [name]
            return obj, metrics
        error = str(metrics.get("error", "unknown"))
        if error.startswith("no ") and "API_KEY" in error:
            continue
        errors.append(f"{name}: {error}")

    return None, {
        "source": "remote_cascade",
        "model": last_metrics.get("model"),
        "error": "all providers failed: " + " | ".join(errors) if errors else "no providers configured",
        "cascade_order": order,
        "providers_tried": order,
        "raw_response": last_metrics.get("raw_response", ""),
    }


__all__ = [
    "_call_gemini_json_llm",
    "_call_openrouter_json_llm",
    "_call_groq_json_llm",
    "_call_remote_cascade",
    "SOURCE_GEMINI",
    "SOURCE_OPENROUTER",
    "SOURCE_GROQ",
]
