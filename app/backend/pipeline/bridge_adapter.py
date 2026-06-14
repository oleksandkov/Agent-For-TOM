"""app/backend/pipeline/bridge_adapter.py

Adapter that runs the orchestrator and emits signals compatible with
the existing QML `ProgressScreen.qml` contract.

The QML contract (see `app/bridge.py:113-119` and
`app/ui/ProgressScreen.qml`) uses 6 logical steps:

  0. Конвертація файлів    (file conversion)
  1. Text LLM (Pass 1)
  2. Валідація
  3. Генерація зображень
  4. Виконання + Compose
  5. PDF компіляція

Our current pipeline collapses this into 3 real stages (transit,
compact, output). We expose those 3 as steps 0, 1, 2 of the QML
contract and report steps 3–5 as "skipped" (image generation is
deferred in this scope).

The adapter is QObject-agnostic — it accepts a generic `emit`
callable, so the bridge can wire it to pyqtSignal.emit and a CLI
caller can wire it to `print`.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable, Optional

from app.backend.pipeline.orchestrator import PipelineRunner
from app.backend.pipeline.types import (
    PipelineContext,
    StageResult,
    STAGE_FAIL,
    STAGE_OK,
    STAGE_WARN,
)


# (qml_index, qml_label, qml_detail)
_STEP_TABLE: list[tuple[int, str, str]] = [
    (0, "Конвертація файлів", "Валідація transit + конвертація вкладених файлів"),
    (1, "Text LLM (Pass 1)", "Складання filled.py + валідація AST"),
    (2, "Валідація", "ManifestValidator + word count"),
    (3, "Генерація зображень", "Matplotlib + HuggingFace FLUX (вимкнено в поточній версії)"),
    (4, "Виконання + Compose", "subprocess filled.py → DOCX + PDF"),
    (5, "PDF компіляція", "DOCX → PDF через reportlab"),
]


def _now_ts() -> str:
    from datetime import datetime
    return datetime.now().strftime("%H:%M:%S")


def _qml_step_index(stage_name: str) -> int:
    """Map a stage name from the orchestrator to a QML step index."""
    if stage_name in ("stage1", "stage1_transit"):
        return 0
    if stage_name in ("stage5", "stage5_output"):
        return 4  # we treat the whole synthesis+execute as "execute+compose"
    return 0  # stage2 falls under "Text LLM (Pass 1)" for the QML UI


class BridgePipelineAdapter:
    """Runs PipelineRunner and emits QML-compatible signals.

    The constructor takes a QObject-like `target` whose methods map to
    the existing pyqtSignals on `AppBridge`:

        target.pipelineStarted()       -> no args
        target.pipelineProgress(pct, name)
        target.pipelineLog(ts, msg)
        target.pipelineStepActive(idx)
        target.pipelineStepDone(idx, name, detail)
        target.pipelineFinished(session_id)
        target.pipelineError(stage, msg)
        target.fileWarning(msg)        -> re-used for missing-token warnings
    """

    def __init__(self, target: Any) -> None:
        self._target = target
        self._lock = threading.Lock()
        self._cancelled = False

    def request_cancel(self) -> None:
        with self._lock:
            self._cancelled = True

    def _cancelled_now(self) -> bool:
        with self._lock:
            return self._cancelled

    def _emit_log(self, msg: str) -> None:
        try:
            self._target.pipelineLog.emit(_now_ts(), msg)
        except Exception:  # noqa: BLE001
            pass

    def _emit_progress(self, pct: int, name: str) -> None:
        try:
            self._target.pipelineProgress.emit(int(pct), name)
        except Exception:  # noqa: BLE001
            pass

    def _emit_step_active(self, idx: int) -> None:
        try:
            self._target.pipelineStepActive.emit(int(idx))
        except Exception:  # noqa: BLE001
            pass

    def _emit_step_done(self, idx: int, name: str, detail: str) -> None:
        try:
            self._target.pipelineStepDone.emit(int(idx), name, detail)
        except Exception:  # noqa: BLE001
            pass

    def _emit_finished(self, session_id: str) -> None:
        try:
            self._target.pipelineFinished.emit(session_id)
        except Exception:  # noqa: BLE001
            pass

    def _emit_error(self, stage: str, msg: str) -> None:
        try:
            self._target.pipelineError.emit(stage, msg)
        except Exception:  # noqa: BLE001
            pass

    def _emit_warning(self, msg: str) -> None:
        try:
            self._target.fileWarning.emit(msg)
        except Exception:  # noqa: BLE001
            pass

    def _on_step(self, step_name: str, result: StageResult) -> None:
        if self._cancelled_now():
            self._emit_log(f"[abort] cancellation requested before {step_name}")
            return

        idx, name, _detail = _STEP_TABLE[_qml_step_index(step_name)]
        self._emit_step_active(idx)
        self._emit_log(f"[{step_name}] start")
        for w in result.warnings:
            self._emit_log(f"[{step_name}] WARN  {w}")
            self._emit_warning(w)
        for e in result.errors:
            self._emit_log(f"[{step_name}] ERROR {e}")
        if result.metrics:
            metrics_str = ", ".join(
                f"{k}={v}" for k, v in result.metrics.items() if not isinstance(v, (dict, list))
            )
            if metrics_str:
                self._emit_log(f"[{step_name}] {metrics_str}")
        self._emit_step_done(idx, name, f"{result.status} ({result.duration_ms:.0f} ms)")

    def run(self, transit_dir: Path | str) -> dict[str, Any]:
        """Execute the pipeline; emit signals; return the run dict.

        Designed to be called from a QThread. Errors are surfaced via
        the ``pipelineError`` signal AND the returned dict.
        """
        try:
            self._target.pipelineStarted.emit()
        except Exception:  # noqa: BLE001
            pass

        # Mark all 6 steps in the UI as "active then done" at the
        # correct moments. The QML progress bar uses cumulative
        # percentages; we approximate that by mapping our 3 stages to
        # 0%, 33%, 75% progress, then 100% on completion.
        for idx, name, detail in _STEP_TABLE:
            self._emit_log(f"[step {idx}] {name}: {detail}")
        self._emit_progress(0, "Старт")
        self._emit_log("=== pipeline started ===")

        try:
            runner = PipelineRunner()
            run = runner.run(transit_dir, on_step=self._on_step)
        except Exception as exc:  # noqa: BLE001
            self._emit_error("orchestrator", f"{type(exc).__name__}: {exc}")
            self._emit_log(f"[orchestrator] ERROR {exc}")
            return {"is_ok": False, "errors": [str(exc)]}

        if self._cancelled_now():
            self._emit_log("[abort] pipeline cancelled")
            self._emit_error("user", "Pipeline cancelled by user")
            return {"is_ok": False, "cancelled": True}

        if not run.index:
            err = "Pipeline failed before producing index.json"
            self._emit_error(run.stages[-1].name if run.stages else "unknown", err)
            self._emit_progress(100, "Помилка")
            return run.to_dict()

        artifacts = run.index.get("artifacts", {})
        if run.index.get("status") == "completed":
            self._emit_log("=== pipeline finished ===")
            self._emit_log(f"docx: {artifacts.get('docx') or '(missing)'}")
            self._emit_log(f"pdf:  {artifacts.get('pdf') or '(missing)'}")
            self._emit_progress(100, "Готово")
            self._emit_finished(run.session_id)
        else:
            self._emit_progress(100, "Завершено з помилками")
            self._emit_error(
                run.stages[-1].name if run.stages else "stage5",
                f"Pipeline status={run.index.get('status')!r}",
            )
        return run.to_dict()


def run_in_thread(target: Any, transit_dir: Path | str) -> tuple[BridgePipelineAdapter, threading.Thread]:
    """Spawn the adapter in a daemon thread. Returns (adapter, thread)."""
    adapter = BridgePipelineAdapter(target)
    thread = threading.Thread(
        target=adapter.run,
        args=(transit_dir,),
        daemon=True,
    )
    return adapter, thread


__all__ = ["BridgePipelineAdapter", "run_in_thread"]
