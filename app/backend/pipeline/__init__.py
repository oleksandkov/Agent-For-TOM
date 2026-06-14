"""app/backend/pipeline — Agent-For-TOM backend pipeline package.

Public entry points:
  - PipelineRunner / run_pipeline_for_session (orchestrator)
  - run_stage1 / run_stage2 / run_stage5 (individual stages)
  - QwenRunner, heuristic_truncate (local compaction)
  - render_filled_py (local fallback filled.py synthesizer)
"""
from __future__ import annotations

from app.backend.compact.qwen_runner import QwenRunner, heuristic_truncate
from app.backend.llm.gap_assembler import render_filled_py
from app.backend.pipeline.orchestrator import (
    PipelineRun,
    PipelineRunner,
    run_pipeline_for_session,
)
from app.backend.pipeline.stage1_transit import run_stage1
from app.backend.pipeline.stage2_compact import run_stage2
from app.backend.pipeline.stage5_output import run_stage5
from app.backend.pipeline.types import (
    PipelineContext,
    StageResult,
    STAGE_OK,
    STAGE_WARN,
    STAGE_SKIP,
    STAGE_FAIL,
)
from app.backend.pipeline.utils import (
    APP_DIR,
    COMPACT_DIR,
    DEBUG_DIR,
    MAIN_OUT_DIR,
    OUTPUT_DIR,
    REPO_ROOT,
    TRANSIT_DIR,
    get_hf_token,
    get_text_model,
    get_image_model,
    get_compact_model,
    load_env,
    count_tokens,
    sha256_hex,
    now_iso,
    session_paths,
)

__all__ = [
    "PipelineRunner",
    "PipelineRun",
    "run_pipeline_for_session",
    "run_stage1",
    "run_stage2",
    "run_stage5",
    "PipelineContext",
    "StageResult",
    "STAGE_OK",
    "STAGE_WARN",
    "STAGE_SKIP",
    "STAGE_FAIL",
    "QwenRunner",
    "heuristic_truncate",
    "render_filled_py",
    "APP_DIR",
    "REPO_ROOT",
    "DEBUG_DIR",
    "TRANSIT_DIR",
    "COMPACT_DIR",
    "MAIN_OUT_DIR",
    "OUTPUT_DIR",
    "get_hf_token",
    "get_text_model",
    "get_image_model",
    "get_compact_model",
    "load_env",
    "count_tokens",
    "sha256_hex",
    "now_iso",
    "session_paths",
]
