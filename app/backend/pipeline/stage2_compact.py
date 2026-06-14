"""app/backend/pipeline/stage2_compact.py

Stage 2 of the pipeline: condense large attached files and oversized
template instructions into short summaries that fit the text-LLM
context window.

Inputs (from Stage 1 in `transit_dir`):
  - attached/<hash>.txt        (one per file, may be very large)
  - <template>_fill.md         (template-specific instructions)

Outputs (written to `app/debug/compact/<session_id>/`):
  - session_context.json       (verbatim copy)
  - general_instructions.md    (verbatim copy)
  - <template>_fill.md         (compacted if >4000 tokens)
  - <template>_params.json     (verbatim copy)
  - attached_compact/<hash>.txt (compacted version of each attached file
                                that was >4000 tokens, else unchanged
                                copy)
  - compact.log                (timing, token counts, fallback reason)

Strategy:
  1. For every input, measure tokens.
  2. If below TOKEN_LIMIT_FOR_COMPACTION: copy verbatim.
  3. Otherwise: try to compact with QwenRunner (Qwen2.5-1.5B GGUF).
     If Qwen is unavailable or fails, fall back to `heuristic_truncate`.
  4. Cache the compacted text by SHA-256 of the source content.

This stage is intentionally "soft": no HARD failures, only warnings.
A bad compaction just means the next stage receives more tokens.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from app.backend.compact.qwen_runner import (
    QwenRunner,
    heuristic_truncate,
)
from app.backend.pipeline.types import (
    PipelineContext,
    StageResult,
    STAGE_OK,
    STAGE_SKIP,
    STAGE_WARN,
)
from app.backend.pipeline.utils import (
    COMPACT_CACHE_DIR,
    COMPACTION_TARGET_TOKENS,
    Timer,
    count_tokens,
    ensure_dir,
    now_iso,
    sha256_hex,
    TOKEN_LIMIT_FOR_COMPACTION,
)


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""


def _write(p: Path, text: str) -> None:
    ensure_dir(p.parent)
    p.write_text(text, encoding="utf-8")


def _cache_path(content_hash: str, label: str) -> Path:
    """Path to a cached compaction, content-addressed."""
    return COMPACT_CACHE_DIR / "text" / content_hash[:2] / f"{content_hash}.{label}.txt"


def _maybe_compact(
    label: str,
    text: str,
    runner: QwenRunner | None,
    cache: dict[str, Any],
) -> tuple[str, bool, str, int, int]:
    """Compact a single piece of text. Returns (text, was_compacted, mode, in_tokens, out_tokens).

    Since compaction is disabled, this always returns the text verbatim.
    """
    in_tokens = count_tokens(text)
    return text, False, "verbatim", in_tokens, in_tokens


def run_stage2(ctx: PipelineContext, *, use_qwen: bool = True) -> StageResult:
    """Compact the transit snapshot into the compact stage directory."""
    result = StageResult(name="stage2_compact")
    transit_dir: Path = ctx.transit_dir
    session_id = ctx.session_id
    # Resolve compact output dir from the context (set by orchestrator).
    compact_dir: Path = ctx.compact_dir
    ensure_dir(compact_dir)

    log_lines: list[str] = []
    log_lines.append("Compaction is disabled. All files are copied verbatim.")
    runner: QwenRunner | None = None

    try:
        with Timer(ctx.timings, "stage2") as _t:
            # 1. Copy session_context.json verbatim.
            src_ctx = transit_dir / "session_context.json"
            dst_ctx = compact_dir / "session_context.json"
            if src_ctx.is_file():
                shutil.copy2(src_ctx, dst_ctx)
                result.artifacts["session_context"] = str(dst_ctx)
            else:
                result.warnings.append("session_context.json missing in transit; rebuilt from input_snapshot")
                minimal = {
                    "id": session_id,
                    "name": ctx.session_name,
                    "template_id": ctx.template_id,
                    "status": "processing",
                    "input_snapshot": ctx.input_snapshot,
                    "created_at": ctx.input_snapshot.get("created_at") or now_iso(),
                }
                _write(dst_ctx, json.dumps(minimal, ensure_ascii=False, indent=2))
                result.artifacts["session_context"] = str(dst_ctx)

            # 2. Copy general instructions verbatim.
            src_g = transit_dir / "general_instructions.md"
            if src_g.is_file():
                dst_g = compact_dir / "general_instructions.md"
                shutil.copy2(src_g, dst_g)
                result.artifacts["global_instructions"] = str(dst_g)
            else:
                result.warnings.append("general_instructions.md missing in transit")

            # Copy library_files.json verbatim.
            src_lf = transit_dir / "library_files.json"
            if src_lf.is_file():
                dst_lf = compact_dir / "library_files.json"
                shutil.copy2(src_lf, dst_lf)
                result.artifacts["library_files"] = str(dst_lf)

            # 3. Copy params JSON verbatim.
            params_src_name = f"{ctx.template_id}_params.json"
            params_src = transit_dir / params_src_name
            if not params_src.is_file():
                # Fall back to gap_values_ref from input_snapshot, then any *_params.json.
                gap_ref = ctx.input_snapshot.get("gap_values_ref")
                if gap_ref:
                    params_src = transit_dir / gap_ref
            if params_src.is_file():
                dst_p = compact_dir / params_src_name
                shutil.copy2(params_src, dst_p)
                result.artifacts["params"] = str(dst_p)
                log_lines.append(f"params copied: {params_src.name}")
            else:
                result.warnings.append("Template params file not found; downstream will need fallback values")

            # 4. Compact template-specific instructions.
            template_ins_name = f"{ctx.template_id}_fill.md"
            src_ti = transit_dir / template_ins_name
            if not src_ti.is_file():
                candidates = sorted(transit_dir.glob("*_fill.md"))
                if candidates:
                    src_ti = candidates[0]
                    template_ins_name = src_ti.name
            if src_ti.is_file():
                text = _read(src_ti)
                compact_text, was_compacted, mode, in_t, out_t = _maybe_compact(
                    "fill", text, runner, cache=result.metrics
                )
                dst_ti = compact_dir / template_ins_name
                _write(dst_ti, compact_text)
                result.artifacts["template_instructions"] = str(dst_ti)
                log_lines.append(
                    f"template_instructions: {in_t} -> {out_t} tokens ({mode})"
                )
                result.metrics["template_instructions_in"] = in_t
                result.metrics["template_instructions_out"] = out_t
                if was_compacted and mode == "heuristic":
                    result.warnings.append(
                        f"Template instructions ({in_t} tokens) compacted by heuristic"
                    )
            else:
                result.warnings.append("Template instructions file missing")

            # 5. Compact each attached file.
            attached_src = transit_dir / "attached"
            attached_dst = ensure_dir(compact_dir / "attached_compact")
            attached_total_in = 0
            attached_total_out = 0
            attached_compacted = 0
            attached_cached = 0
            attached_heuristic = 0
            attached_files_count = 0

            if attached_src.is_dir():
                for txt_path in sorted(attached_src.glob("*.txt")):
                    attached_files_count += 1
                    text = _read(txt_path)
                    in_t = count_tokens(text)
                    attached_total_in += in_t
                    if in_t <= TOKEN_LIMIT_FOR_COMPACTION:
                        # Copy verbatim, no need to spend Qwen budget.
                        shutil.copy2(txt_path, attached_dst / txt_path.name)
                        attached_total_out += in_t
                        log_lines.append(
                            f"attached {txt_path.name}: {in_t} tokens (verbatim)"
                        )
                        continue
                    summary, was_compacted, mode, in_t, out_t = _maybe_compact(
                        txt_path.stem, text, runner, cache=result.metrics
                    )
                    _write(attached_dst / txt_path.name, summary)
                    attached_total_out += out_t
                    if mode == "qwen":
                        attached_compacted += 1
                    elif mode == "cache":
                        attached_cached += 1
                    elif mode == "heuristic":
                        attached_heuristic += 1
                    log_lines.append(
                        f"attached {txt_path.name}: {in_t} -> {out_t} tokens ({mode})"
                    )
                result.artifacts["attached_dir"] = str(attached_dst)
            else:
                result.warnings.append("No attached/ directory in transit")

            result.metrics["attached_files"] = attached_files_count
            result.metrics["attached_in_tokens"] = attached_total_in
            result.metrics["attached_out_tokens"] = attached_total_out
            result.metrics["attached_qwen_count"] = attached_compacted
            result.metrics["attached_cache_count"] = attached_cached
            result.metrics["attached_heuristic_count"] = attached_heuristic

            # 6. User style (always copy if present).
            user_style_path = ctx.transit_dir / "user_style.md"
            if user_style_path.is_file():
                style_text = _read(user_style_path)
                if style_text.strip():
                    compact_text, _, mode, in_t, out_t = _maybe_compact(
                        "user_style", style_text, runner, cache=result.metrics
                    )
                    dst_style = compact_dir / "user_style.md"
                    _write(dst_style, compact_text)
                    result.artifacts["user_style"] = str(dst_style)
                    log_lines.append(f"user_style: {in_t} -> {out_t} tokens ({mode})")

            result.artifacts["compact_dir"] = str(compact_dir)
            result.status = STAGE_OK if not result.warnings else STAGE_WARN
            log_lines.append(
                f"stage2 done: status={result.status}, "
                f"warnings={len(result.warnings)}"
            )
    finally:
        if runner is not None:
            try:
                runner.close()
            except Exception:
                pass

    result.duration_ms = ctx.timings.get("stage2", 0.0)
    _write_log(compact_dir, result, log_lines)
    return result


def _write_log(compact_dir: Path, result: StageResult, lines: list[str]) -> None:
    log_path = compact_dir / "compact.log"
    header = (
        f"[{now_iso()}] stage={result.name} status={result.status} "
        f"duration_ms={result.duration_ms:.1f}\n"
    )
    body = "\n".join(lines) + "\n"
    warnings = "".join(f"WARN  {w}\n" for w in result.warnings)
    errors = "".join(f"ERROR {e}\n" for e in result.errors)
    try:
        log_path.write_text(header + body + warnings + errors, encoding="utf-8")
    except OSError:
        pass
