"""app/backend/pipeline/stage5_output.py

Stage 5 of the pipeline (the only "real" generation stage in the
scoped-down plan): produce `filled.py`, validate it, execute it in a
sandbox, and write the final `index.json`.

Scope note
----------
The full backend plan has 5 runtime stages:
  1. transit          — read snapshot, convert files
  2. compact          — summarise with local Qwen
  3. main_out         — call remote text LLM, write filled.py + manifest
  4. image_gen        — render PNGs from manifest
  5. output           — execute filled.py, embed images, write index.json

This implementation intentionally collapses stages 3 and 4 into a
single "filled.py synthesis" pass that lives inside Stage 5. The
synthesis path is now driven by `app.backend.llm.synthesizer`,
which:

  1. Asks the LLM for a strict JSON object containing the per-section
     text content (NOT Python code — LLMs are unreliable at that).
  2. Falls back through 3 tiers: remote HF LLM → local Qwen 2.5
     (GGUF) → user-typed values.
  3. Hands the result to `gap_assembler.render_filled_py` which
     emits the real `filled.py` (this part is rock-solid).

Output is written to `app/debug/main_out/<session_id>/filled.py` and
the run proceeds to `app/debug/output/<session_id>/`.
"""
from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from app.backend.llm import synthesizer as _synthesizer
from app.backend.llm.gap_assembler import render_filled_py
from app.backend.llm.synthesizer import (
    SynthesisResult,
    synthesize_gap_values,
)
from app.backend.pipeline.types import (
    PipelineContext,
    StageResult,
    STAGE_FAIL,
    STAGE_OK,
    STAGE_WARN,
)
from app.backend.pipeline.utils import (
    APP_DIR,
    HF_REQUEST_TIMEOUT_SECONDS,
    SANDBOX_TIMEOUT_SECONDS,
    Timer,
    ensure_dir,
    now_iso,
    sha256_hex,
)


# ─── helpers ────────────────────────────────────────────────────────────

def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8") if p.is_file() else ""


def _load_gap_values(compact_dir: Path, template_id: str) -> dict[str, Any]:
    """Load `labN_params.json` (gap_values) from the compact dir."""
    p = compact_dir / f"{template_id}_params.json"
    if not p.is_file():
        return {}
    try:
        data = json.loads(_read(p))
        return data.get("gap_values", {}) if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _read_attached_excerpt(compact_dir: Path, max_chars: int = 500000) -> str:
    """Concatenate all attached_compact/*.txt files into a single
    excerpt for the LLM. Capped at `max_chars` to stay under the
    typical context window.

    Returns an empty string when SUPPORT_ATTACH_FILES is disabled.
    """
    from app.backend.pipeline.utils import get_support_attach_files

    if not get_support_attach_files():
        return ""

    attached_dir = compact_dir / "attached_compact"
    if not attached_dir.is_dir():
        return ""
    parts: list[str] = []
    for p in sorted(attached_dir.glob("*.txt")):
        text = _read(p).strip()
        if text:
            parts.append(f"=== FILE {p.stem} ===\n{text}")
    joined = "\n\n".join(parts)
    if len(joined) > max_chars:
        half = max_chars // 2
        joined = joined[:half] + "\n\n[…скорочено…]\n\n" + joined[-half:]
    return joined



def rendered_docx_name(template_id: str) -> str:
    if template_id == "lab2":
        return "Lab_Template_Lab2_Style.docx"
    return "Lab_Template_Final.docx"


def rendered_pdf_name(template_id: str) -> str:
    if template_id == "lab2":
        return "Lab_Template_Lab2_Style.pdf"
    return "Lab_Template_Final.pdf"


# ─── Step B: filled.py validation ───────────────────────────────────────

def _validate_filled_py(text: str) -> list[str]:
    """Return a list of validation errors (empty = valid)."""
    errors: list[str] = []
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        return [f"SyntaxError: {exc.msg} (line {exc.lineno})"]

    has_docx = any(
        isinstance(node, ast.FunctionDef) and node.name == "create_docx"
        for node in tree.body
    )
    has_pdf = any(
        isinstance(node, ast.FunctionDef) and node.name == "create_pdf"
        for node in tree.body
    )
    if not has_docx:
        errors.append("Missing `create_docx` function")
    if not has_pdf:
        errors.append("Missing `create_pdf` function")

    forbidden_substrings = ["[Вставте", "[ВСТАВТЕ", "[вставте"]
    for needle in forbidden_substrings:
        if needle in text:
            errors.append(f"Unfilled placeholder remains: '{needle}…'")
    return errors


# ─── Step C: sandboxed execution ───────────────────────────────────────

def _execute_filled_py(filled_py_path: Path, cwd: Path) -> tuple[int, str, str]:
    """Run `python filled.py` in a sandboxed subprocess. Returns (rc, stdout, stderr)."""
    env = os.environ.copy()
    env.setdefault("NO_PROXY", "*")
    env.setdefault("HF_HUB_OFFLINE", "1")
    env.setdefault("TRANSFORMERS_OFFLINE", "1")
    try:
        proc = subprocess.run(
            [sys.executable, str(filled_py_path)],
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            timeout=SANDBOX_TIMEOUT_SECONDS,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"Execution timed out after {SANDBOX_TIMEOUT_SECONDS}s"
    except Exception as exc:  # noqa: BLE001
        return 1, "", f"Failed to launch subprocess: {exc}"


# ─── Step D: index.json ────────────────────────────────────────────────

def _build_index(
    ctx: PipelineContext,
    stage_results: list[StageResult],
    filled_py_path: Path,
    docx_path: Path | None,
    pdf_path: Path | None,
    synthesis: SynthesisResult,
) -> dict[str, Any]:
    timings = {k: round(v, 1) for k, v in ctx.timings.items()}
    token_usage = {
        "prompt_tokens": synthesis.prompt_tokens,
        "output_tokens": synthesis.output_tokens,
        "source": synthesis.source,
        "model": synthesis.model,
    }
    return {
        "session_id": ctx.session_id,
        "session_name": ctx.session_name,
        "template_id": ctx.template_id,
        "input_snapshot": ctx.input_snapshot,
        "status": "completed" if not ctx.errors else "failed",
        "source_mode": synthesis.source,
        "created_at": now_iso(),
        "completed_at": now_iso(),
        "duration_ms": int(sum(timings.values())),
        "timings": timings,
        "stages": [s.to_dict() for s in stage_results],
        "warnings": list(ctx.warnings),
        "errors": list(ctx.errors),
        "token_usage": token_usage,
        "artifacts": {
            "filled_py": str(filled_py_path),
            "docx": str(docx_path) if docx_path else None,
            "pdf": str(pdf_path) if pdf_path else None,
        },
    }


# ─── Main entry point ──────────────────────────────────────────────────

def _synthesize(
    compact_dir: Path,
    template_id: str,
    input_snapshot: dict[str, Any],
    warnings: list[str],
    errors: list[str],
) -> tuple[SynthesisResult, str, str]:
    """Drive the JSON-only synthesizer, then render filled.py via the
    local gap_assembler. Returns ``(synthesis, out_docx, out_pdf)``.

    No hard stop: the synthesizer tries local Qwen first, then the
    remote HF LLM, then falls back to the user-typed values. Only
    raises if the local Qwen is missing AND no remote token is
    configured AND the user typed a token is not in the env.
    """
    user_gap_values = _load_gap_values(compact_dir, template_id)
    has_any_ai_accessible = any(
        isinstance(v, dict) and v.get("ai_accessible", True)
        for v in user_gap_values.values()
    )

    # HARD STOP: ai_accessible gaps present + NEITHER local Qwen NOR
    # ANY remote provider is available. Without an LLM the user's
    # typed values would be used verbatim, which is not what the user
    # asked for when they marked the gap ai_accessible.
    from app.backend.compact.qwen_runner import QwenRunner
    from app.backend.pipeline.utils import (
        get_allow_local_llm,
        get_gemini_api_key,
        get_openrouter_api_key,
        get_groq_api_key,
    )
    local_qwen_available = get_allow_local_llm()
    if local_qwen_available:
        try:
            QwenRunner()
        except Exception:
            local_qwen_available = False
    remote_available = bool(
        get_gemini_api_key() or get_openrouter_api_key() or get_groq_api_key()
    )

    if (
        has_any_ai_accessible
        and not local_qwen_available
        and not remote_available
    ):
        msg = (
            "No LLM is available to rewrite `ai_accessible: true` gaps. "
            "Either install llama-cpp-python (the local Qwen 2.5 GGUF "
            "model is auto-downloaded on first use), or configure a "
            "remote provider in .env:\n"
            "  - Gemini: GOOGLE_API_KEY=xxx\n"
            "  - OpenRouter: OPENROUTER_API_KEY=xxx\n"
            "  - Groq: GROQ_API_KEY=xxx\n"
            "Then retry."
        )
        errors.append(msg)
        raise RuntimeError(msg)

    attached = _read_attached_excerpt(compact_dir)
    synthesis = synthesize_gap_values(
        template_id=template_id,
        theme=str(input_snapshot.get("theme") or input_snapshot.get("name") or "лабораторна робота"),
        user_input=str(input_snapshot.get("user_input") or ""),
        length=str(input_snapshot.get("length") or "middle"),
        hardness=str(input_snapshot.get("hardness") or "university_1"),
        user_gap_values=user_gap_values,
        attached_excerpt=attached,
        compact_dir=compact_dir,
        allow_local_qwen=local_qwen_available,
    )

    from app.backend.pipeline.utils import get_support_attach_files
    included = ["general_instructions.md", "session_context.json", f"{template_id}_params.json"]
    if (compact_dir / f"{template_id}_fill.md").is_file():
        included.append(f"{template_id}_fill.md")
    if get_support_attach_files():
        included.append("library_files.json")
    if (compact_dir / "user_style.md").is_file():
        included.append("user_style.md")
    warnings.append(f"Prompt sent to LLM included the following files as context: {', '.join(included)}")

    if synthesis.source == "user_typed" and has_any_ai_accessible:
        warnings.append(
            "AI-accessible gaps exist but no LLM produced output "
            f"(synthesis error: {synthesis.error})"
        )
    elif synthesis.source == "cache":
        warnings.append("Using cached gap_values from llm_cache.")
    elif synthesis.source == "local_qwen":
        if synthesis.error:
            warnings.append(f"Remote cascade failed before local Qwen: {synthesis.error}")
        warnings.append(
            f"Local Qwen 2.5 (model={synthesis.model}) synthesized gap_values in "
            f"{synthesis.duration_ms:.0f} ms ({synthesis.output_tokens} tokens)."
        )
    elif synthesis.source == "remote":
        warnings.append(
            f"Remote LLM ({synthesis.model}) synthesized gap_values in "
            f"{synthesis.duration_ms:.0f} ms ({synthesis.output_tokens} tokens)."
        )

    return synthesis, rendered_docx_name(template_id), rendered_pdf_name(template_id)


def run_stage5(
    ctx: PipelineContext,
    prior_stages: list[StageResult],
) -> StageResult:
    """Run synthesis → validation → execution → index.json."""
    result = StageResult(name="stage5_output")
    main_out_dir: Path = ensure_dir(ctx.main_out_dir)
    output_dir: Path = ensure_dir(ctx.output_dir)

    with Timer(ctx.timings, "stage5") as _t:
        # A. Synthesize gap_values via the JSON-only synthesizer and
        #    render filled.py via gap_assembler.
        try:
            synthesis, docx_name, pdf_name = _synthesize(
                ctx.compact_dir, ctx.template_id, ctx.input_snapshot,
                result.warnings, result.errors,
            )
        except Exception as exc:  # noqa: BLE001
            result.status = STAGE_FAIL
            result.errors.append(f"filled.py synthesis failed: {exc}")
            _write_log(output_dir, result)
            return result
        # gap_assembler renders the actual filled.py.
        rendered = render_filled_py(ctx.template_id, synthesis.gap_values)
        filled_py = rendered["filled_py"]
        # If the template renderer picked a different filename, prefer it.
        docx_name = rendered.get("out_docx") or docx_name
        pdf_name = rendered.get("out_pdf") or pdf_name

        result.metrics["filled_py_source"] = synthesis.source
        result.metrics["llm_prompt_tokens"] = synthesis.prompt_tokens
        result.metrics["llm_output_tokens"] = synthesis.output_tokens
        result.metrics["llm_model"] = synthesis.model or "n/a"
        result.metrics["llm_cached"] = bool(synthesis.cached)
        result.metrics["llm_duration_ms"] = round(synthesis.duration_ms, 1)
        if synthesis.error:
            result.metrics["llm_error"] = synthesis.error
        result.artifacts["filled_py"] = str(main_out_dir / "filled.py")
        result.artifacts["out_docx_name"] = docx_name
        result.artifacts["out_pdf_name"] = pdf_name

        # Persist the raw LLM response for debugging.
        if synthesis.raw_response:
            ensure_dir(main_out_dir)
            (main_out_dir / "llm_raw_response.txt").write_text(
                synthesis.raw_response, encoding="utf-8"
            )

        # Persist the LLM prompt for debugging and visibility.
        if synthesis.prompt:
            ensure_dir(main_out_dir)
            (main_out_dir / "llm_prompt.txt").write_text(
                synthesis.prompt, encoding="utf-8"
            )

        # B. Write filled.py to both main_out (for inspection) and output.
        ensure_dir(main_out_dir)
        main_filled = main_out_dir / "filled.py"
        main_filled.write_text(filled_py, encoding="utf-8")
        ensure_dir(output_dir)
        out_filled = output_dir / "filled.py"
        out_filled.write_text(filled_py, encoding="utf-8")

        # C. Validate
        validation_errors = _validate_filled_py(filled_py)
        if validation_errors:
            result.status = STAGE_FAIL
            result.errors.extend(validation_errors)
            ensure_dir(main_out_dir)
            (main_out_dir / "validation_errors.txt").write_text(
                "\n".join(validation_errors), encoding="utf-8"
            )
            _write_log(output_dir, result)
            return result
        result.metrics["filled_py_sha256"] = sha256_hex(filled_py)

        # D. Execute in sandbox
        rc, stdout, stderr = _execute_filled_py(out_filled, output_dir)
        (output_dir / "filled_stdout.log").write_text(stdout, encoding="utf-8")
        (output_dir / "filled_stderr.log").write_text(stderr, encoding="utf-8")
        result.metrics["filled_exit_code"] = rc
        if rc != 0:
            result.status = STAGE_FAIL
            result.errors.append(
                f"filled.py exited with code {rc}; see filled_stderr.log"
            )
            _write_log(output_dir, result)
            return result

        # E. Locate the produced DOCX/PDF in the output dir.
        docx_path = output_dir / docx_name
        pdf_path = output_dir / pdf_name
        if not docx_path.is_file():
            candidates = list(output_dir.glob("*.docx"))
            if candidates:
                docx_path = candidates[0]
        if not pdf_path.is_file():
            candidates = list(output_dir.glob("*.pdf"))
            if candidates:
                pdf_path = candidates[0]
        result.artifacts["docx"] = str(docx_path) if docx_path.is_file() else ""
        result.artifacts["pdf"] = str(pdf_path) if pdf_path.is_file() else ""
        if not docx_path.is_file():
            result.warnings.append("DOCX output file not found after execution")
        if not pdf_path.is_file():
            result.warnings.append("PDF output file not found after execution")

        result.metrics["images_generated"] = 0  # image gen out of scope

        # F. Write index.json
        all_stages: list[StageResult] = list(prior_stages) + [result]
        index = _build_index(
            ctx, all_stages, out_filled,
            docx_path if docx_path.is_file() else None,
            pdf_path if pdf_path.is_file() else None,
            synthesis,
        )
        (output_dir / "index.json").write_text(
            json.dumps(index, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        result.artifacts["index_json"] = str(output_dir / "index.json")

        result.status = STAGE_OK if not result.warnings else STAGE_WARN
        ctx.warnings.extend(result.warnings)
        ctx.errors.extend(result.errors)
        ctx.artifacts.update(result.artifacts)

    result.duration_ms = ctx.timings.get("stage5", 0.0)
    _write_log(output_dir, result)
    return result


def _write_log(output_dir: Path, result: StageResult) -> None:
    ensure_dir(output_dir)
    log_path = output_dir / "output.log"
    header = (
        f"[{now_iso()}] stage={result.name} status={result.status} "
        f"duration_ms={result.duration_ms:.1f}\n"
    )
    body = "\n".join(
        f"{k}={v}" for k, v in result.metrics.items()
    ) + "\n"
    artifacts = "\n".join(f"ART {k}={v}" for k, v in result.artifacts.items()) + "\n"
    warnings = "".join(f"WARN  {w}\n" for w in result.warnings)
    errors = "".join(f"ERROR {e}\n" for e in result.errors)
    try:
        log_path.write_text(
            header + body + artifacts + warnings + errors, encoding="utf-8"
        )
    except OSError:
        pass
