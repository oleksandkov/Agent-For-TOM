"""app/backend/pipeline/types.py

Result and configuration data classes shared by all pipeline stages.

A stage returns a `StageResult`; the orchestrator inspects `status` /
`warnings` / `artifacts` to drive UI signals and write index.json.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


STAGE_OK = "ok"
STAGE_WARN = "warn"
STAGE_SKIP = "skip"
STAGE_FAIL = "fail"


@dataclass
class StageResult:
    """Outcome of one pipeline stage."""

    name: str
    status: str = STAGE_OK
    duration_ms: float = 0.0
    artifacts: dict[str, str] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def is_ok(self) -> bool:
        return self.status in (STAGE_OK, STAGE_WARN, STAGE_SKIP)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "artifacts": self.artifacts,
            "metrics": self.metrics,
            "warnings": list(self.warnings),
            "errors": list(self.errors),
        }


@dataclass
class PipelineContext:
    """Mutable state carried across stages for one session run."""

    session_id: str
    session_name: str
    template_id: str
    input_snapshot: dict[str, Any]
    transit_dir: Any  # Path, kept loose to avoid heavy imports here
    compact_dir: Any = None  # Path
    main_out_dir: Any = None  # Path
    image_gen_dir: Any = None  # Path
    output_dir: Any = None  # Path
    timings: dict[str, float] = field(default_factory=dict)
    artifacts: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    hf_token_missing: bool = False
    hf_token_warned: bool = False

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    def fail(self, msg: str) -> None:
        self.errors.append(msg)
