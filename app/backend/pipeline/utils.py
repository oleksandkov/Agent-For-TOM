"""app/backend/pipeline/utils.py

Shared utilities for the Agent-For-TOM backend pipeline.

Responsibilities:
  - locate the project root and the canonical directory layout
  - load .env once and expose typed accessors
  - read HUGGY_FACE_TOKEN / HF_TOKEN with consistent precedence
  - small helpers: SHA-256, token counting, ISO timestamps
"""
from __future__ import annotations

import hashlib
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Repo root is the parent of the `app/` package directory.
APP_DIR: Path = Path(__file__).resolve().parents[2]
REPO_ROOT: Path = APP_DIR.parent

DEBUG_DIR: Path = APP_DIR / "debug"
TRANSIT_DIR: Path = DEBUG_DIR / "transit"
COMPACT_DIR: Path = DEBUG_DIR / "compact"
MAIN_OUT_DIR: Path = DEBUG_DIR / "main_out"
IMAGE_GEN_DIR: Path = DEBUG_DIR / "image-gen"
OUTPUT_DIR: Path = DEBUG_DIR / "output"

INSTRUCTIONS_DIR: Path = APP_DIR / "instructions"
TEMPLATE_INS_DIR: Path = INSTRUCTIONS_DIR / "template-ins"
GLOBAL_INSTRUCTIONS: Path = INSTRUCTIONS_DIR / "global_instructions.md"

CACHE_DIR: Path = APP_DIR / "data" / "cache"
COMPACT_CACHE_DIR: Path = CACHE_DIR / "compact"
DB_PATH: Path = APP_DIR / "db" / "agent.db"

ENV_FILE: Path = REPO_ROOT / ".env"

# Pipeline tunables. Sensible defaults; users can override via .env.
TOKEN_LIMIT_FOR_COMPACTION = 4000
COMPACTION_TARGET_TOKENS = 600
SANDBOX_TIMEOUT_SECONDS = 60
HF_REQUEST_TIMEOUT_SECONDS = 120
DEFAULT_TEXT_LLM_MODEL = "meta-llama/Llama-3.3-70B-Instruct"
DEFAULT_IMAGE_LLM_MODEL = "black-forest-labs/FLUX.1-schnell"
DEFAULT_COMPACT_MODEL = "Qwen/Qwen2.5-1.5B-Instruct-GGUF"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_OPENROUTER_MODEL = "openrouter/free"
DEFAULT_GROQ_MODEL = "llama-3.1-8b-instant"

# Whether to print a hard warning when HUGGY_FACE_TOKEN is missing.
WARN_ON_MISSING_HF_TOKEN = False


def load_env(env_file: Path | None = None) -> dict[str, str]:
    """Load .env into os.environ and return the parsed key/value map.

    Tolerant: missing file, blank lines, or malformed lines are skipped.
    Existing process env values are NOT overridden (matches shell
    semantics). Inline ``#`` comments after a value are stripped
    (e.g. ``KEY=foo # bar`` -> ``KEY=foo``) so users can document a
    line in-place without breaking the value.
    """
    env_file = env_file or ENV_FILE
    parsed: dict[str, str] = {}
    if not env_file.is_file():
        return parsed
    try:
        raw = env_file.read_text(encoding="utf-8")
    except OSError:
        return parsed
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        if " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        parsed[key] = value
        os.environ[key] = value  # .env always overrides pre-existing process env
    return parsed


def get_env(name: str, default: str | None = None) -> str | None:
    """Case-insensitive .env lookup with explicit None default."""
    value = os.environ.get(name)
    if value is not None and value != "":
        return value
    if default is not None:
        return default
    return None


def get_hf_token() -> str | None:
    """Return None as HuggingFace API usage is removed."""
    return None


def get_text_model() -> str:
    return get_env("HUGGING_FACE_MODEL") or get_env("HF_MODEL") or DEFAULT_TEXT_LLM_MODEL


def get_image_model() -> str:
    return get_env("HF_IMAGE_MODEL") or DEFAULT_IMAGE_LLM_MODEL


def get_compact_model() -> str:
    return get_env("COMPACT_MODEL") or get_env("HF_COMPACT_MODEL") or DEFAULT_COMPACT_MODEL


def get_gemini_api_key() -> str | None:
    for key in ("GOOGLE_API_KEY", "GOOGLE_GEMINI_API_KEY"):
        value = get_env(key)
        if value:
            return value.strip()
    return None


def get_gemini_model() -> str:
    return get_env("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL


def get_openrouter_api_key() -> str | None:
    for key in ("OPENROUTER_API_KEY", "OPENROUTER_KEY"):
        value = get_env(key)
        if value:
            return value.strip()
    return None


def get_openrouter_model() -> str:
    return get_env("OPENROUTER_MODEL") or DEFAULT_OPENROUTER_MODEL


def get_groq_api_key() -> str | None:
    for key in ("GROQ_API_KEY", "GROQ_TOKEN"):
        value = get_env(key)
        if value:
            return value.strip()
    return None


def get_groq_model() -> str:
    return get_env("GROQ_MODEL") or DEFAULT_GROQ_MODEL


def get_support_attach_files() -> bool:
    """Return True if attached files should be included as LLM context."""
    val = (get_env("SUPPORT_ATTACH_FILES") or "false").strip().lower()
    return val in ("true", "1", "yes")


def get_enable_caching() -> bool:
    """Return True if LLM response caching is enabled."""
    val = (get_env("ENABLE_CACHING") or "false").strip().lower()
    return val in ("true", "1", "yes")


def get_allow_local_llm() -> bool:
    """Return True if the local Qwen model runner is allowed to run."""
    val = (get_env("ALLOW_LOCAL_LLM") or "true").strip().lower()
    return val in ("true", "1", "yes")


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# Lightweight token approximation. We deliberately avoid a hard dep on
# tiktoken (it is a 5 MB native wheel). A simple word split is good enough
# for the routing decisions in this pipeline (above/below 4000 tokens).
_WORD_RE = re.compile(r"\S+")


def count_tokens(text: str) -> int:
    if not text:
        return 0
    return len(_WORD_RE.findall(text))


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def session_paths(session_id: str) -> dict[str, Path]:
    """Return canonical directory paths for one session, by stage."""
    return {
        "transit": TRANSIT_DIR / session_id,
        "compact": COMPACT_DIR / session_id,
        "main_out": MAIN_OUT_DIR / session_id,
        "image_gen": IMAGE_GEN_DIR / session_id,
        "output": OUTPUT_DIR / session_id,
    }


def stage_timing() -> dict[str, float]:
    """Initialize a per-stage timing dict keyed by stage name."""
    return {}


class Timer:
    """Tiny context manager that records elapsed wall-clock ms into a dict."""

    __slots__ = ("_store", "_key", "_t0")

    def __init__(self, store: dict[str, float], key: str) -> None:
        self._store = store
        self._key = key
        self._t0 = 0.0

    def __enter__(self) -> "Timer":
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self._store[self._key] = round((time.perf_counter() - self._t0) * 1000.0, 1)


# Re-export for the other stages.
__all__ = [
    "APP_DIR",
    "REPO_ROOT",
    "DEBUG_DIR",
    "TRANSIT_DIR",
    "COMPACT_DIR",
    "MAIN_OUT_DIR",
    "IMAGE_GEN_DIR",
    "OUTPUT_DIR",
    "INSTRUCTIONS_DIR",
    "TEMPLATE_INS_DIR",
    "GLOBAL_INSTRUCTIONS",
    "CACHE_DIR",
    "COMPACT_CACHE_DIR",
    "DB_PATH",
    "ENV_FILE",
    "TOKEN_LIMIT_FOR_COMPACTION",
    "COMPACTION_TARGET_TOKENS",
    "SANDBOX_TIMEOUT_SECONDS",
    "HF_REQUEST_TIMEOUT_SECONDS",
    "WARN_ON_MISSING_HF_TOKEN",
    "load_env",
    "get_env",
    "get_hf_token",
    "get_text_model",
    "get_image_model",
    "get_compact_model",
    "get_gemini_api_key",
    "get_gemini_model",
    "get_openrouter_api_key",
    "get_openrouter_model",
    "get_groq_api_key",
    "get_groq_model",
    "sha256_hex",
    "now_iso",
    "count_tokens",
    "ensure_dir",
    "session_paths",
    "stage_timing",
    "Timer",
    "get_support_attach_files",
    "get_enable_caching",
    "get_allow_local_llm",
]
