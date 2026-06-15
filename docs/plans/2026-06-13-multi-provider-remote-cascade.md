# Multi-Provider Remote Cascade — Implementation Plan

> **For implementer:** A senior dev who knows the codebase should be able to execute this task-by-task without further questions. Each task = 2-5 min of focused work.

**Goal:** Replace the binary "HF or Groq" remote synthesis tier in `synthesizer.py` with a 3-provider cascade — **Google Gemini 2.5 Flash → OpenRouter (free) → Groq**. First provider to return schema-valid JSON wins. If all three fail, fall back to the existing local Qwen (Tier 2), then to `user_typed` (Tier 3).

**Architecture:**
- New thin wrappers in `app/backend/llm/providers.py` (Gemini via `httpx`, OpenRouter via `httpx`, Groq reused)
- New `_call_remote_cascade()` orchestrator that calls each provider in order and returns the first valid result
- `synthesizer.py` `synthesize_gap_values()` swaps the primary tier from the binary switch to the cascade
- `REMOTE_LLM_PROVIDER` env var becomes a **comma-separated cascade order** (default: `gemini,openrouter,groq`), so users can re-order or disable a provider without code changes
- A provider is **skipped** (not failed) if its API key is missing — only present-in-env providers are tried

**Tech stack:** `httpx` (already a transitive dep, promoted to direct), `groq` (new direct dep), `google-genai` NOT used (raw REST via `httpx` is leaner and avoids a 5 MB dep).

---

## Cascade order and provider defaults

| # | Provider      | Default model                              | Env var (key)               | Env var (model)       | Free tier limits               |
|---|---------------|--------------------------------------------|------------------------------|------------------------|--------------------------------|
| 1 | Google Gemini | `gemini-2.5-flash`                         | `GOOGLE_API_KEY`             | `GEMINI_MODEL`         | 15 RPM, 1M TPM, 1500 RPD       |
| 2 | OpenRouter    | `qwen/qwen-2.5-72b-instruct:free`          | `OPENROUTER_API_KEY`         | `OPENROUTER_MODEL`     | ~20 RPM per free model         |
| 3 | Groq          | `llama-3.3-70b-versatile`                  | `GROQ_API_KEY`               | `GROQ_MODEL`           | 30 RPM, generous TPM           |

Override the order: `REMOTE_LLM_PROVIDER=openrouter,gemini,groq` in `.env`. A missing key short-circuits its provider (not an error). If a key is present but the call fails, that's an error and the next provider is tried.

---

## Files to touch

| File                                                              | Change                                                                 |
|-------------------------------------------------------------------|------------------------------------------------------------------------|
| `app/backend/llm/providers.py`                                    | **CREATE** — 3 provider wrappers + cascade orchestrator                |
| `app/backend/llm/synthesizer.py`                                  | Replace primary-tier dispatch with cascade call; update docstring     |
| `app/backend/pipeline/utils.py`                                   | Add 4 env accessors: gemini key/model, openrouter key/model           |
| `.env`                                                             | Add 4 new vars (only the keys the user has)                            |
| `requirements.txt`                                                | Add `groq>=0.11.0` and `httpx>=0.27.0`                                 |
| `tests/test_synthesizer.py`                                       | Add 4 unit tests: cascade order, key-skip, error propagation          |
| `AGENTS.md`                                                       | Update the 3-tier diagram to show the 5-tier final order               |

---

## Tasks

### Task 1: Promote `httpx` to a direct dependency
**Why:** Both new providers use `httpx` for REST calls. It's already installed transitively via `huggingface-hub`, but we make it explicit so the project doesn't break on a future HF SDK change.

**Files:** `requirements.txt`

**Step 1.** Add a line after `huggingface-hub>=0.20.0`:
```
httpx>=0.27.0
groq>=0.11.0
```

**Step 2.** Install in the active venv:
```bash
cd C:\Github\Agent-For-Labs
uv pip install -r requirements.txt
```

**Verification:** `uv pip list | grep -E "^(httpx|groq)"` shows both pinned to a version >= the floor.

---

### Task 2: Add env accessors in `pipeline/utils.py`
**Why:** All new env reads go through one module so tests can patch them. Mirrors the existing `get_groq_api_key()` / `get_groq_model()` pattern (lines 151-165 of `utils.py`).

**Files:** `app/backend/pipeline/utils.py:151-165`

**Step 1.** Insert after `get_groq_model()` (after line 165):
```python
def get_gemini_api_key() -> str | None:
    """Return the Google AI Studio API key, or None if absent.

    Order: ``GOOGLE_API_KEY`` (canonical) → ``GOOGLE_GEMINI_API_KEY``
    (alias matching the project naming convention).
    """
    for key in ("GOOGLE_API_KEY", "GOOGLE_GEMINI_API_KEY"):
        value = get_env(key)
        if value:
            return value.strip()
    return None


def get_gemini_model() -> str:
    return get_env("GEMINI_MODEL") or "gemini-2.5-flash"


def get_openrouter_api_key() -> str | None:
    for key in ("OPENROUTER_API_KEY", "OPENROUTER_KEY"):
        value = get_env(key)
        if value:
            return value.strip()
    return None


def get_openrouter_model() -> str:
    return get_env("OPENROUTER_MODEL") or "qwen/qwen-2.5-72b-instruct:free"
```

**Step 2.** Update `__all__` in the same file to export the four new helpers.

**Verification:** `python -c "from app.backend.pipeline.utils import get_gemini_api_key, get_gemini_model, get_openrouter_api_key, get_openrouter_model; print('ok')"` prints `ok`.

---

### Task 3: Create `app/backend/llm/providers.py` skeleton
**Why:** All three provider wrappers + the cascade live in one focused module. Easier to test and reason about than scattered through `synthesizer.py`.

**Files:** `app/backend/llm/providers.py` (CREATE)

**Step 1.** Write the file with imports, system prompt reference, and the three function signatures (bodies filled in Tasks 4-6):
```python
"""app/backend/llm/providers.py

Thin HTTP wrappers for the remote-LLM providers used by the
3-provider cascade. The cascade order is decided in
``_call_remote_cascade`` (also here); each individual provider is
self-contained so it can be tested or replaced without touching the
others.

All three providers must return the same shape:
    (parsed_dict | None, metrics_dict)

``parsed_dict`` is None if the response is missing, un-parseable as
JSON, or fails schema validation. ``metrics_dict`` always contains
``model`` and ``source`` and (on success) ``prompt_tokens`` /
``output_tokens``; on failure it contains ``error``.
"""
from __future__ import annotations

import json
import time
from typing import Any

# Reuse the system prompt that lived in synthesizer.py so all
# providers get identical behaviour.
from app.backend.llm.synthesizer import (
    SCHEMA,
    SYSTEM_PROMPT,
    _extract_json_object,
    _validate_against_schema,
)
from app.backend.pipeline.utils import (
    get_gemini_api_key,
    get_gemini_model,
    get_groq_api_key,
    get_groq_model,
    get_openrouter_api_key,
    get_openrouter_model,
    get_env,
)


SOURCE_GEMINI = "remote_gemini"
SOURCE_OPENROUTER = "remote_openrouter"
SOURCE_GROQ = "remote_groq"


def _metrics(source: str, model: str) -> dict[str, Any]:
    return {"source": source, "model": model}


# ─── Provider: Google Gemini 2.5 Flash ─────────────────────────────────


def _call_gemini_json_llm(prompt: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Call Google AI Studio's generateContent REST endpoint.

    Docs: https://ai.google.dev/api/generate-content
    Auth: ``?key=API_KEY`` query param (simple, no OAuth needed).
    """
    api_key = get_gemini_api_key()
    model = get_gemini_model()
    metrics = _metrics(SOURCE_GEMINI, model)
    if not api_key:
        metrics["error"] = "no GOOGLE_API_KEY"
        return None, metrics
    try:
        import httpx  # already a transitive dep, promoted in Task 1
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        body = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 8000,
                "responseMimeType": "application/json",
            },
        }
        resp = httpx.post(
            url, params={"key": api_key}, json=body, timeout=120.0
        )
        resp.raise_for_status()
        data = resp.json()
        # Path: candidates[0].content.parts[0].text
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        metrics["prompt_tokens"] = int(
            data.get("usageMetadata", {}).get("promptTokenCount", 0) or 0
        )
        metrics["output_tokens"] = int(
            data.get("usageMetadata", {}).get("candidatesTokenCount", 0) or 0
        )
    except Exception as exc:  # noqa: BLE001
        metrics["error"] = f"{type(exc).__name__}: {exc}"
        return None, metrics

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


# ─── Provider: OpenRouter (OpenAI-compatible REST) ─────────────────────


def _call_openrouter_json_llm(prompt: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Call OpenRouter's chat completions endpoint (OpenAI-style).

    Docs: https://openrouter.ai/docs
    Note: OpenRouter forwards to many providers; the free-tier model
    we default to (qwen-2.5-72b-instruct:free) is the most reliable
    multilingual one for Ukrainian academic text.
    """
    api_key = get_openrouter_api_key()
    model = get_openrouter_model()
    metrics = _metrics(SOURCE_OPENROUTER, model)
    if not api_key:
        metrics["error"] = "no OPENROUTER_API_KEY"
        return None, metrics
    try:
        import httpx
        resp = httpx.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "HTTP-Referer": "https://github.com/agent-for-tom",
                "X-Title": "Agent-For-Labs",
            },
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 8000,
                "temperature": 0.2,
            },
            timeout=120.0,
        )
        resp.raise_for_status()
        data = resp.json()
        text = data["choices"][0]["message"]["content"] or ""
        usage = data.get("usage") or {}
        metrics["prompt_tokens"] = int(usage.get("prompt_tokens", 0) or 0)
        metrics["output_tokens"] = int(usage.get("completion_tokens", 0) or 0)
    except Exception as exc:  # noqa: BLE001
        metrics["error"] = f"{type(exc).__name__}: {exc}"
        return None, metrics

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


# ─── Provider: Groq (OpenAI-compatible) ────────────────────────────────


def _call_groq_json_llm(prompt: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Call Groq's chat completions endpoint via the official SDK.

    Kept on the SDK (rather than raw httpx) because the SDK handles
    a few edge cases (streaming abort, retry-after) and the project
    already pins the dep in requirements.txt (Task 1).
    """
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


# ─── Cascade orchestrator ──────────────────────────────────────────────


_DEFAULT_CASCADE = "gemini,openrouter,groq"


def _cascade_order() -> list[str]:
    """Return the list of provider names to try, in order.

    Reads ``REMOTE_LLM_PROVIDER`` from env (comma-separated). Falls
    back to ``gemini,openrouter,groq``. Provider names are
    case-insensitive; unknown names are silently dropped.
    """
    raw = get_env("REMOTE_LLM_PROVIDER") or _DEFAULT_CASCADE
    allowed = {"gemini", "openrouter", "groq"}
    return [p.strip().lower() for p in raw.split(",") if p.strip().lower() in allowed]


_DISPATCH = {
    "gemini": _call_gemini_json_llm,
    "openrouter": _call_openrouter_json_llm,
    "groq": _call_groq_json_llm,
}


def _call_remote_cascade(prompt: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Try each provider in the configured order; return the first
    schema-valid response. Aggregate per-provider errors so the
    caller can show a meaningful failure reason.
    """
    errors: list[str] = []
    last_metrics: dict[str, Any] = {}
    for name in _cascade_order():
        fn = _DISPATCH[name]
        t0 = time.perf_counter()
        obj, metrics = fn(prompt)
        metrics["duration_ms"] = round((time.perf_counter() - t0) * 1000.0, 1)
        last_metrics = metrics
        if obj is not None:
            metrics["cascade_order"] = _cascade_order()
            return obj, metrics
        err = metrics.get("error", "unknown")
        # "no API key" is a SKIP, not a real failure — don't list it
        # as a cascade error so the user knows the provider wasn't
        # actually tried.
        if "no " in err and "API_KEY" in err:
            continue
        errors.append(f"{name}: {err}")
    summary_metrics = {
        "source": "remote_cascade",
        "model": last_metrics.get("model"),
        "error": "all providers failed: " + " | ".join(errors) if errors else "no providers configured",
        "cascade_order": _cascade_order(),
        "providers_tried": [n for n in _cascade_order()],
    }
    return None, summary_metrics


__all__ = [
    "_call_gemini_json_llm",
    "_call_openrouter_json_llm",
    "_call_groq_json_llm",
    "_call_remote_cascade",
    "SOURCE_GEMINI",
    "SOURCE_OPENROUTER",
    "SOURCE_GROQ",
]
```

**Verification:** `python -c "from app.backend.llm.providers import _call_remote_cascade; print('cascade importable')"` prints `cascade importable`.

---

### Task 4: Add unit tests for the cascade dispatcher
**Why:** The cascade is the new Tier 1 and has subtle logic (skip-on-missing-key, error aggregation, order). Tests catch regressions in dispatch.

**Files:** `tests/test_synthesizer.py` (append a new class)

**Step 1.** Append to the end of the file:
```python
class TestRemoteCascade(unittest.TestCase):
    """The 3-provider cascade must: try in order, skip missing keys,
    aggregate errors, and stop on first valid JSON."""

    def _prompt(self) -> str:
        return json.dumps({"goal": "x" * 20, "general_info": "y" * 60,
                           "tasks": ["a"], "control_questions": ["b"],
                           "bibliography": ["c"]})

    @patch("app.backend.llm.providers._call_gemini_json_llm")
    @patch("app.backend.llm.providers._call_openrouter_json_llm")
    @patch("app.backend.llm.providers._call_groq_json_llm")
    def test_cascade_stops_on_first_success(
        self, mock_groq, mock_or, mock_gem
    ):
        # Gemini returns a parseable, schema-valid object
        from app.backend.llm.providers import _call_remote_cascade
        mock_gem.return_value = (json.loads(self._prompt()), {"model": "gem", "source": "remote_gemini"})
        mock_or.return_value = (None, {"error": "should not reach"})
        mock_groq.return_value = (None, {"error": "should not reach"})
        with patch.dict("os.environ", {"REMOTE_LLM_PROVIDER": "gemini,openrouter,groq"}):
            obj, metrics = _call_remote_cascade("prompt")
        self.assertIsNotNone(obj)
        self.assertEqual(metrics["source"], "remote_gemini")
        mock_gem.assert_called_once()
        mock_or.assert_not_called()
        mock_groq.assert_not_called()

    @patch("app.backend.llm.providers._call_groq_json_llm")
    def test_cascade_skips_provider_without_key(self, mock_groq):
        from app.backend.llm.providers import _call_remote_cascade
        # Both Gemini and OpenRouter report "no API_KEY" → skipped, not errors
        with patch("app.backend.llm.providers._call_gemini_json_llm",
                   return_value=(None, {"model": "g", "source": "remote_gemini", "error": "no GOOGLE_API_KEY"})), \
             patch("app.backend.llm.providers._call_openrouter_json_llm",
                   return_value=(None, {"model": "o", "source": "remote_openrouter", "error": "no OPENROUTER_API_KEY"})), \
             patch.dict("os.environ", {"REMOTE_LLM_PROVIDER": "gemini,openrouter,groq"}):
            mock_groq.return_value = (json.loads(self._prompt()),
                                      {"model": "llama", "source": "remote_groq"})
            obj, metrics = _call_remote_cascade("prompt")
        self.assertIsNotNone(obj)
        self.assertEqual(metrics["source"], "remote_groq")
        # "no API key" errors must NOT appear in the aggregated error
        self.assertNotIn("GOOGLE_API_KEY", metrics.get("error", ""))

    def test_cascade_order_is_configurable(self):
        from app.backend.llm.providers import _cascade_order
        with patch.dict("os.environ", {"REMOTE_LLM_PROVIDER": "groq,gemini"}):
            self.assertEqual(_cascade_order(), ["groq", "gemini"])
        with patch.dict("os.environ", {"REMOTE_LLM_PROVIDER": "unknown,gemini"}):
            self.assertEqual(_cascade_order(), ["gemini"])  # unknown is dropped
```

**Step 2.** Run only these tests:
```bash
cd C:\Github\Agent-For-Labs
.venv\Scripts\python.exe -m unittest tests.test_synthesizer.TestRemoteCascade -v
```

**Expected:** `Ran 3 tests ... OK`.

---

### Task 5: Wire the cascade into `synthesizer.py`
**Why:** The synthesizer is the single entry point the pipeline uses. Swapping its primary tier from the binary switch to the cascade completes the feature.

**Files:** `app/backend/llm/synthesizer.py` (lines 834-865, the dispatch block)

**Step 1.** Add the import near the other imports (around line 50):
```python
from app.backend.llm.providers import _call_remote_cascade
```

**Step 2.** Replace the `remote_is_primary` / `if remote_is_primary and allow_remote:` block (lines 839-865) with:
```python
    if allow_remote:
        primary_label = "remote cascade (gemini→openrouter→groq)"
        primary_call = lambda: _call_remote_cascade(prompt)  # noqa: E731
    else:
        primary_label = "remote"
        primary_call = None
    if allow_local_qwen:
        fallback_label = "local Qwen"
        fallback_call = lambda: _call_local_qwen_json_llm(prompt)  # noqa: E731
    else:
        fallback_label = "local Qwen"
        fallback_call = None
```

**Step 3.** Update the cascade-aware `source_label` mapping (line 879) so cascade-sourced results still report as `"remote"` (the public enum stays unchanged for downstream consumers like `run_pipeline.py`):
```python
        source_label = (
            "remote" if primary_label.startswith("remote")
            else "local_qwen"
        )
```
This already works — no change needed; the cascade's `source` field is overridden by `source_label` at return.

**Step 4.** Update the module docstring at the top of the file (lines 11-28) from "3-tier fallback" to "5-tier fallback":
- Tier 1: remote cascade (Gemini → OpenRouter → Groq) — first one with valid JSON wins
- Tier 2: local Qwen 2.5 (GGUF)
- Tier 3: gap_assembler pass-through (user_typed)
- And within Tier 1, the providers are tried in `REMOTE_LLM_PROVIDER` order, with a missing key acting as a skip.

**Step 5.** Update `__all__` at the bottom of the file to add `"_call_remote_cascade"` (re-exported for symmetry with the other internal helpers).

**Verification:** `python -c "from app.backend.llm.synthesizer import synthesize_gap_values; print('synthesizer ok')"` prints `synthesizer ok`.

---

### Task 6: Run the full existing test suite
**Why:** Make sure the dispatch change didn't break the 3 existing tests that mock the old `_call_remote_json_llm` and `_call_groq_json_llm`.

**Files:** none (just run tests)

**Step 1.**
```bash
cd C:\Github\Agent-For-Labs
.venv\Scripts\python.exe -m unittest tests.test_synthesizer -v
```

**Expected:** All tests pass (existing 3-tier tests may need a small patch to mock `_call_remote_cascade` instead of `_call_remote_json_llm` — see Step 2 if so).

**Step 2.** If existing tests fail with `AttributeError: <module> does not have '_call_remote_json_llm'`, that's because the old function is still in the file but unused. Two options:
- (a) Leave it as dead code — it stays as a fallback for any external code that imports it.
- (b) Delete it (~60 lines, lines 519-578).

Pick (a) for safety; we can delete in a follow-up commit if no caller remains.

---

### Task 7: Add `.env` entries (manual, requires user-provided keys)
**Why:** The cascade only does anything if at least one provider has a key.

**Files:** `C:\Github\Agent-For-Labs\.env`

**Step 1.** Ask the user which of the three providers they have keys for. Append only the lines they confirm:
```bash
# --- Remote cascade (Task 7) ---
# Uncomment and paste the key for any provider you have. Uncommented
# keys make that provider a candidate; commented lines are skipped.
# GOOGLE_API_KEY=AIza...
# GEMINI_MODEL=gemini-2.5-flash
# OPENROUTER_API_KEY=sk-or-...
# OPENROUTER_MODEL=qwen/qwen-2.5-72b-instruct:free
# GROQ_API_KEY=gsk_...
# GROQ_MODEL=llama-3.3-70b-versatile
# REMOTE_LLM_PROVIDER=gemini,openrouter,groq
```

**Step 2.** Where to get the keys (for the user's reference):
- **Google**: https://aistudio.google.com/app/apikey (free, no card)
- **OpenRouter**: https://openrouter.ai/settings/keys (free, sign in with Google/GitHub)
- **Groq**: https://console.groq.com/keys (free, sign in with Google/GitHub/email)

**Verification:** `python -c "from app.backend.pipeline.utils import get_gemini_api_key, get_openrouter_api_key, get_groq_api_key; print('gemini:', bool(get_gemini_api_key()), 'openrouter:', bool(get_openrouter_api_key()), 'groq:', bool(get_groq_api_key()))"` shows the keys you added.

---

### Task 8: End-to-end smoke test of the cascade
**Why:** The unit tests mock the providers; this exercises the real network with a real (small) prompt to confirm wiring is correct.

**Step 1.** With at least one key configured in `.env`:
```bash
cd C:\Github\Agent-For-Labs
.venv\Scripts\python.exe -c "
from app.backend.llm.synthesizer import synthesize_gap_values
r = synthesize_gap_values(
    template_id='lab1', theme='Сортування масивів',
    user_input='дослідити бульбашкове сортування',
    length='short', hardness='easy',
    user_gap_values={
        'goal': {'value': '', 'ai_accessible': True},
        'general_info': {'value': '', 'ai_accessible': True},
        'tasks': {'value': [], 'ai_accessible': True},
        'control_questions': {'value': [], 'ai_accessible': True},
        'bibliography': {'value': [], 'ai_accessible': True},
    },
)
print('source:', r.source)
print('model :', r.model)
print('error :', r.error)
print('keys  :', list(r.gap_values.keys()))
"
```

**Expected:**
- `source: remote` (cascade succeeded)
- `model: gemini-2.5-flash` (or whatever the winning provider is)
- `error: None`
- `keys: ['goal', 'general_info', 'tasks', 'control_questions', 'bibliography']`

**Step 2.** If the result is `source: user_typed`, the cascade failed end-to-end. Print the error to see which provider failed:
```bash
.venv\Scripts\python.exe -c "from app.backend.llm.providers import _call_remote_cascade; obj, m = _call_remote_cascade('return {\"goal\": \"дослідити сортування — це фундаментальна операція\",\"general_info\": \"Довгий текст. \" * 20, \"tasks\": [\"Завдання 1\"], \"control_questions\": [\"Питання 1\"], \"bibliography\": [\"Книга 1\"]}'); print(m.get('error'))"
```
This calls the cascade directly and shows the aggregated error.

---

### Task 9: Run the full pipeline on `example_session` to confirm the new tier in action
**Why:** The pipeline is the integration surface; if `source_mode: remote` appears with the new model, the change is end-to-end correct.

**Step 1.**
```bash
cd C:\Github\Agent-For-Labs
.venv\Scripts\python.exe run_pipeline.py example_session
```

**Expected final line:** `status: 'completed'  source_mode: 'remote'`

**Step 2.** Inspect the output DOCX to confirm it contains LLM-written Ukrainian prose (not user-typed):
```bash
.venv\Scripts\python.exe -c "
from docx import Document
doc = Document(r'app\debug\output\example_session\Lab_Template_Final.docx')
for p in doc.paragraphs:
    if p.text.strip() and 'фундаментальною' not in p.text:
        print(' -', p.text[:200])
" | head -10
```

**Expected:** multiple paragraphs of real Ukrainian academic prose, none of them the original "Сортування є фундаментальною операцією" placeholder.

---

### Task 10: Update AGENTS.md to reflect the 5-tier reality
**Why:** Future readers (and the user in 3 months) need to see the actual cascade order in the project docs.

**Files:** `AGENTS.md` (the "How the application works" section, lines 55-73)

**Step 1.** Replace the bullet that says "Pass 1 — Text LLM. ... HuggingFace Inference API" with:
```
- **Pass 1 — Text LLM.** Tries the 5-tier synthesis chain: (1) Google
  Gemini 2.5 Flash, (2) OpenRouter (free model), (3) Groq, (4) local
  Qwen 2.5 1.5B GGUF, (5) pass-through of the user's typed values.
  Within Tier 1, the order is set by `REMOTE_LLM_PROVIDER` in `.env`
  (default: `gemini,openrouter,groq`). The first provider that
  returns JSON matching the schema wins; missing API keys are
  skipped, not failed.
```

---

## Rollback plan

If anything goes wrong, the cascade is **additive** — the old `REMOTE_LLM_PROVIDER=groq` path still works. To roll back:
1. Set `REMOTE_LLM_PROVIDER=groq` in `.env` — switches back to the binary switch.
2. (Hard rollback) revert commits that touched `synthesizer.py` lines 834-865.

## Out of scope (YAGNI)

- Per-provider timeout configuration (all use 120s for now)
- Per-provider rate-limit tracking / cooldown
- Streaming responses
- Automatic key-rotation
- Parallel provider calls with first-valid-wins
- A UI to add keys (still done via `.env`)

These can be added later if usage patterns demand them.
