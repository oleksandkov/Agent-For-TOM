"""app/backend/compact/qwen_runner.py

Local Qwen 2.5 1.5B (GGUF) summarization runner.

Why a GGUF model + llama-cpp-python?
  - No torch / no CUDA required. Runs on plain CPU.
  - 1.5B Q4_K_M is ~1.0 GB on disk, fast to load, fits in any laptop.
  - HuggingFace hosts the file: `Qwen/Qwen2.5-1.5B-Instruct-GGUF`
    (a repository, not a single file; we pick a concrete .gguf asset).

How it is used:
  - `QwenRunner(model_id=...)` is constructed by Stage 2.
  - `runner.summarize(text, max_tokens=500)` returns the summary.
  - The runner is **explicitly unloaded** after the stage finishes
    (`runner.close()`), so the RAM it consumed is released.

Failure modes (handled by Stage 2):
  - llama-cpp-python not installed: Stage 2 falls back to a heuristic
    "head + tail" truncation and emits a warning.
  - Model file not on disk and download fails: same fallback.
  - Model load succeeds but generation crashes: same fallback.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

# We pin the asset filename. Qwen2.5-1.5B-Instruct-GGUF ships several
# quantizations. Q4_K_M is the standard balance of size vs. quality.
DEFAULT_GGUF_FILE = "qwen2.5-1.5b-instruct-q4_k_m.gguf"

# Per the user instruction. Keep the default in sync with .env / utils.
DEFAULT_MODEL_REPO = "Qwen/Qwen2.5-1.5B-Instruct-GGUF"

# Cap input fed to the model. The runner truncates anything longer
# than this on a character basis, but the *token* limit is the real
# gate. We use a conservative character cap so the truncated text
# still fits inside `n_ctx - max_tokens` of the model context.
INPUT_CHAR_CAP = 24_000
# Rough chars-per-token ratio. For Cyrillic/Ukrainian text in Qwen's BPE,
# the ratio is around 2.2-2.6 chars/token. We use a conservative
# under-estimate of 1.8 to ensure prompt tokens do not overflow n_ctx.
_CHARS_PER_TOKEN = 1.8

SYSTEM_PROMPT_UK = (
    "Ти асистент, який робить стислий конспект навчальних матеріалів українською мовою.\n"
    "Сформулюй структурований конспект обов'язково за такою структурою:\n"
    "1. Тема та мета роботи (1-2 речення).\n"
    "2. Основні теоретичні відомості (2-3 дуже короткі тези/визначення, максимум 50 слів).\n"
    "3. Завдання та варіанти (2-3 короткі тези, що описують суть завдань, максимум 50 слів).\n"
    "4. Рекомендована література (максимум 2 ключові джерела).\n"
    "Пиши максимально лаконічно, тезами. Не використовуй вступні фрази. "
    "Не копіюй текст дослівно, перефразовуй дуже коротко. "
    "Кожен розділ має містити не більше 2-3 коротких пунктів. "
    "Обов'язково заверши конспект повністю, вклавшись у ліміт."
)

ATTACHED_SYSTEM_PROMPT_UK = (
    "Ти асистент, який робить розгорнутий та детальний конспект навчальних матеріалів українською мовою.\n"
    "Твоє завдання — детально законспектувати надісланий документ, забезпечивши збереження всієї критично важливої інформації. "
    "Конспект обов'язково має містити такі розділи:\n"
    "1. Тема та мета роботи (докладно, з усіма деталями).\n"
    "2. Теоретичні відомості (детальний опис концепцій, формул, алгоритмів, архітектурних рішень та визначень. Не скорочуй формули та ключові терміни!).\n"
    "3. Завдання та Варіанти (детально випиши суть завдань, умови виконання та ВСІ варіанти з їхніми конкретними параметрами, даними чи інструкціями, які є в документі).\n"
    "4. Рекомендована література та джерела.\n"
    "Пиши інформативно, структуровано та розгорнуто. Уникай загальних фраз, зосереджуйся на технічних і математичних подробицях, формулах і конкретних вхідних даних для варіантів."
)

ATTACHED_CHUNK_SYSTEM_PROMPT_UK = (
    "Ти асистент, який робить детальні нотатки з навчальних матеріалів українською мовою. "
    "Запиши розгорнутий опис теоретичних положень, формул, завдань та варіантів з цього фрагмента тексту. "
    "Зберігай усі технічні деталі, обмеження, коди, формули та варіанти повністю."
)


class QwenRunner:
    """Lazy-loaded GGUF summarization model.

    The actual llama-cpp-python handle is created on first `summarize()`
    call. This keeps unit tests free of network/IO side effects until
    they actually need the model.
    """

    def __init__(
        self,
        model_repo: str | None = None,
        gguf_file: str | None = None,
        n_ctx: int = 4096,
        n_threads: int | None = None,
    ) -> None:
        from app.backend.pipeline.utils import get_compact_model
        self.model_repo = model_repo or get_compact_model() or DEFAULT_MODEL_REPO
        # If the user passes the bare repo name (default), pin the asset.
        if gguf_file:
            self.gguf_file = gguf_file
        elif "GGUF" in self.model_repo and not self.model_repo.endswith(".gguf"):
            self.gguf_file = DEFAULT_GGUF_FILE
        else:
            # Allow pointing at a single .gguf file directly.
            self.gguf_file = self.model_repo.split("/")[-1]
            self.model_repo = "/".join(self.model_repo.split("/")[:-1])

        self.n_ctx = n_ctx
        self.n_threads = n_threads or max(1, (os.cpu_count() or 2) - 1)
        self._model: Any = None
        self._loaded_from: Path | None = None
        self._last_error: str | None = None

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    @property
    def model_path(self) -> Path:
        """Where the GGUF file lives on disk (downloaded on first use)."""
        from app.backend.pipeline.utils import COMPACT_CACHE_DIR
        return COMPACT_CACHE_DIR / "qwen" / self.gguf_file

    def ensure_model(self) -> Path:
        """Download the GGUF file if missing. Returns the local path.

        Strategy: prefer huggingface_hub's snapshot_download with
        allow_patterns pinned to the single asset. Falls back to
        hf_hub_download if the model is a single-file repo.
        """
        from app.backend.pipeline.utils import COMPACT_CACHE_DIR, ensure_dir
        target = self.model_path
        if target.is_file() and target.stat().st_size > 0:
            return target
        ensure_dir(target.parent)
        try:
            from huggingface_hub import hf_hub_download
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "huggingface-hub is required to download the Qwen GGUF model. "
                "Install it with: pip install huggingface-hub"
            ) from exc

        # 1) Try as part of a multi-file repo with the explicit asset name.
        try:
            local = hf_hub_download(
                repo_id=self.model_repo,
                filename=self.gguf_file,
                cache_dir=str(COMPACT_CACHE_DIR / "hf_cache"),
                local_dir=str(target.parent),
            )
            downloaded = Path(local)
            if downloaded != target:
                # Move/symlink into our canonical location.
                if target.exists():
                    target.unlink()
                try:
                    target.symlink_to(downloaded)
                except OSError:
                    downloaded.rename(target)
            return target
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"hf_hub_download failed: {exc}"
            raise

    def _load(self) -> Any:
        if self._model is not None:
            return self._model
        try:
            from llama_cpp import Llama  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "llama-cpp-python is not installed. "
                "Install it with: pip install llama-cpp-python"
            ) from exc

        local_path = self.ensure_model()
        # Construct with conservative defaults so the model fits in
        # low-RAM environments. mlock=False avoids needing root on Linux.
        self._model = Llama(
            model_path=str(local_path),
            n_ctx=self.n_ctx,
            n_threads=self.n_threads,
            mlock=False,
            verbose=False,
        )
        self._loaded_from = local_path
        return self._model

    def _call_remote_chat(self, system_prompt: str, user_prompt: str, max_tokens: int) -> str:
        from app.backend.pipeline.utils import (
            get_openrouter_api_key,
            get_openrouter_model,
            get_groq_api_key,
            get_groq_model,
        )
        import httpx

        # 1. Try OpenRouter
        openrouter_key = get_openrouter_api_key()
        if openrouter_key:
            model = get_openrouter_model()
            models_to_try = [model]
            if "free" in model and model != "openrouter/free":
                models_to_try.append("openrouter/free")

            for current_model in models_to_try:
                print(f"[QwenRunner] Calling OpenRouter model: {current_model}")
                try:
                    response = httpx.post(
                        "https://openrouter.ai/api/v1/chat/completions",
                        headers={
                            "Authorization": f"Bearer {openrouter_key}",
                            "HTTP-Referer": "https://github.com/agent-for-tom",
                            "X-Title": "Agent-For-TOM",
                        },
                        json={
                            "model": current_model,
                            "messages": [
                                {"role": "system", "content": system_prompt},
                                {"role": "user", "content": user_prompt},
                            ],
                            "max_tokens": max_tokens,
                            "temperature": 0.2,
                        },
                        timeout=120.0,
                    )
                    print(f"[QwenRunner] OpenRouter {current_model} response status: {response.status_code}")
                    response.raise_for_status()
                    res_data = response.json()
                    if "error" in res_data:
                        print(f"[QwenRunner] OpenRouter {current_model} returned error body: {res_data['error']}")
                    choice = res_data.get("choices", [{}])[0]
                    summary = (choice.get("message", {}).get("content") or "").strip()
                    if summary:
                        return summary
                    else:
                        print(f"[QwenRunner] OpenRouter {current_model} returned empty summary. JSON: {res_data}")
                except Exception as exc:
                    print(f"OpenRouter call with {current_model} failed: {exc}")

        # 2. Try Groq
        groq_key = get_groq_api_key()
        if groq_key:
            model = get_groq_model()
            try:
                from groq import Groq
                client = Groq(api_key=groq_key, timeout=120.0)
                response = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    max_tokens=max_tokens,
                    temperature=0.2,
                )
                choice = response.choices[0]
                summary = (getattr(choice.message, "content", "") or "").strip()
                if summary:
                    return summary
            except Exception as exc:
                print(f"Groq call failed: {exc}")

        raise RuntimeError("Both OpenRouter and Groq remote calls failed or were not configured.")

    def summarize(self, text: str, max_tokens: int = 600, label: str | None = None) -> str:
        """Summarize `text` to approximately `max_tokens` output tokens.

        Splits text into chunks of at most 16,000 characters (~4,000 tokens) if it is too
        large, summarizes each chunk using remote OpenRouter/Groq, and then reduces the
        summaries into a final structured Ukrainian summary.
        """
        if not text or not text.strip():
            return ""
        snippet = text.strip()
        # Remove page markers like "===== Page N =====" or "----- Page N (header) -----"
        snippet = re.sub(
            r"^\s*[=\-]+\s*Page\s+\d+\s*(?:\([^)]*\))?\s*[=\-]+\s*$",
            "",
            snippet,
            flags=re.MULTILINE,
        )
        # Collapse multiple empty lines
        snippet = re.sub(r"\n{3,}", "\n\n", snippet)

        is_attached = label not in ("fill", "user_style") if label else True

        if is_attached:
            sys_prompt = ATTACHED_SYSTEM_PROMPT_UK
            chunk_sys_prompt = ATTACHED_CHUNK_SYSTEM_PROMPT_UK
            max_tokens = max(max_tokens, 1000)
        else:
            sys_prompt = SYSTEM_PROMPT_UK
            chunk_sys_prompt = (
                "Ти асистент, який робить короткі нотатки з навчальних матеріалів українською мовою. "
                "Напиши короткий список (2-3 тези) найважливіших теоретичних положень, завдань чи формул з цього фрагмента тексту. "
                "Пиши максимально лаконічно, без вступних слів."
            )

        char_cap = 16_000

        try:
            if len(snippet) <= char_cap:
                return self._call_remote_chat(
                    system_prompt=sys_prompt,
                    user_prompt=f"Підсумуй цей text:\n\n{snippet}",
                    max_tokens=max_tokens,
                )

            # Map-Reduce Chunk-based summarization
            # 1. Split into chunks by paragraphs
            paragraphs = snippet.split("\n\n")
            chunks = []
            current_chunk = []
            current_len = 0

            for p in paragraphs:
                if len(p) > char_cap:
                    if current_chunk:
                        chunks.append("\n\n".join(current_chunk))
                        current_chunk = []
                        current_len = 0
                    lines = p.split("\n")
                    for line in lines:
                        if len(line) > char_cap:
                            for i in range(0, len(line), char_cap):
                                chunks.append(line[i:i+char_cap])
                        else:
                            if current_len + len(line) > char_cap:
                                chunks.append("\n".join(current_chunk))
                                current_chunk = [line]
                                current_len = len(line)
                            else:
                                current_chunk.append(line)
                                current_len += len(line)
                else:
                    if current_len + len(p) > char_cap:
                        chunks.append("\n\n".join(current_chunk))
                        current_chunk = [p]
                        current_len = len(p)
                    else:
                        current_chunk.append(p)
                        current_len += len(p)
            if current_chunk:
                chunks.append("\n\n".join(current_chunk))

            # 2. Summarize each chunk (Map step)
            chunk_summaries = []
            for idx, chunk_text in enumerate(chunks, 1):
                try:
                    chunk_summary = self._call_remote_chat(
                        system_prompt=chunk_sys_prompt,
                        user_prompt=f"Зроби короткі нотатки для цього фрагмента (частина {idx} з {len(chunks)}):\n\n{chunk_text}",
                        max_tokens=250 if is_attached else 150,
                    )
                    if chunk_summary:
                        chunk_summaries.append(chunk_summary)
                except Exception as exc:
                    print(f"Failed to summarize chunk {idx}: {exc}")

            if not chunk_summaries:
                return ""

            # 3. Merge and summarize the summaries (Reduce step)
            merged_text = "\n\n".join(chunk_summaries)
            return self._call_remote_chat(
                system_prompt=sys_prompt,
                user_prompt=f"Підсумуй цей об'єднаний конспект фрагментів тексту:\n\n{merged_text}",
                max_tokens=max_tokens,
            )
        except Exception as exc:
            print(f"Remote summarization failed completely: {exc}")

        return ""

    def close(self) -> None:
        """Release the model handle. After this, a fresh load is needed."""
        if self._model is not None:
            try:
                # llama-cpp-python >=0.2 has __del__; explicit close
                # is best-effort. Dropping the reference is enough.
                del self._model
            except Exception:
                pass
            self._model = None


def heuristic_truncate(text: str, target_tokens: int = 500) -> str:
    """Cheap CPU fallback when Qwen is unavailable.

    Keeps the first ~70% and the last ~30% of the document up to the
    target token budget. Preserves the most likely-to-be-useful context
    (intro + tail).
    """
    if not text:
        return ""
    words = text.split()
    if len(words) <= target_tokens:
        return text
    head = int(target_tokens * 0.7)
    tail = target_tokens - head
    head_part = " ".join(words[:head])
    tail_part = " ".join(words[-tail:])
    return f"{head_part}\n\n[…скорочено {len(words) - head - tail} слів…]\n\n{tail_part}"


__all__ = [
    "QwenRunner",
    "heuristic_truncate",
    "DEFAULT_MODEL_REPO",
    "DEFAULT_GGUF_FILE",
]
