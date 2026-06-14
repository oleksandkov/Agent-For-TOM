"""app/backend/pipeline/orchestrator.py

End-to-end pipeline runner. Glues stages 1 → 2 → 5 together and
exposes a single `PipelineRunner` class plus a free-function entry
point `run_pipeline_for_session(...)`.

This is the only object the UI / bridge.py needs to know about.

Public surface:
  - PipelineRunner(steps=("stage1","stage2","stage5"), use_qwen=True)
      .run(transit_dir, on_step=lambda step,result: ...)
  - run_pipeline_for_session(transit_dir, on_step=None, use_qwen=True)
      -> dict with the index.json payload

Why stages 3 + 4 are not exposed separately
------------------------------------------
The first-cut scope (per the user's answer) is "stages 1, 2, 5 only —
skip image generation". Stage 3 (remote text LLM) and Stage 4 (image
generation) are therefore collapsed into Stage 5's synthesis step
(see `stage5_output._synthesize_filled_py`). When the user later
wants to wire stages 3 and 4 separately, the orchestrator's `steps`
list is the single change point.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from app.backend.pipeline.types import (
    PipelineContext,
    StageResult,
    STAGE_FAIL,
    STAGE_OK,
)
from app.backend.pipeline.utils import (
    COMPACT_DIR,
    IMAGE_GEN_DIR,
    MAIN_OUT_DIR,
    OUTPUT_DIR,
    TRANSIT_DIR,
    get_hf_token,
    load_env,
    now_iso,
    session_paths,
    WARN_ON_MISSING_HF_TOKEN,
)

log = logging.getLogger("agent_for_tom.pipeline")


@dataclass
class PipelineRun:
    """Result of a complete pipeline run."""

    session_id: str
    context: PipelineContext
    stages: list[StageResult] = field(default_factory=list)
    index: dict[str, Any] = field(default_factory=dict)

    @property
    def is_ok(self) -> bool:
        return all(s.status != STAGE_FAIL for s in self.stages)

    @property
    def docx_path(self) -> str | None:
        return self.index.get("artifacts", {}).get("docx")

    @property
    def pdf_path(self) -> str | None:
        return self.index.get("artifacts", {}).get("pdf")

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "is_ok": self.is_ok,
            "stages": [s.to_dict() for s in self.stages],
            "index": self.index,
        }


class PipelineRunner:
    """Sequential executor for the configured pipeline stages.

    Each stage receives the shared `PipelineContext` and returns a
    `StageResult`. The runner records every result and stops on HARD
    failures (status == "fail") so subsequent stages don't run on bad
    data.
    """

    DEFAULT_STEPS: tuple[str, ...] = ("stage1", "stage2", "stage5")

    def __init__(
        self,
        steps: tuple[str, ...] | list[str] | None = None,
        use_qwen: bool = True,
    ) -> None:
        load_env()  # best-effort; harmless if .env is missing
        self.steps: tuple[str, ...] = tuple(steps) if steps else self.DEFAULT_STEPS
        self.use_qwen = use_qwen

    def _build_context(self, transit_dir: Path) -> PipelineContext:
        """Build a PipelineContext from a transit snapshot directory."""
        session_id = transit_dir.name
        paths = session_paths(session_id)
        ctx = PipelineContext(
            session_id=session_id,
            session_name=session_id,
            template_id="lab1",
            input_snapshot={},
            transit_dir=transit_dir,
            compact_dir=paths["compact"],
            main_out_dir=paths["main_out"],
            image_gen_dir=paths["image_gen"],
            output_dir=paths["output"],
        )

        sc_path = transit_dir / "session_context.json"
        if sc_path.is_file():
            try:
                sc = json.loads(sc_path.read_text(encoding="utf-8"))
                ctx.session_name = sc.get("name") or session_id
                ctx.template_id = (
                    sc.get("template_id")
                    or sc.get("input_snapshot", {}).get("template_id")
                    or "lab1"
                )
                ctx.input_snapshot = sc.get("input_snapshot", {}) or {}
            except json.JSONDecodeError:
                ctx.warnings.append("session_context.json malformed; using defaults")
        return ctx


    def run(
        self,
        transit_dir: Path | str,
        on_step: Callable[[str, StageResult], None] | None = None,
    ) -> PipelineRun:
        """Execute the configured stages against one transit snapshot."""
        transit_dir = Path(transit_dir).resolve()
        if not transit_dir.is_dir():
            raise FileNotFoundError(f"transit_dir not found: {transit_dir}")

        ctx = self._build_context(transit_dir)
        run = PipelineRun(session_id=ctx.session_id, context=ctx)

        # Open the DB once for the entire run so we can persist
        # sessions / pipeline_runs / audit rows. All DB failures
        # are logged and ignored — the pipeline must keep working
        # even if the DB is unreadable (e.g. locked by another
        # process, corrupted, missing).
        from app.backend.db.connection import Database
        from app.backend.db.facade import BridgeRepository
        from app.backend.db.repositories.pipeline_runs import PipelineRunsRepository
        from app.backend.db.repositories.sessions import SessionRepository
        try:
            db = Database()
            bridge = BridgeRepository(db)
        except Exception as exc:  # noqa: BLE001
            log.warning("Database unavailable, skipping DB persistence: %s", exc)
            db = None
            bridge = None

        # Create the session row up front so we can record timings.
        db_session_id: str | None = None
        if bridge is not None:
            try:
                snap = ctx.input_snapshot or {}
                template_row = bridge.templates.get_by_name(ctx.template_id or snap.get("template_id", "lab1") or "lab1")
                template_pk = template_row["id"] if template_row else "00000000-0000-0000-0000-000000000001"
                sess = bridge.sessions.create(
                    template_id=template_pk,
                    name=ctx.session_name,
                    input_snapshot=snap,
                    output_dir=str(ctx.output_dir.relative_to(self._repo_root())),
                )
                db_session_id = sess["id"]
                bridge.audit.log(
                    actor="pipeline", action="session.create",
                    target_id=db_session_id, details={"name": ctx.session_name},
                )
                bridge.sessions.set_started(db_session_id)
            except Exception as exc:  # noqa: BLE001
                log.warning("Failed to create session row: %s", exc)
                db_session_id = None

        # Lazy imports so the orchestrator can be used in unit tests
        # without spinning up the heavy converter / model machinery.
        from app.backend.pipeline import stage1_transit, stage2_compact, stage5_output

        stage_map: dict[str, Any] = {
            "stage1": lambda: stage1_transit.run_stage1(ctx),
            "stage2": lambda: stage2_compact.run_stage2(ctx, use_qwen=self.use_qwen),
            "stage5": lambda: stage5_output.run_stage5(ctx, run.stages),
        }
        # Map stage name → pipeline_runs.stage enum for DB persistence.
        stage_enum = {
            "stage1": "file_convert",
            "stage2": "text_model_compact",
            "stage5": "execute",
        }

        for step_name in self.steps:
            log.info("Running %s for session=%s", step_name, ctx.session_id)
            # Persist "started" row.
            run_id: str | None = None
            if bridge is not None and db_session_id is not None:
                try:
                    run_id = bridge.pipeline_runs.start(
                        db_session_id, stage_enum.get(step_name, step_name)
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning("Failed to start pipeline_run row: %s", exc)
                    run_id = None
            runner = stage_map.get(step_name)
            if runner is None:
                result = StageResult(name=step_name, status=STAGE_FAIL)
                result.errors.append(f"Unknown stage: {step_name}")
            else:
                try:
                    result = runner()
                except Exception as exc:  # noqa: BLE001
                    result = StageResult(name=step_name, status=STAGE_FAIL)
                    result.errors.append(f"{type(exc).__name__}: {exc}")
                    log.exception("Stage %s crashed", step_name)
            run.stages.append(result)

            # Persist "finished" row.
            if bridge is not None and run_id is not None:
                try:
                    bridge.pipeline_runs.finish(
                        run_id,
                        status=("ok" if result.status == STAGE_OK
                                else "warn" if result.status == "warn"
                                else "error"),
                        error_message=("; ".join(result.errors) or None),
                        metrics=result.metrics,
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning("Failed to finish pipeline_run row: %s", exc)

            if on_step is not None:
                try:
                    on_step(step_name, result)
                except Exception:  # noqa: BLE001
                    log.exception("on_step callback raised; continuing")

            if result.status == STAGE_FAIL:
                log.error("Stage %s failed; aborting pipeline", step_name)
                if bridge is not None and db_session_id is not None:
                    try:
                        bridge.sessions.update_status(
                            db_session_id, "failed",
                            error_stage=stage_enum.get(step_name, step_name),
                            error_message=("; ".join(result.errors) or None),
                        )
                        bridge.audit.log(
                            actor="pipeline", action="session.fail",
                            target_id=db_session_id,
                            details={"stage": step_name, "errors": result.errors},
                        )
                    except Exception as exc:  # noqa: BLE001
                        log.warning("Failed to mark session as failed: %s", exc)
                break

        # Persist the final index.json (Stage 5 does this itself; we
        # also re-load it here so callers can consume it in-memory).
        if run.stages and run.stages[-1].name in ("stage5", "stage5_output"):
            index_path = ctx.output_dir / "index.json"
            if index_path.is_file():
                try:
                    run.index = json.loads(index_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    log.warning("index.json at %s is not valid JSON", index_path)

        # Mark the session as completed in the DB (or whatever the
        # last stage reported).
        if bridge is not None and db_session_id is not None:
            try:
                artifacts = run.index.get("artifacts", {}) if run.index else {}
                duration_ms = int(sum(ctx.timings.values()))
                # Build token_usage dict from stage5 LLM metrics.
                token_usage: dict[str, Any] = {}
                if run.stages:
                    stage5 = run.stages[-1]
                    for k, v in (stage5.metrics or {}).items():
                        if k.startswith("llm_") and isinstance(v, (int, float, str)):
                            token_usage[k[len("llm_"):]] = v
                bridge.sessions.set_completed(
                    db_session_id,
                    duration_ms=duration_ms,
                    docx_path=artifacts.get("docx"),
                    pdf_path=artifacts.get("pdf"),
                    image_count=int(run.index.get("image_count", 0) if run.index else 0),
                    token_usage=token_usage or None,
                )
                bridge.audit.log(
                    actor="pipeline", action="session.complete",
                    target_id=db_session_id,
                    details={"duration_ms": duration_ms},
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("Failed to mark session as completed: %s", exc)
            finally:
                try:
                    db.close()
                except Exception:
                    pass
        elif db is not None:
            try:
                db.close()
            except Exception:
                pass
        return run

    @staticmethod
    def _repo_root() -> Path:
        from app.backend.pipeline.utils import REPO_ROOT
        return REPO_ROOT


# ─── Convenience free function ────────────────────────────────────────────

def run_pipeline_for_session(
    transit_dir: Path | str,
    on_step: Callable[[str, StageResult], None] | None = None,
    use_qwen: bool = True,
) -> dict[str, Any]:
    """One-shot helper. Returns the run as a dict (index.json + stages)."""
    runner = PipelineRunner(use_qwen=use_qwen)
    run = runner.run(transit_dir, on_step=on_step)
    return run.to_dict()


__all__ = [
    "PipelineRunner",
    "PipelineRun",
    "run_pipeline_for_session",
    "TRANSIT_DIR",
    "COMPACT_DIR",
    "MAIN_OUT_DIR",
    "IMAGE_GEN_DIR",
    "OUTPUT_DIR",
]
