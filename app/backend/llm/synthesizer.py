"""app/backend/llm/synthesizer.py

JSON-only LLM-based synthesis of the per-template `gap_values`.

This is the **clean and robust** approach. The model is asked to do
one thing it is good at (text -> strict JSON) instead of text ->
Python. The post-processor (``app.backend.llm.gap_assembler``) then
turns the JSON into the actual `filled.py` — a job it already does
flawlessly.

Five-tier fallback
-------------------
1. **Remote cascade**: Google Gemini 2.5 Flash → OpenRouter free model →
   Groq. The order is configurable with ``REMOTE_LLM_PROVIDER`` as a
   comma-separated list, and the first provider that returns JSON matching
   the schema wins. Missing API keys are skipped.

2. **Remote HuggingFace text LLM** (kept as a compatibility fallback for
   code that still calls ``_call_remote_json_llm`` directly).

3. **Groq** (kept as a compatibility fallback for code that still calls
   ``_call_groq_json_llm`` directly).

4. **Local Qwen 2.5 (GGUF)** via ``llama-cpp-python``. Used when the
   remote cascade fails. 1.5B Q4_K_M is ~1 GB and runs on plain CPU.

5. **gap_assembler pass-through** — only used when all LLM tiers fail.
   Returns the user's typed values verbatim, with no rewriting.

The strict JSON contract is documented in ``SCHEMA`` below. Any
response that does not match is treated as a failure and the next
tier is tried.

Cache
-----
Every successful synthesis writes a row to the ``llm_cache`` table
(keyed by SHA-256 of the prompt inputs). Re-running the same
pipeline with the same input is therefore instant.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# IMPORTANT: this module is imported by both `app.backend.llm.*` and
# `app.backend.pipeline.*`. We must NOT import from `pipeline.utils`
# here (it creates a circular import), and we keep the helper
# definitions local instead.


# ─── Local copies of the few pipeline.utils helpers we need ───────────────
# These are the SAME implementations as in pipeline/utils.py; they
# are duplicated here to avoid the circular import. The two copies
# are kept deliberately small (4 short functions) so a future
# consolidation is trivial.

_DEFAULT_TEXT_LLM_MODEL = "meta-llama/Llama-3.3-70B-Instruct"
_DEFAULT_COMPACT_MODEL = "Qwen/Qwen2.5-1.5B-Instruct-GGUF"
# Default Groq model: llama-3.3-70b-versatile. Free tier, larger,
# produces the 1000-1700 word count needed by the global
# instructions. The 8b-instant model is faster but only emits ~50
# words of general_info which falls far short of the target.
_DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"


def _get_env(name: str) -> str | None:
    import os
    value = os.environ.get(name)
    return value if (value is not None and value != "") else None


def load_env() -> None:
    """Best-effort: load .env at the repo root into os.environ.

    Tolerant: missing file, blank lines, or malformed lines are
    silently skipped. Existing process env values are NOT overridden.
    Inline ``#`` comments after a value are stripped (e.g. ``KEY=foo
    # bar`` -> ``KEY=foo``), but only when the ``#`` is preceded by
    whitespace so we don't strip hashes inside quoted values.
    """
    import os
    from pathlib import Path
    env_file = Path(__file__).resolve().parents[3] / ".env"
    if not env_file.is_file():
        return
    try:
        raw = env_file.read_text(encoding="utf-8")
    except OSError:
        return
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # Strip surrounding quotes.
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        # Strip inline "# comment" after a space.
        if " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        os.environ.setdefault(key, value)


def get_hf_token() -> str | None:
    """Returns None as HuggingFace API usage is removed."""
    return None


def get_text_model() -> str:
    return _get_env("HUGGING_FACE_MODEL") or _get_env("HF_MODEL") or _DEFAULT_TEXT_LLM_MODEL


def get_compact_model() -> str:
    return _get_env("COMPACT_MODEL") or _get_env("HF_COMPACT_MODEL") or _DEFAULT_COMPACT_MODEL


def get_remote_provider() -> str:
    """Return the legacy remote provider selector.

    The production synthesizer now uses ``REMOTE_LLM_PROVIDER`` as a
    comma-separated cascade order in ``app.backend.llm.providers``.
    This helper is kept for backwards compatibility with older callers.
    """
    return (_get_env("REMOTE_LLM_PROVIDER") or "huggingface").strip().lower()


def get_groq_api_key() -> str | None:
    """Return the Groq API key from env, or None if absent.

    Order of precedence: ``GROQ_API_KEY`` (canonical) → ``GROQ_TOKEN``
    (community alias).
    """
    for key in ("GROQ_API_KEY", "GROQ_TOKEN"):
        value = _get_env(key)
        if value:
            return value.strip()
    return None


def get_groq_model() -> str:
    return _get_env("GROQ_MODEL") or _DEFAULT_GROQ_MODEL


# ─── Strict JSON schema ──────────────────────────────────────────────────
#
# The LLM must emit a single JSON object with exactly these five top-level
# keys. Values are either strings (goal, general_info) or arrays of
# strings (tasks, control_questions, bibliography).
#
# `lab_number` is intentionally NOT in the schema — it is a small
# fixed fact that lives in the params file, not something the LLM
# should generate.

SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["goal", "general_info", "tasks", "control_questions", "bibliography"],
    "properties": {
        "goal": {"type": "string", "minLength": 10},
        "general_info": {"type": "string", "minLength": 50},
        "tasks": {"type": "array", "items": {"type": "string", "minLength": 5}, "minItems": 1},
        "control_questions": {"type": "array", "items": {"type": "string", "minLength": 5}, "minItems": 1},
        "bibliography": {"type": "array", "items": {"type": "string", "minLength": 5}, "minItems": 1},
    },
    "additionalProperties": False,
}

# A small, complete example of the expected output. The few-shot
# prompt that follows contains this so the model knows exactly what
# shape to emit.
FEW_SHOT_EXAMPLE: dict[str, Any] = {
    "goal": "дослідити принципи роботи основних алгоритмів сортування масивів (бульбашкового, вибором, вставленням, Шелла та швидкого) та експериментально порівняти їхню часову складність на різних наборах даних.",
    "general_info": "Сортування є фундаментальною операцією в обробці даних. Бульбачкове сортування — найпростіший алгоритм із складністю O(n²); сортування вибором також O(n²) але з меншою кількістю обмінів; сортування вставленням ефективне на малих масивах; алгоритм Шелла зменшує складність до O(n log² n); швидке сортування має середню складність O(n log n) і є найшвидшим на практиці.",
    "tasks": [
        "Реалізувати функції сортування масиву методами бульбашки, вибору, вставлення, Шелла та швидкого сортування.",
        "Згенерувати тестові набори даних (випадкові, відсортовані, обернено відсортовані) різного розміру (N = 100, 1000, 10000).",
        "Виміряти час виконання кожного алгоритму та записати результати у таблицю.",
        "Побудувати графік залежності часу сортування від розміру масиву.",
        "Зробити висновки про доцільність застосування кожного методу.",
    ],
    "control_questions": [
        "У чому полягає принцип роботи алгоритму сортування Шелла?",
        "Чим відрізняється найгірший випадок швидкого сортування від середнього і за яких умов він виникає?",
        "Поясніть, чому алгоритм бульбашкового сортування має складність O(n²) навіть у середньому випадку.",
        "Який з досліджених алгоритмів є стабільним і чому це важливо?",
    ],
    "bibliography": [
        "Кнут, Д. Е. Мистецтво програмування. Т. 3 : Сортування і пошук. Київ : Вільямс, 2020. 824 с.",
        "Кормен, Т. Х. та ін. Вступ до алгоритмів. 3-тє вид. Київ : К.І.С., 2021. 1024 с.",
        "Седжвік, Р. Алгоритми на C++. Київ : Діалектика, 2019. 720 с.",
    ],
}


# ─── Public result type ─────────────────────────────────────────────────

@dataclass
class SynthesisResult:
    """Result of one gap_values synthesis attempt."""

    gap_values: dict[str, Any]               # ready for gap_assembler
    source: str                              # "remote" | "local_qwen" | "user_typed"
    model: str | None
    prompt_tokens: int = 0
    output_tokens: int = 0
    duration_ms: float = 0.0
    cached: bool = False
    error: str | None = None
    raw_response: str = ""                  # for debugging
    prompt: str = ""                        # full prompt text sent to LLM


# ─── Prompt builder ─────────────────────────────────────────────────────

SYSTEM_PROMPT = (
    "You are an assistant that fills a Ukrainian academic document template. "
    "You ALWAYS respond with a single valid JSON object. "
    "No prose before or after the JSON. No markdown fences. No comments. "
    "The JSON object must match the requested schema exactly."
)


def _build_user_prompt(
    *,
    template_id: str,
    theme: str,
    user_goal: str,
    user_input: str,
    length: str,
    hardness: str,
    user_gap_values: dict[str, Any],
    attached_excerpt: str,
    compact_dir: Path | None = None,
) -> str:
    """Build the user-side prompt for the JSON-only synthesis call.

    Structure (in order):
      1. System instructions (general_instructions.md)
      2. Session context (session_context.json)
      3. Template params (labN_params.json)
      4. Special instructions (labN_fill.md)
      5. Attached files metadata (library_files.json)
      6. Attached excerpt / reference material text
      7. Schema validation and few-shot example
    """
    import os as _os
    support_attach = (_os.environ.get("SUPPORT_ATTACH_FILES") or "false").strip().lower() in ("true", "1", "yes")

    general_instructions_content = ""
    session_context_content = ""
    library_files_content = ""
    template_params_content = ""
    template_instructions_content = ""
    user_style_content = ""

    if compact_dir is not None:
        compact_dir = Path(compact_dir)
        
        # 1. Read general_instructions.md
        g_path = compact_dir / "general_instructions.md"
        if g_path.is_file():
            general_instructions_content = g_path.read_text(encoding="utf-8")
        
        # 2. Read session_context.json
        sc_path = compact_dir / "session_context.json"
        if sc_path.is_file():
            session_context_content = sc_path.read_text(encoding="utf-8")
            
        # 3. Read library_files.json (only if support_attach is enabled)
        if support_attach:
            lf_path = compact_dir / "library_files.json"
            if lf_path.is_file():
                library_files_content = lf_path.read_text(encoding="utf-8")
        else:
            library_files_content = "SUPPORT_ATTACH_FILES is disabled in .env. Attached files are ignored."

        # 4. Read template parameters (*_params.json)
        params_files = list(compact_dir.glob("*_params.json"))
        if params_files:
            template_params_content = params_files[0].read_text(encoding="utf-8")

        # 5. Read template instructions (*_fill.md)
        fill_files = list(compact_dir.glob("*_fill.md"))
        if fill_files:
            template_instructions_content = fill_files[0].read_text(encoding="utf-8")

        # 6. Read user_style.md
        us_path = compact_dir / "user_style.md"
        if us_path.is_file():
            user_style_content = us_path.read_text(encoding="utf-8")

    # Fallback for general_instructions if not found in compact_dir
    if not general_instructions_content:
        from app.backend.pipeline.utils import GLOBAL_INSTRUCTIONS
        g_fallback = Path(GLOBAL_INSTRUCTIONS)
        if g_fallback.is_file():
            general_instructions_content = g_fallback.read_text(encoding="utf-8")

    prompt_parts = []
    
    if general_instructions_content:
        prompt_parts.append(f"# SYSTEM INSTRUCTIONS (general_instructions.md)\n{general_instructions_content}\n")
        
    prompt_parts.append("# INPUT JSON DATA & CONTEXT\n")
    
    if session_context_content:
        prompt_parts.append(f"## 1. Session Context (session_context.json)\n```json\n{session_context_content}\n```\n")
    else:
        # Legacy/fallback representation
        legacy_sc = {
            "template_id": template_id,
            "theme": theme,
            "length": length,
            "hardness": hardness,
            "user_input": user_input
        }
        prompt_parts.append(f"## 1. Session Context (session_context.json - fallback)\n```json\n{json.dumps(legacy_sc, ensure_ascii=False, indent=2)}\n```\n")
        
    if template_params_content:
        prompt_parts.append(f"## 2. Template Parameters & Gaps ({template_id}_params.json)\n```json\n{template_params_content}\n```\n")
    else:
        # Legacy/fallback representation of gap values
        user_lines: list[str] = []
        for key, block in user_gap_values.items():
            if not isinstance(block, dict):
                value = block
                ai = True
            else:
                value = block.get("value", "")
                ai = bool(block.get("ai_accessible", True))
            marker = "✎ REWRITE" if ai else "🔒 KEEP"
            if isinstance(value, list):
                value_repr = " | ".join(str(x) for x in value)
            else:
                value_repr = str(value)
            user_lines.append(f"- [{marker}] {key}: {value_repr}")
        user_block = "\n".join(user_lines) if user_lines else "(none)"
        prompt_parts.append(f"## 2. Template Parameters & Gaps (fallback representation)\n{user_block}\n")
        
    if template_instructions_content:
        prompt_parts.append(f"## 3. Template-Specific Instructions ({template_id}_fill.md)\n{template_instructions_content}\n")
        
    prompt_parts.append(f"## 4. Attached Files Metadata (library_files.json)\n```json\n{library_files_content}\n```\n")
    
    if support_attach:
        prompt_parts.append(f"## 5. Reference Material / Attached Files Excerpts\n{attached_excerpt or '(no reference material)'}\n")
    else:
        prompt_parts.append("## 5. Reference Material / Attached Files Excerpts\n(Attached files support is disabled, ignoring attached files)\n")
        
    if user_style_content.strip():
        prompt_parts.append(f"## 6. User Preferred Vocabulary & Style (user_style.md)\n{user_style_content}\n")
    else:
        prompt_parts.append("## 6. User Preferred Vocabulary & Style (user_style.md)\n(no user style provided)\n")
        
    # Schema validation reminder and example
    schema_str = json.dumps(SCHEMA, ensure_ascii=False, indent=2)
    example_json = json.dumps(FEW_SHOT_EXAMPLE, ensure_ascii=False, indent=2)
    
    prompt_parts.append(
        f"# RESPONSE SCHEMA REQUIREMENT\n"
        f"You MUST return a single valid JSON object matching this schema exactly:\n"
        f"```json\n{schema_str}\n```\n\n"
        f"Example of correct response structure (do NOT copy content, just follow shape):\n"
        f"```json\n{example_json}\n```\n\n"
        f"# YOUR TASK\n"
        f"Generate the missing or rewritten gap values (marked as `ai_accessible: true` or ✎ REWRITE) in Ukrainian academic style. "
        f"Ensure that all fields marked as `ai_accessible: false` (🔒 KEEP) are returned verbatim. "
        f"CRITICAL: Adhere to the target word count constraints for the length specified in session_context.json.\n"
        f"Output ONLY the JSON object. Do not include markdown fences, comments, or extra prose."
    )
    
    return "\n".join(prompt_parts)


# ─── JSON parser (with one recovery pass for common LLM slips) ────────

def _extract_json_object(text: str) -> dict[str, Any] | None:
    """Parse a JSON object out of an LLM response.

    Common failure modes we recover from:
      * Wrapped in ```json ... ``` or ``` ... ``` fences.
      * Surrounded by prose like "Here is the JSON: { ... }".
      * Markdown bold/italic delimiters touching the JSON braces.
      * Python-style True/False/None (replaced with JSON true/false/null).
      * Brace characters inside a string (handled by depth scanner
        that respects quoted regions).

    The strategy: try `json.loads(text)` first, then try the same
    with Python-keyword normalisation, then try fence stripping, then
    a depth-aware scan to find the outermost object.
    """
    if not text:
        return None
    text = text.strip()

    def _try_load(s: str) -> dict[str, Any] | None:
        try:
            obj = json.loads(s)
            if isinstance(obj, dict):
                return obj
        except (json.JSONDecodeError, TypeError, ValueError):
            return None
        return None

    def _py_to_json(s: str) -> str:
        # Replace bare Python keywords with JSON equivalents. The
        # lookbehind/lookahead make sure we only touch word
        # boundaries — JSON's `true` inside a string like "true" is
        # left alone (it'd be wrapped in quotes anyway).
        s = re.sub(r'(?<![A-Za-z0-9_"])None(?![A-Za-z0-9_"])', "null", s)
        s = re.sub(r'(?<![A-Za-z0-9_"])True(?![A-Za-z0-9_"])', "true", s)
        s = re.sub(r'(?<![A-Za-z0-9_"])False(?![A-Za-z0-9_"])', "false", s)
        return s

    # 1. Fast path: the whole response is valid JSON.
    obj = _try_load(text)
    if obj is not None:
        return obj

    # 2. Same with Python-keyword normalisation.
    obj = _try_load(_py_to_json(text))
    if obj is not None:
        return obj

    # 3. Strip ```json ... ``` or ``` ... ``` fences if present.
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        obj = _try_load(_py_to_json(fence.group(1)))
        if obj is not None:
            return obj

    # 4. Last resort: find the first '{' and the matching '}' by depth.
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    end = -1
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end < 0:
        return None
    candidate = text[start:end]
    return _try_load(_py_to_json(candidate))


# ─── Schema validator ──────────────────────────────────────────────────

def _validate_against_schema(obj: dict[str, Any]) -> list[str]:
    """Return a list of human-readable errors. Empty = valid.

    Small local models (Qwen 1.5B) often return lists as single
    strings, e.g. ``"tasks": "Do X, Do Y"`` instead of
    ``["Do X", "Do Y"]``. The schema validator is therefore TOLERANT:
    lists are auto-coerced from strings (split by newlines or by a
    numbered list pattern). If the coercion succeeds, the object is
    accepted and the coerced list replaces the original.
    """
    errors: list[str] = []
    for required in SCHEMA["required"]:
        if required not in obj:
            errors.append(f"missing key: {required!r}")
    for key, spec in SCHEMA["properties"].items():
        if key not in obj:
            continue
        value = obj[key]
        if spec.get("type") == "string":
            if not isinstance(value, str):
                errors.append(f"{key!r} must be a string, got {type(value).__name__}")
                continue
            if len(value) < spec.get("minLength", 0):
                errors.append(f"{key!r} is too short ({len(value)} < {spec.get('minLength')})")
        elif spec.get("type") == "array":
            if isinstance(value, list):
                if len(value) < spec.get("minItems", 0):
                    errors.append(f"{key!r} has too few items ({len(value)} < {spec.get('minItems')})")
                continue
            if isinstance(value, str):
                # Coerce: split by newline OR by numbered prefix.
                coerced = _split_string_to_list(value)
                if coerced and len(coerced) >= spec.get("minItems", 1):
                    obj[key] = coerced  # in-place: object is now valid
                    continue
            errors.append(
                f"{key!r} must be a list, got {type(value).__name__}; "
                f"string could not be split into enough items"
            )
    extra = set(obj) - set(SCHEMA["properties"])
    if extra:
        errors.append(f"unexpected keys: {sorted(extra)}")
    return errors


def _split_string_to_list(text: str) -> list[str]:
    """Best-effort split of a model's string-encoded list.

    Recognised patterns (tried in order):
      * "1. item\\n2. item\\n3. item"  (numbered, one per line)
      * "1) item\\n2) item"            (numbered with paren)
      * "- item\\n- item"              (dashes)
      * "item; item; item"            (semicolons)
      * "item | item | item"          (Qwen's preferred separator)
      * "item, item, item"            (commas, only if ≥ 2 commas)
    Returns the cleaned list, or an empty list if the input has no
    recognisable structure.
    """
    if not text or not text.strip():
        return []
    # Try newline split first.
    raw_lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    if len(raw_lines) > 1:
        cleaned: list[str] = []
        for ln in raw_lines:
            cleaned.append(re.sub(r"^\s*(?:\d+[.)]|[-•*])\s*", "", ln).strip())
        cleaned = [c for c in cleaned if c]
        if len(cleaned) >= 2:
            return cleaned
    # Fallback: try a sequence of separators. Order matters: Qwen
    # loves the pipe character.
    for sep in ("; ", " | ", " |", "| ", ";\n", ". ", ", "):
        parts = [p.strip() for p in text.split(sep) if p.strip()]
        if len(parts) >= 2:
            return parts
    return []


def _coerce_to_gap_values(obj: dict[str, Any], user_gap_values: dict[str, Any]) -> dict[str, Any]:
    """Convert the LLM JSON into the schema-aware gap_values format
    that `gap_assembler.render_filled_py` expects.

    The LLM's value wins over the user's existing value, EXCEPT for
    keys that the user marked as `ai_accessible=false` — those are
    preserved verbatim (the LLM is told about this in the prompt and
    the contract is enforced here as a backstop).
    """
    out: dict[str, Any] = {}
    # Start with user values so `lab_number` and other non-LLM keys survive.
    for k, v in user_gap_values.items():
        out[k] = dict(v) if isinstance(v, dict) else {"value": v, "ai_accessible": True}
    # Overwrite / insert LLM values.
    for k, v in obj.items():
        if k not in out:
            out[k] = {"value": v, "ai_accessible": True}
        else:
            existing_ai = out[k].get("ai_accessible", True) if isinstance(out[k], dict) else True
            if existing_ai:
                out[k] = {**out[k], "value": v, "ai_accessible": True}
            # else: leave user-locked value untouched
    return out


# ─── Tier 1: remote HuggingFace text LLM ───────────────────────────────

def _call_remote_json_llm(
    prompt: str,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Call the remote HF LLM and try to parse a JSON object.

    Returns ``(parsed_object, metrics)``. ``parsed_object`` is None
    if the response is not parseable JSON matching the schema.
    """
    token = get_hf_token()
    if not token:
        return None, {"error": "no token"}
    model = get_text_model()
    metrics: dict[str, Any] = {"model": model, "source": "remote"}
    try:
        from huggingface_hub import InferenceClient  # type: ignore
        client = InferenceClient(token=token, timeout=120)
        if hasattr(client, "chat_completion"):
            response = client.chat_completion(
                model=model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=4000,
                temperature=0.2,
            )
        else:
            response = client.chat.completions(
                model=model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=4000,
                temperature=0.2,
            )
        text = response.choices[0].message.content or ""
    except Exception as exc:  # noqa: BLE001
        return None, {"error": f"{type(exc).__name__}: {exc}", "model": model}

    metrics["raw_response"] = text
    try:
        usage = getattr(response, "usage", None) or {}
        if hasattr(usage, "model_dump"):
            usage = usage.model_dump()
        if isinstance(usage, dict):
            metrics["prompt_tokens"] = int(usage.get("prompt_tokens") or 0)
            metrics["output_tokens"] = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    except Exception:
        pass

    obj = _extract_json_object(text)
    if obj is None:
        metrics["error"] = "JSON parse failed"
        return None, metrics
    schema_errors = _validate_against_schema(obj)
    if schema_errors:
        metrics["error"] = "schema validation failed: " + "; ".join(schema_errors)
        return None, metrics
    return obj, metrics


# ─── Tier 1b: Groq (OpenAI-compatible, real free tier) ─────────────────

def _call_groq_json_llm(
    prompt: str,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Call Groq's chat completions API and try to parse a JSON object.

    Groq is OpenAI-compatible and ships a generous free tier (no
    monthly credit deduction like the new HF router). It is enabled
    when ``REMOTE_LLM_PROVIDER=groq`` is set in ``.env`` and a
    ``GROQ_API_KEY`` is configured.

    Returns ``(parsed_object, metrics)``. ``parsed_object`` is None
    if the response is not parseable JSON matching the schema.
    """
    api_key = get_groq_api_key()
    if not api_key:
        return None, {"error": "no GROQ_API_KEY"}
    model = get_groq_model()
    metrics: dict[str, Any] = {"model": model, "source": "remote_groq"}
    try:
        from groq import Groq  # type: ignore
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
    except Exception as exc:  # noqa: BLE001
        return None, {"error": f"{type(exc).__name__}: {exc}", "model": model}

    metrics["raw_response"] = text
    try:
        usage = getattr(response, "usage", None)
        if usage is not None:
            metrics["prompt_tokens"] = int(getattr(usage, "prompt_tokens", 0) or 0)
            metrics["output_tokens"] = int(getattr(usage, "completion_tokens", 0) or 0)
    except Exception:
        pass

    obj = _extract_json_object(text)
    if obj is None:
        metrics["error"] = "JSON parse failed"
        return None, metrics
    schema_errors = _validate_against_schema(obj)
    if schema_errors:
        metrics["error"] = "schema validation failed: " + "; ".join(schema_errors)
        return None, metrics
    return obj, metrics


def _call_remote_cascade(prompt: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Dispatch synthesis through the configurable remote cascade."""
    from app.backend.llm.providers import _call_remote_cascade as cascade

    return cascade(prompt)


# ─── Tier 2: local Qwen 2.5 (GGUF) ────────────────────────────────────

def _call_local_qwen_json_llm(prompt: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Call the local Qwen 2.5 GGUF runner and parse a JSON object.

    Qwen 1.5B is too small to follow JSON-only output reliably, so
    we wrap the user prompt in a strict instruction and use a
    temperature of 0.1 + a hard stop sequence to keep things tight.
    """
    metrics: dict[str, Any] = {"model": get_compact_model(), "source": "local_qwen"}
    try:
        from app.backend.compact.qwen_runner import QwenRunner
        runner = QwenRunner()
    except Exception as exc:  # noqa: BLE001
        return None, {"error": f"Qwen unavailable: {exc}"}
    # Reuse the runner's summarisation slot with a much lower max_tokens
    # cap than the compaction pass — we only need a JSON blob.
    system = (
        "Ти асистент, який відповідає ВИКЛЮЧНО валідним JSON. "
        "Жодного тексту до чи після JSON. Жодних ``` огорож. "
        "Лише один JSON-об'єкт, що відповідає схемі з запиту."
    )
    full_prompt = (
        f"<|im_start|>system\n{system}<|im_end|>\n"
        f"<|im_start|>user\n{prompt}<|im_end|>\n"
        f"<|im_start|>assistant\n"
    )
    try:
        out = runner._load()(  # noqa: SLF001 — internal but stable
            full_prompt,
            max_tokens=2000,
            temperature=0.1,
            top_p=0.95,
            stop=["<|im_end|>"],
        )
        text = out["choices"][0]["text"]
    except Exception as exc:  # noqa: BLE001
        try:
            runner.close()
        except Exception:
            pass
        return None, {"error": f"Qwen call failed: {exc}"}
    finally:
        try:
            runner.close()
        except Exception:
            pass

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


# ─── Cache ──────────────────────────────────────────────────────────────

def _cache_key(
    template_id: str,
    theme: str,
    user_input: str,
    length: str,
    hardness: str,
    user_gap_values: dict[str, Any],
    attached_excerpt: str,
) -> str:
    raw = json.dumps(
        {
            "template_id": template_id,
            "theme": theme,
            "user_input": user_input,
            "length": length,
            "hardness": hardness,
            "user_gap_values": _strip_ai_for_key(user_gap_values),
            "attached_excerpt": attached_excerpt,
        },
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _strip_ai_for_key(gap_values: dict[str, Any]) -> dict[str, Any]:
    """Drop the user's existing *value* from the cache key — only the
    *shape* (which keys, ai_accessible flags) matters. This way two
    runs with identical (template, attached text, length, hardness)
    hit the cache even if the user typed slightly different values.
    """
    out: dict[str, Any] = {}
    for k, v in gap_values.items():
        if isinstance(v, dict):
            out[k] = {"ai_accessible": v.get("ai_accessible", True)}
        else:
            out[k] = None
    return out


def _cache_lookup(key: str) -> dict[str, Any] | None:
    try:
        from app.backend.db.connection import Database
        from app.backend.db.facade import BridgeRepository
        db = Database()
        try:
            bridge = BridgeRepository(db)
            row = bridge.cache.get_llm_response(key)
            if row and row.get("response_text"):
                return json.loads(row["response_text"])
        finally:
            db.close()
    except Exception:
        return None
    return None


def _cache_store(
    key: str, template_id: str, response: dict[str, Any], metrics: dict[str, Any]
) -> None:
    try:
        from app.backend.db.connection import Database
        from app.backend.db.facade import BridgeRepository
        db = Database()
        try:
            bridge = BridgeRepository(db)
            bridge.cache.set_llm_response(
                cache_key=key,
                template_id=template_id,
                template_name=template_id,
                params={"source": metrics.get("source", "remote")},
                user_files_hash=None,
                style_hash=None,
                response_text=json.dumps(response, ensure_ascii=False),
                prompt_tokens=int(metrics.get("prompt_tokens") or 0),
                output_tokens=int(metrics.get("output_tokens") or 0),
            )
        finally:
            db.close()
    except Exception:
        pass


# ─── Public entry point ────────────────────────────────────────────────

def synthesize_gap_values(
    *,
    template_id: str,
    theme: str,
    user_input: str,
    length: str,
    hardness: str,
    user_gap_values: dict[str, Any],
    attached_excerpt: str = "",
    allow_remote: bool = True,
    allow_local_qwen: bool = True,
    compact_dir: Path | None = None,
) -> SynthesisResult:
    """Synthesize the per-template gap_values via the 5-tier fallback.

    Returns a :class:`SynthesisResult` whose ``gap_values`` field is
    always populated (the worst-case fallback is "user_typed" which
    just returns the user-supplied values unchanged).
    """
    load_env()

    # Read the caching flag (local helper to avoid circular import).
    import os as _os
    _caching_enabled = (_os.environ.get("ENABLE_CACHING") or "false").strip().lower() in ("true", "1", "yes")

    cache_k = _cache_key(
        template_id, theme, user_input, length, hardness,
        user_gap_values, attached_excerpt,
    )
    
    prompt = _build_user_prompt(
        template_id=template_id,
        theme=theme,
        user_goal=user_input,
        user_input=user_input,
        length=length,
        hardness=hardness,
        user_gap_values=user_gap_values,
        attached_excerpt=attached_excerpt,
        compact_dir=compact_dir,
    )

    if _caching_enabled:
        cached = _cache_lookup(cache_k)
        if cached is not None:
            merged = _coerce_to_gap_values(cached, user_gap_values)
            return SynthesisResult(
                gap_values=merged,
                source="cache",
                model=None,
                cached=True,
                prompt=prompt,
            )

    last_raw = ""
    last_metrics: dict[str, Any] = {}
    first_error = ""
    second_error = ""

    # ── Pick the primary tier ────────────────────────────────────────
    # Tier 1 is the configurable remote cascade. Missing provider keys
    # are skipped; failed provider calls fall through to local Qwen.
    primary_label = "remote cascade"
    primary_call = (lambda: _call_remote_cascade(prompt)) if allow_remote else None
    fallback_label = "local Qwen"
    fallback_call = (
        (lambda: _call_local_qwen_json_llm(prompt))
        if allow_local_qwen else None
    )

    # ── Primary tier ────────────────────────────────────────────────
    if primary_call is not None:
        t0 = time.perf_counter()
        obj, metrics = primary_call()
        dt = (time.perf_counter() - t0) * 1000
        last_metrics = metrics
        last_raw = metrics.get("raw_response", "") or ""
        if obj is not None:
            merged = _coerce_to_gap_values(obj, user_gap_values)
            if _caching_enabled:
                _cache_store(cache_k, template_id, obj, metrics)
            # Source label: 'remote' for any remote provider (Groq or
            # HF), 'local_qwen' for the on-device runner.
            source_label = (
                "remote" if primary_label.startswith("remote")
                else "local_qwen"
            )
            return SynthesisResult(
                gap_values=merged,
                source=source_label,
                model=metrics.get("model"),
                prompt_tokens=metrics.get("prompt_tokens", 0),
                output_tokens=metrics.get("output_tokens", 0),
                duration_ms=dt,
                raw_response=last_raw,
                prompt=prompt,
            )
        first_error = f"{primary_label}: {metrics.get('error', 'failed')}"
    else:
        first_error = f"{primary_label} disabled"

    # ── Fallback tier ───────────────────────────────────────────────
    if fallback_call is not None:
        t0 = time.perf_counter()
        obj, metrics = fallback_call()
        dt = (time.perf_counter() - t0) * 1000
        last_metrics = metrics
        last_raw = metrics.get("raw_response", "") or ""
        if obj is not None:
            merged = _coerce_to_gap_values(obj, user_gap_values)
            if _caching_enabled:
                _cache_store(cache_k, template_id, obj, metrics)
            source_label = (
                "remote" if fallback_label.startswith("remote")
                else "local_qwen"
            )
            return SynthesisResult(
                gap_values=merged,
                source=source_label,
                model=metrics.get("model"),
                prompt_tokens=metrics.get("prompt_tokens", 0),
                output_tokens=metrics.get("output_tokens", 0),
                duration_ms=dt,
                error=first_error,
                raw_response=last_raw,
                prompt=prompt,
            )
        second_error = f"{fallback_label}: {metrics.get('error', 'failed')}"
    else:
        second_error = f"{fallback_label} disabled"

    # ── Tier 3: pass-through ───────────────────────────────────────
    return SynthesisResult(
        gap_values=dict(user_gap_values),
        source="user_typed",
        model=None,
        duration_ms=0.0,
        error=f"{primary_label}: {first_error}; {fallback_label}: {second_error}",
        raw_response=last_raw,
        prompt=prompt,
    )


__all__ = [
    "synthesize_gap_values",
    "SynthesisResult",
    "SCHEMA",
    "FEW_SHOT_EXAMPLE",
    "_extract_json_object",
    "_validate_against_schema",
    "_coerce_to_gap_values",
    "_call_remote_cascade",
]
