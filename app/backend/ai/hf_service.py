"""
HuggingFace Inference API integration.

Uses `huggingface_hub.InferenceClient` (free tier) with the OpenAI-compatible
chat-completions endpoint routed through a provider (default: Cerebras).

HuggingFace's free inference tier does NOT support native JSON-schema
enforcement the way Gemini's `response_schema` does. We compensate with a
prompt-engineered strict-JSON contract and a validate-and-retry loop (up to
3 attempts) that feeds the Pydantic ValidationError back into the prompt
on each failed attempt.
"""

from __future__ import annotations

import json
import re
from typing import Type, TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

DEFAULT_MODEL = "meta-llama/Llama-3.1-8B-Instruct"
DEFAULT_PROVIDER = "cerebras"
MAX_TOKENS = 8000

# Substrings in a model reply that mean "this will never work, don't retry".
# Any of these triggers an immediate RuntimeError that bubbles up to the UI.
_HARD_ERROR_PATTERNS = {
    "not_supported": ["not supported by provider", "is not supported", "model_not_supported"],
    "not_found": ["model not found", "unknown model", "no such model", "404 not found"],
    "gated": ["gated", "access denied", "you need to agree", "accept the license"],
    "auth": ["invalid token", "authentication failed", "401 unauthorized", "403 forbidden"],
    "rate": ["rate limit", "quota exceeded", "too many requests", "503 service"],
}


def _classify_hard_error(content: str) -> str | None:
    """Return the error category if `content` looks like a hard error, else None."""
    lc = content.lower()
    for category, patterns in _HARD_ERROR_PATTERNS.items():
        for p in patterns:
            if p in lc:
                return category
    return None


def _extract_json(text: str) -> str:
    """Pull the first balanced JSON object out of a model reply.

    Strips Markdown code fences and tolerates leading/trailing prose.
    """
    fence = re.search(r"```(?:json)?\s*(\{.*\}|\[.*\])\s*```", text, re.DOTALL)
    if fence:
        return fence.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        return text[start : end + 1]
    raise ValueError("Model response did not contain a JSON object")


def _is_hf_token(token: str) -> bool:
    """A free HF token starts with 'hf_'. Gemini tokens start with 'AIza'."""
    if not token:
        return False
    t = token.strip()
    return t.startswith("hf_") or (t.startswith("hf ") is False and "AIza" not in t and len(t) > 20)


def detect_provider(token: str) -> str:
    """Return 'huggingface' or 'gemini' based on the token prefix."""
    if not token:
        return "gemini"
    t = token.strip()
    if t.startswith("hf_"):
        return "huggingface"
    if t.startswith("AIza"):
        return "gemini"
    return "huggingface"


def generate_validated(
    api_key: str,
    system_prompt: str,
    user_prompt: str,
    schema: Type[T],
    model: str = DEFAULT_MODEL,
    provider: str = DEFAULT_PROVIDER,
    max_retries: int = 3,
) -> T:
    """Call the HF chat-completions API and validate the response against a Pydantic schema.

    Retries up to `max_retries` times. On each failed attempt the previous
    validation error is appended to the user prompt as a self-correction hint.
    """
    try:
        from huggingface_hub import InferenceClient
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "Бібліотеку 'huggingface_hub' не знайдено. Встановіть: py -3 -m pip install huggingface_hub"
        ) from exc

    client = InferenceClient(provider=provider, api_key=api_key)

    last_err: Exception | None = None
    current_user_prompt = user_prompt

    for attempt in range(max_retries):
        try:
            response = client.chat_completion(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": current_user_prompt},
                ],
                max_tokens=MAX_TOKENS,
                temperature=0.5 if attempt == 0 else 0.2,
                top_p=0.95,
            )
            content = (response.choices[0].message.content or "").strip()

            # HARD ERROR CHECK FIRST: if the provider rejected the model or
            # the call, the reply is a plain-text error string. We must NOT
            # treat it as a JSON-parse failure and burn retries on it.
            err_category = _classify_hard_error(content)
            if err_category is not None:
                snippet = content[:400]
                if err_category == "not_supported":
                    raise RuntimeError(
                        f"Модель '{model}' не підтримується провайдером '{provider}'. "
                        "Оберіть іншу модель у випадному списку та натисніть «Згенерувати» ще раз."
                    ) from RuntimeError(snippet)
                if err_category == "not_found":
                    raise RuntimeError(
                        f"Модель '{model}' не знайдена у провайдера '{provider}'. "
                        "Натисніть «Оновити список моделей», щоб побачити доступні."
                    ) from RuntimeError(snippet)
                if err_category == "gated":
                    raise RuntimeError(
                        f"Модель '{model}' захищена ліцензією (gated). "
                        "Відкрийте https://huggingface.co/{model} та прийміть угоду, "
                        "або оберіть іншу модель."
                    ) from RuntimeError(snippet)
                if err_category == "auth":
                    raise RuntimeError(
                        "HuggingFace токен невалідний або відкликаний. "
                        "Перевірте токен на https://huggingface.co/settings/tokens."
                    ) from RuntimeError(snippet)
                if err_category == "rate":
                    raise RuntimeError(
                        "Перевищено ліміт запитів HuggingFace. Спробуйте пізніше "
                        "або оберіть іншу модель."
                    ) from RuntimeError(snippet)
                raise RuntimeError(f"Помилка HuggingFace: {snippet}") from RuntimeError(snippet)

            json_text = _extract_json(content)
            data = json.loads(json_text)
            return schema.model_validate(data)
        except (ValidationError, json.JSONDecodeError, ValueError) as e:
            last_err = e
            if attempt < max_retries - 1:
                current_user_prompt = (
                    user_prompt
                    + "\n\n"
                    + (
                        f"[СИСТЕМНЕ ЗАУВАЖЕННЯ — спроба {attempt + 1} невдала] "
                        f"Твоя попередня відповідь не пройшла валідацію схеми: {e}. "
                        "Поверни ВИКЛЮЧНО один валідний JSON-об'єкт, без жодного пояснення, "
                        "без Markdown-огородження, без коментарів."
                    )
                )
                continue
            raise RuntimeError(
                f"Не вдалося отримати валідний JSON від моделі після {max_retries} спроб: {last_err}"
            ) from last_err
        except Exception as e:
            msg = str(e)
            msg_l = msg.lower()
            if "401" in msg or "unauthorized" in msg_l or "invalid credentials" in msg_l:
                raise RuntimeError(
                    "HuggingFace токен невалідний або відкликаний. "
                    "Перевірте токен на https://huggingface.co/settings/tokens."
                ) from e
            if "403" in msg or "forbidden" in msg_l:
                raise RuntimeError(
                    "Доступ заборонено (403). Можливо, токен не має права 'Make calls to "
                    "Inference Providers'. Створіть новий токен з цим правом, або оберіть "
                    "іншу модель."
                ) from e
            if "429" in msg or "rate" in msg_l or "quota" in msg_l:
                raise RuntimeError(
                    "Перевищено ліміт запитів HuggingFace. Зачекайте або оберіть іншу модель."
                ) from e
            if "gated" in msg_l:
                raise RuntimeError(
                    f"Модель '{model}' захищена (gated). "
                    "Відкрийте https://huggingface.co/{model} та прийміть ліцензійну угоду, "
                    "або оберіть іншу модель."
                ) from e
            raise RuntimeError(f"Помилка HuggingFace API: {msg[:400]}") from e

    raise RuntimeError("Unreachable: retry loop exited without returning")  # pragma: no cover
